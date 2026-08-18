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
_SUFIKS_PROBY = ".probe"


class StateWriteError(RuntimeError):
    """Nie da się utrwalić stanu (brak miejsca, tylko-do-odczytu, brak praw).

    Wydzielony z ``OSError``, bo dla orkiestracji NIE jest to błąd przejściowy: ponowienie
    przebiegu przy pełnym dysku nie zapisze stanu, ale ZDĄŻY wysłać kolejne wiadomości, zanim
    znowu padnie na zapisie. Ten plik jest jedyną pamięcią „kogo już zagadnąłem", więc jego
    awaria musi zatrzymać wysyłkę, a nie ją zwielokrotnić (patrz ``app._run_once_with_retry``).
    """


# statusy obiegu
AWAITING_REPLY = "awaiting_reply"
AWAITING_CONFIRM = "awaiting_confirm"
APPLIED = "applied"
DECLINED = "declined"
EXPIRED = "expired"  # minęło okno odpowiedzi bez reakcji pracownika (koniec odpytywania)
SELF_FILLED = "self_filled"  # pracownik sam uzupełnił grafik w Shifts (koniec odpytywania)


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
    # Dni (0=pon…6=nd) już objęte urlopem w Graphie w chwili nudge'a. Przy zapisie pomijamy je,
    # by nie utworzyć DRUGIEGO timeOff, gdyby pracownik zgłosił je ponownie (create_time_off nie
    # deduplikuje). Pole opcjonalne — stare pliki stanu bez niego dostają pustą listę.
    known_time_off_weekdays: list[int] = field(default_factory=list)
    # Nieudane próby obsługi od ostatniego UDANEGO commitu (licznik zeruje wyłącznie `_commit`,
    # więc obejmuje też kolejne różne wiadomości, jeśli żadna nie doszła do końca).
    # Chroni przed zapętleniem na błędzie deterministycznym (patrz ``app._record_failure``).
    fail_count: int = 0
    # Ile cykli Z RZĘDU zakończyło się `ReadOutcome.UNKNOWN` (nie dało się ustalić, czy pracownik
    # odpisał). Zerowany przy każdym rozstrzygniętym odczycie. Bez tego licznika wpis, którego
    # czatu nie da się przeczytać, żył wiecznie i po cichu: `fail_count` nie rósł (awaria
    # `list_chat_messages` jest PRZED obsługą wiadomości), a `should_expire` słusznie odmawiał
    # wygaszenia bez dowodu.
    unknown_count: int = 0
    # Pamięć rozmowy: WYŁĄCZNIE wiadomości pracownika (nie bota), od najstarszej do najnowszej,
    # przycięta do ostatnich 10 (``replies.MEMORY_CAP``). Kontekst wieloturowy dla interpretera.
    # Pole opcjonalne — stare pliki stanu bez niego dostają pustą listę.
    employee_memory: list[str] = field(default_factory=list)
    # Kotwica STAŁEGO okna pamięci (``replies.MEMORY_WINDOW``): createdDateTime PIERWSZEJ wiadomości
    # w pamięci. Osobne pole (a nie ``employee_memory[0]``), by przycięcie do 10 NIE przesuwało okna
    # — inaczej okno stałoby się kroczące zamiast liczonym od pierwszej interakcji.
    memory_started_at: str = ""
    # Gotowy HTML wiadomości INICJOWANEJ przez bota, która czeka na dopuszczalne okno wysyłki
    # (`config.send_window_*`). Pusty napis = nic nie czeka. Status terminalny utrwalamy od razu,
    # więc odłożenie dotyczy WYŁĄCZNIE wysyłki — wpis nigdy nie wraca do obiegu przez porę doby.
    odlozona_wiadomosc: str = ""


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
    #
    # Pomijamy też pola z wartością ``null``: przekazane do konstruktora nadpisałyby domyślną
    # wartość (``field(default_factory=list)`` / ``""``) jawnym ``None``, a warstwa wyżej zakłada,
    # że ``employee_memory`` jest listą, a znaczniki czasu napisem. ``employee_memory=null`` (ręczna
    # edycja, dryf schematu) daje wtedy ``None`` zamiast ``[]``, na którym ``advance_memory`` rzuca
    # ``TypeError`` — i to w miejscu, gdzie ``_record_failure`` miał już tylko „odpuścić" tę
    # wiadomość, więc pending grzązłby w pętli ponowień. Odsianie ``None`` przywraca default,
    # spójnie z tolerancją wobec starych plików bez tych pól.
    result: dict[str, PendingReminder] = {}
    for key, value in raw.items():
        try:
            result[key] = PendingReminder(
                **{k: v for k, v in value.items() if k in _FIELDS and v is not None}
            )
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


def ensure_writable(path: Path) -> None:
    """Sprawdź, że stan DA SIĘ zapisać — wołane PRZED pierwszą nieodwracalną wysyłką w przebiegu.

    Kolejność „wyślij, potem utrwal" jest bezpieczna tylko wtedy, gdy „potem utrwal" naprawdę
    działa. Przy pełnym wolumenie (ENOSPC) albo wolumenie podmontowanym tylko-do-odczytu wysyłka
    się udaje, zapis pada, przebieg jest ponawiany — i ta sama osoba dostaje kolejną prośbę przy
    każdej próbie. Próbny zapis z `fsync` kosztuje jedną operację na przebieg i zamienia serię
    wiadomości do pracowników w jeden czytelny błąd przed wysłaniem czegokolwiek.

    To bramka, nie gwarancja: dysk może się zapełnić między próbą a właściwym zapisem. Dlatego
    ``save_state`` i tak sygnalizuje awarię osobnym wyjątkiem, którego orkiestracja nie ponawia.
    """
    proba = path.with_suffix(path.suffix + _SUFIKS_PROBY)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with proba.open("w", encoding="utf-8") as plik:
            plik.write("{}")
            plik.flush()
            os.fsync(plik.fileno())  # ENOSPC wychodzi dopiero tutaj, nie na `write`
        proba.unlink()
    except OSError as blad:
        raise StateWriteError(f"Nie da się zapisać stanu {path}: {blad}") from blad


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

    Awaria systemu plików wychodzi jako ``StateWriteError``, nie surowy ``OSError``: dla
    orkiestracji NIE jest to błąd przejściowy i nie wolno ponawiać na nim całego przebiegu
    (patrz ``app._run_once_with_retry``).
    """
    payload = {key: asdict(value) for key, value in state.items()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
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
    except OSError as blad:
        raise StateWriteError(f"Nie udało się utrwalić stanu {path}: {blad}") from blad
