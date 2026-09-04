"""Klient Graph, który CZYTA normalnie, a każdy skutek na zewnątrz zamienia w wpis w logu.

Po co: pętla nasłuchu — interpretacja odpowiedzi, prośba o potwierdzenie, decyzja o zapisie —
jest jedyną ścieżką tej usługi, której NIE dawało się przećwiczyć przed wdrożeniem. ``DRY_RUN``
w listenerze to natychmiastowy ``return``, więc tryb próbny nie przechodził przez nią wcale,
a jednocześnie największe zmiany 0.2.7–0.2.11 leżą właśnie tam. Jedyną okazją do zobaczenia tej
warstwy w działaniu było uruchomienie jej na żywym tenancie — czyli na grafiku klienta.

Ten klient rozcina ten węzeł: odczyty idą do prawdziwego Graph i prawdziwego modelu, a trzy
metody niosące skutek (zapis zmiany, zapis czasu wolnego, wiadomość na czacie) nie wychodzą poza
proces. Dzięki temu ``--proba-nasluchu`` odpowiada na pytanie „czy to w ogóle działa u klienta",
nie płacąc za odpowiedź grafikiem ani wiadomością do pracownika.

Czego ten klient NIE załatwia — i dlatego ``cli`` dokłada do tego osobny plik stanu: przebieg
nasłuchu ZAPISUJE STAN (watermarki, statusy, pamięć rozmowy). Gdyby próba pisała po stanie
produkcyjnym, oznaczyłaby rozmowy jako obsłużone, a pracownik nigdy nie dostałby wiadomości,
o której stan twierdzi, że poszła. Bezpieczeństwo próby stoi więc na DWÓCH nogach: ten klient
odcina świat zewnętrzny, a kopia stanu odcina skutek dla przyszłych przebiegów.

Dlaczego podklasa, a nie osobna implementacja protokołu: ``GraphClient`` jest typem konkretnym
w sygnaturach całego obiegu, a odczytów jest kilkanaście. Podklasa dziedziczy je bez zmian, więc
próba idzie DOKŁADNIE tym samym kodem co produkcja — a to jest cały sens ćwiczenia.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from powiadomienia_teams.domain.czas import to_graph_iso
from powiadomienia_teams.domain.models import Shift, TimeOff
from powiadomienia_teams.graph.client import GraphClient

logger = logging.getLogger(__name__)

_UTC = timezone.utc
# Prefiks identyfikatorów zwracanych zamiast prawdziwych. Ma być rozpoznawalny w logu i w pliku
# stanu próby: gdyby taki identyfikator kiedykolwiek trafił do stanu produkcyjnego, ma krzyczeć,
# a nie udawać wpis z Shifts.
_PROBA = "proba-"


class GraphClientTylkoOdczyt(GraphClient):
    """``GraphClient`` z zablokowanymi trzema metodami niosącymi skutek poza procesem.

    Blokujemy dokładnie te, których skutku nie da się cofnąć albo ukryć przed człowiekiem:
    ``create_shift`` i ``create_time_off`` piszą do grafiku klienta nieodwracalnie,
    ``send_chat_message`` pisze do pracownika, a ``create_or_get_chat`` zakłada rozmowę.
    Wszystko inne — listy członków, odczyty grafiku, historia czatu, odświeżanie tokenu —
    działa bez zmian, bo to właśnie tego zachowania mamy się dowiedzieć.
    """

    def __init__(self, *args: Any, loguj_tresc: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Rejestr zablokowanych czynności — po próbie mówi, co WYDARZYŁOBY SIĘ naprawdę.
        # To jest właściwy wynik ćwiczenia: nie „przeszło bez wyjątku", tylko „oto co by zrobiło".
        self.zablokowane: list[tuple[str, str]] = []
        # Czy do logu wolno wpisać TREŚĆ wiadomości. Domyślnie nie — i to jest niezmiennik
        # prywatności, nie ustawienie wygody. Wiadomość bota niesie m.in. nazwę powodu
        # nieobecności z tenanta („Zwolnienie lekarskie"), czyli kategorię szczególną; wpisana
        # bezwarunkowo lądowała w logu kontenera, a przy `docker compose run` także w logach
        # demona. `docs/dane-osobowe.md` §3 opisuje regułę „pełna treść tylko przy
        # LOGUJ_NAZWISKA=true" — krok próby jest obowiązkowy przed `DRY_RUN=false`, więc dopóki
        # ta ścieżka jej nie przestrzegała, dokument mówił nieprawdę o systemie.
        self._loguj_tresc = loguj_tresc

    def _zablokuj(self, czynnosc: str, szczegol: str) -> None:
        self.zablokowane.append((czynnosc, szczegol))
        logger.info("[próba] POMIJAM %s — %s", czynnosc, szczegol)

    def _opis_wiadomosci(self, html: str) -> str:
        """Treść wiadomości albo sama jej MIARA — zależnie od ``loguj_tresc``.

        Miara wystarcza do tego, po co ten wiersz istnieje: operator sprawdza, że wiadomość
        POWSTAŁA i poszłaby na właściwy czat. Do przeczytania, CO w niej jest, służy tryb
        diagnostyczny — tak samo jak w ``nudge.run_once``.
        """
        if self._loguj_tresc:
            return _skrot(html)
        # Pełna nazwa zmiennej, nie skrót: operator kopiuje ją z logu wprost do pliku `env`,
        # a `LOGUJ_NAZWISKA` bez przedrostka nie istnieje (por. `env.example`).
        return (
            f"wiadomość gotowa ({len(html)} znaków; treść w logu tylko przy "
            "POWIADOMIENIA_LOGUJ_NAZWISKA=true)"
        )

    def create_or_get_chat(self, me_id: str, target_user_id: str) -> str:
        self._zablokuj("utworzenie czatu", f"z użytkownikiem {target_user_id}")
        return f"{_PROBA}czat-{target_user_id}"

    def send_chat_message(self, chat_id: str, html: str) -> str:
        # Zwracamy znacznik czasu, bo wołający zapisuje go jako watermark. Realny „teraz" jest tu
        # właściwy: próba ma zachowywać się jak wysyłka, która się udała — inaczej badalibyśmy
        # ścieżkę awaryjną zamiast tej, o którą chodzi.
        self._zablokuj("wiadomość na czacie", f"{chat_id}: {self._opis_wiadomosci(html)}")
        return to_graph_iso(datetime.now(_UTC))

    def create_shift(self, team_id: str, shift: Shift) -> str:
        self._zablokuj(
            "zapis zmiany do grafiku",
            f"{shift.user_id}: {shift.start.isoformat()} → {shift.end.isoformat()}",
        )
        return f"{_PROBA}zmiana"

    def create_time_off(self, team_id: str, time_off: TimeOff) -> str:
        self._zablokuj(
            "zapis czasu wolnego do grafiku",
            f"{time_off.user_id}: {time_off.start.isoformat()} → {time_off.end.isoformat()}",
        )
        return f"{_PROBA}wolne"


def _skrot(html: str, limit: int = 120) -> str:
    """Jedna linia bez znaczników — log próby ma być czytelny, nie kompletny."""
    tekst = " ".join(re.sub(r"<[^>]*>", " ", html).split())
    return tekst if len(tekst) <= limit else tekst[:limit] + "…"
