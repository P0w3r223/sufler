"""Jak runtime NAZYWA pracownika, gdy mówi do operatora — jedno miejsce na całą usługę.

Alerty lądują na webhooku, który zwykle jest kanałem Teams albo zewnętrznym zbieraczem zdarzeń:
treść zostaje tam bezterminowo i jest przeszukiwalna. Logi kontenera podlegają ROTACJI, a nie
polityce retencji. Ani jedno, ani drugie nie jest miejscem na imię i nazwisko pracownika, skoro
identyfikator wystarcza operatorowi do zrobienia tego samego — `scripts/lista_czlonkow.py`
rozwiązuje go na nazwisko w jednym poleceniu, na żądanie i bez zostawiania śladu.

Do PRACOWNIKA nadal piszemy po imieniu (`messages.py` buduje treść z obiektu rosteru, nie stąd),
a podsumowanie tygodniowe operuje wyłącznie liczbami — nazwisko nigdy nie było mu potrzebne.

Funkcja jest jedna i przyjmuje ``Settings``, żeby przełącznik miał dokładnie jedno miejsce
egzekwowania. Rozproszenie warunku `if settings.loguj_nazwiska` po kilkudziesięciu wywołaniach
`logger.*` skończyłoby się tym, czym kończy się zawsze: nowe miejsce logowania powstaje bez
warunku i nikt tego nie zauważa, dopóki nazwisko nie wypłynie na webhook.
"""

from __future__ import annotations

from powiadomienia_teams import state as st
from powiadomienia_teams.config import Settings
from powiadomienia_teams.domain.models import Member

# Wpis stanu bez identyfikatora (dryf schematu — `--stan` liczy się z takim plikiem) dałby
# w alercie wiersz zaczynający się od myślnika i niczego więcej. Lepiej powiedzieć wprost, że
# tożsamości nie ma, niż wypisać pustkę, którą czytający weźmie za usterkę formatowania.
_BEZ_IDENTYFIKATORA = "(wpis bez identyfikatora)"


def osoba(pending: st.PendingReminder, settings: Settings) -> str:
    """Jak nazwać osobę z otwartego przypomnienia w logu albo w alercie."""
    return _etykieta(pending.member_id, pending.member_name, settings)


def czlonek(member: Member, settings: Settings) -> str:
    """To samo dla osoby z rosteru — przebieg tygodniowy nie ma jeszcze wpisu w stanie.

    Bierze CAŁY obiekt, nie dwa napisy obok siebie. Sygnatura `(user_id, display_name)` wygląda
    niewinnie, ale wywołanie z zamienionymi argumentami zwracałoby nazwisko przy wyłączonym
    trybie — i nie zauważyłby tego ani strażnik statyczny, ani żaden test. Tak zamiana jest
    nie do zapisania.
    """
    return _etykieta(member.user_id, member.display_name, settings)


def _etykieta(identyfikator: str, nazwisko: str, settings: Settings) -> str:
    if not settings.loguj_nazwiska:
        return identyfikator or _BEZ_IDENTYFIKATORA
    # Tryb diagnostyczny daje nazwisko RAZEM z identyfikatorem, nie zamiast: wszystko, co czyta
    # te logi po identyfikatorze (grep operatora, korelacja z `--stan`), ma działać tak samo.
    if not nazwisko:
        return identyfikator or _BEZ_IDENTYFIKATORA
    return f"{nazwisko} ({identyfikator or _BEZ_IDENTYFIKATORA})"
