"""Trwały stan otwartych przypomnień (JSON) — idempotencja i dwukierunkowy obieg.

To NIE sekret (dane operacyjne), trzymane obok stanu drzwi teams_graph. `resolved` to lista
interwałów (weekday + HH:MM) ustalonego grafiku — tz-agnostyczna, odtwarzana przy zapisie.

Zapis jest atomowy (temp + os.replace), a odczyt tolerancyjny (ignoruje nieznane pola,
uszkodzony plik → pusty stan) — bo ten plik chroni przed podwójnym zapisem zmian.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SUFIKS_KOPII = ".bak"

# statusy obiegu
AWAITING_REPLY = "awaiting_reply"
AWAITING_CONFIRM = "awaiting_confirm"
APPLIED = "applied"
DECLINED = "declined"
EXPIRED = "expired"  # minęło okno odpowiedzi bez reakcji pracownika (koniec odpytywania)


@dataclass
class PendingReminder:
    member_id: str
    member_name: str
    chat_id: str
    week_start: str  # ISO date (poniedziałek przyszłego tygodnia)
    status: str
    watermark: str = ""  # createdDateTime ostatniej przetworzonej wiadomości pracownika
    nudged_at: str = ""  # createdDateTime nudge'a (niezmienny; baza okna odpowiedzi)
    proposal: list[dict[str, Any]] = field(default_factory=list)  # gotowiec z zeszłego tygodnia
    resolved: list[dict[str, Any]] = field(default_factory=list)  # grafik ustalony po odpowiedzi
    # Czas wolny ustalony po odpowiedzi: [{weekday, reason_id, reason_name}] (powód rozstrzygnięty).
    resolved_time_off: list[dict[str, Any]] = field(default_factory=list)
    # Nieudane próby obsłużenia BIEŻĄCEJ wiadomości; zerowany przy każdym udanym commicie.
    # Chroni przed zapętleniem na błędzie deterministycznym (patrz ``app._record_failure``).
    fail_count: int = 0


_FIELDS = {f.name for f in fields(PendingReminder)}


def _wczytaj(path: Path) -> dict[str, PendingReminder] | None:
    """Odczytaj jeden plik stanu. ``None`` = nie da się użyć (brak, uszkodzony, zły kształt)."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Uszkodzony plik stanu %s", path)
        return None
    if not isinstance(raw, dict):
        logger.warning("Plik stanu %s nie jest obiektem", path)
        return None
    # Ignoruj nieznane pola (dryf schematu) i POMIJAJ pojedyncze nieczytelne wpisy zamiast kłaść
    # cały nasłuch — zgodnie z deklarowaną tolerancyjnością odczytu.
    result: dict[str, PendingReminder] = {}
    for key, value in raw.items():
        try:
            result[key] = PendingReminder(**{k: v for k, v in value.items() if k in _FIELDS})
        except (TypeError, AttributeError):
            logger.warning("Pomijam nieczytelny wpis stanu %r w %s", key, path)
    return result


def load_state(path: Path) -> dict[str, PendingReminder]:
    """Wczytaj stan; przy nieużywalnym pliku SPRÓBUJ KOPII, dopiero potem startuj z pustego.

    Sięganie po kopię nie jest ozdobnikiem: pusty stan oznacza, że najbliższy przebieg uzna
    wszystkich za nienagabywanych i wyśle prośby DRUGI RAZ — idempotencja opiera się wyłącznie
    na tym pliku. Lepiej odtworzyć stan sprzed jednego zapisu (najwyżej powtórzymy obsługę jednej
    odpowiedzi, co jest bezpieczne) niż zacząć od zera.
    """
    stan = _wczytaj(path)
    if stan is not None:
        return stan
    kopia = path.with_suffix(path.suffix + _SUFIKS_KOPII)
    stan = _wczytaj(kopia)
    if stan is not None:
        logger.warning("Odtworzono stan z kopii %s (%d wpisów)", kopia, len(stan))
        return stan
    return {}


def save_state(path: Path, state: dict[str, PendingReminder]) -> None:
    """Zapisz stan atomowo, z `fsync` i kopią poprzedniej wersji.

    Ten plik jest JEDYNĄ ochroną przed wysłaniem próśb drugi raz do tych samych osób, więc jego
    utrata jest widoczna dla pracowników. `os.replace` chroni przed uciętym plikiem, ale sam nie
    wystarcza: bez `fsync` dane mogą siedzieć w buforze systemu, a nagła utrata zasilania zostawia
    plik pusty mimo udanej podmiany.

    Kopia powstaje przez KOPIOWANIE, nie przeniesienie. Wcześniejsza wersja robiła tu
    `os.replace(path, path.bak)` przed `os.replace(tmp, path)` — czyli dwa przeniesienia pod rząd,
    a MIĘDZY NIMI plik stanu nie istniał. Proces ubity w tym oknie kasował stan całkowicie,
    mimo że dane leżały w kopii. Zapis na `path` musi pozostać JEDNĄ operacją.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: asdict(value) for key, value in state.items()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as plik:
        json.dump(payload, plik, ensure_ascii=False, indent=2)
        plik.flush()
        os.fsync(plik.fileno())  # dane NA DYSKU, nie tylko w buforze systemu
    if path.exists():
        # Best-effort: brak kopii jest lepszy niż zablokowany zapis stanu, bo bez zapisu
        # grozi podwójna wysyłka. Oryginał zostaje na miejscu do samej podmiany.
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + _SUFIKS_KOPII))
        except OSError:
            logger.warning("Nie udało się utworzyć kopii stanu %s", path)
    os.replace(tmp, path)  # JEDYNA operacja na `path` — brak okna bez pliku stanu
