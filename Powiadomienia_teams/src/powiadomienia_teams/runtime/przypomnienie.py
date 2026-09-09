"""Jedno przypomnienie dla pracownika, który po prośbie nie napisał ani słowa (pozycja D5 planu).

Osobny moduł, a nie kolejna funkcja w ``runtime.listener``: tamten ma już 1545 linii i siedem
zlepionych odpowiedzialności, a ta ścieżka jest samodzielna — ma własny predykat (czysty,
w ``reminders.lifecycle``), własną treść (``messages``) i własny szew wysyłki. ``listener``
dostaje z tego jedno wywołanie w kroku 1.7.

**Po co to w ogóle jest.** Pomiar z dwóch tygodni pilotażu: 10 próśb, 4 domknięte skutkiem,
6 wygasłych bez odpowiedzi. Bot pytał dokładnie RAZ — w piątek o 16:00 — i milczał do
poniedziałku 05:00. Ta pozycja dokłada jedno zagadnięcie w sobotę rano.

**Czego ta ścieżka NIE robi.** Nie tworzy nowego wpisu (N15: jedna prośba na osobę na tydzień —
przypomnienie jest wiadomością W ISTNIEJĄCYM temacie, nie drugą prośbą), nie zmienia statusu,
nie dotyka grafiku i nie przesuwa terminu odpowiedzi. To ostatnie jest niezmiennikiem
wymuszonym przez ``lifecycle.czas_na_przypomnienie``, nie skutkiem szczęśliwej konfiguracji.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from powiadomienia_teams import state as st
from powiadomienia_teams.config import OknoCiszy, Settings
from powiadomienia_teams.domain.czas import to_graph_iso
from powiadomienia_teams.graph.client import GraphClient
from powiadomienia_teams.messages import build_przypomnienie_text, to_html
from powiadomienia_teams.reminders.lifecycle import termin_odpowiedzi
from powiadomienia_teams.runtime import etykiety
from powiadomienia_teams.runtime.domkniecia import do_domkniecia
from powiadomienia_teams.runtime.wysylka import NIE_POLYKAJ, do_pracownika

logger = logging.getLogger(__name__)


def przypomnij_milczacym(
    settings: Settings,
    client: GraphClient,
    state: dict[str, st.PendingReminder],
    kandydaci: list[st.PendingReminder],
    tz: ZoneInfo,
    teraz: datetime,
    *,
    okno_ciszy: OknoCiszy,
) -> None:
    """Wyślij po JEDNYM przypomnieniu; oznacz PRZED wysyłką („co najwyżej raz").

    ``okno_ciszy`` to okno PIERWOTNE, a bramką jest ``domkniecia.do_domkniecia`` — ta sama, co dla
    domknięć, i z tego samego powodu, mimo że przypomnienie niczego nie domyka. Jej docstring
    zapowiada tę ścieżkę wprost („pozycja D5 dokłada kolejną ścieżkę piszącą do milczących"):
    reguła brzmi „wiadomość do kogoś, kto w tej rozmowie NIC nie napisał, podlega ciszy nawet
    wtedy, gdy wołający ją świadomie pominął". ``--poll-once`` wyłącza ciszę po to, żeby
    ODPOWIEDZIEĆ tym, którzy właśnie napisali — a nie żeby zagadnąć o piątej rano milczących.

    **Znacznik PRZED wysyłką**, odwrotnie niż w ``listener._oznacz_wyslane``. Tamta kolejność
    broni się tym, że nie wolno liczyć kurtuazji od wiadomości, której pracownik nigdy nie dostał;
    tutaj to ryzyko nie istnieje, bo ``czas_na_przypomnienie`` wysyła wyłącznie wtedy, gdy
    kurtuazja i tak zmieści się pod terminem kalendarzowym — podniesienie dolnej granicy jest więc
    w każdym przypadku bez skutku. Zostaje sam wybór między „pominięte" a „zdublowane", a ten
    projekt rozstrzyga go tak samo wszędzie: proces ubity między zapisem a wysyłką ma kosztować
    JEDNO nieprzysłane przypomnienie, nie dwa przysłane. Wiadomości nie da się cofnąć.

    ``bot_last_message_at`` ustawiamy razem ze znacznikiem, bo przypomnienie JEST prośbą bota
    i kurtuazja („nie zamykaj tematu zaraz po tym, jak o coś poprosiłeś") ma je obejmować.
    """
    wolno = do_domkniecia(kandydaci, teraz, okno_ciszy, "przypomnienia")
    for pending in wolno:
        # Termin liczy ten sam kod, który go egzekwuje (B7). Bierzemy go PRZED oznaczeniem —
        # po nim `bot_last_message_at` podniósłby dolną granicę kurtuazji i treść obiecywałaby
        # godzinę inną niż ta, którą pracownik dostał w pierwszej wiadomości.
        termin = termin_odpowiedzi(pending, settings.okno_odpowiedzi)
        tekst = build_przypomnienie_text(
            _etykieta_tygodnia(pending.week_start),
            termin,
            tz,
            ma_propozycje=bool(pending.proposal),
        )
        znacznik = to_graph_iso(teraz)
        pending.przypomniano_at = znacznik
        pending.bot_last_message_at = znacznik
        st.save_state(settings.state_path, state)
        try:
            do_pracownika(settings, client, pending.chat_id, to_html(tekst), teraz=teraz)
        # Utrata sesji i wysyłka z pominiętą bramką ciszy propagują — patrz `wysylka.NIE_POLYKAJ`.
        except NIE_POLYKAJ:
            raise
        except Exception:
            # Znacznik został — i to jest właściwy kierunek. Ponowienie w kolejnym obiegu
            # znaczyłoby, że trwała awaria czatu zamienia „jedno przypomnienie" w serię prób
            # rozłożoną na cały weekend, a o awarii czatu operator dowiaduje się skądinąd
            # (`_dogladaj_nierozstrzygniete`, ADR 0007).
            logger.exception(
                "Nie udało się wysłać przypomnienia do %s", etykiety.osoba(pending, settings)
            )
            continue
        logger.info("Przypomniano %s o grafiku", etykiety.osoba(pending, settings))


def _etykieta_tygodnia(week_start: str) -> str:
    """„08.09–12.09" z ISO-daty poniedziałku; przy nieczytelnej dacie sama data.

    Ten sam kształt co etykieta z ``runtime.nudge`` (pon–pt, bo o weekend nie pytamy), ale liczony
    tutaj: przypomnienie ma tylko ``week_start`` z pliku stanu, a nie okno przebiegu.
    """
    try:
        poniedzialek = date.fromisoformat(week_start)
    except (ValueError, TypeError):
        return week_start
    return f"{poniedzialek:%d.%m}–{poniedzialek + timedelta(days=4):%d.%m}"
