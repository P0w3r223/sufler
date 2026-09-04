"""Kanał do CZŁOWIEKA-operatora: alerty i zakończenie procesu po awarii uwierzytelnienia.

Wydzielone, bo te dwie czynności są wołane ze wszystkich pozostałych modułów obiegu (przebieg,
nasłuch, pętla usługi, ścieżka startowa), a same nie potrzebują żadnego z nich. Bez osobnego
miejsca każdy podział ``app`` kończył się albo cyklem importów, albo duplikatem.

Reguła, która trzyma graf zależności acyklicznym: **ten moduł importuje wyłącznie ``config``
i ``alerts``**. W szczególności nie wie nic o pętli usługi, dlatego ``zglos_utrate_sesji``
przyjmuje ``sleep`` parametrem, zamiast sięgać po nie samodzielnie.

Nazwa pliku pokrywa się z modułem standardowej biblioteki ``operator``. To bezpieczne — Python 3
ma bezwzględne importy, więc ``import operator`` w dowolnym innym pliku nadal trafia do stdlib —
ale w TYM pliku stdlibowego ``operator`` nie da się zaimportować pod tą nazwą. Nie jest potrzebny.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from powiadomienia_teams import alerts
from powiadomienia_teams.config import Settings

logger = logging.getLogger(__name__)


def alert(settings: Settings, tytul: str, tresc: str, *, waga: str = alerts.BLAD) -> None:
    """Wyślij alert kanałem niezależnym od Graph (best-effort — patrz ``alerts.send_alert``).

    Niezależność jest tu całym sensem: najważniejszy alert powstaje w chwili, gdy token do Graph
    przestał działać, więc wysłanie go przez Teams tożsamością bota jest niemożliwe.

    Adres bierzemy z ``webhook_alertow``, nie z surowego pola: tam mieszka rozstrzygnięcie, że
    jawne wyłączenie alertów wygrywa z ustawionym URL-em.
    """
    alerts.send_alert(settings.webhook_alertow, tytul, tresc, waga=waga)


# Ile identyfikatorów wypisać wprost, zanim reszta zwinie się w liczbę. Alert startowy czyta się
# zwykle na telefonie, a przy dwudziestu pozycjach lista przestaje być czytelna dokładnie wtedy,
# gdy ma czemuś służyć — czyli przy sprawdzaniu, czy zgadza się z tym, co operator wpisywał.
# Skala nie znika (liczba idzie zawsze), ale identyfikatorów po dziesiątym już nie widać: przy
# dłuższej liście porównanie pozycja po pozycji trzeba zrobić w `env`, nie w alercie.
# Ten sam próg i to samo sformułowanie ogona co przy zawieszonych zapisach
# (`service._MAX_ZAWIESZONYCH_W_ALERCIE`) — operator ma się uczyć jednego języka, nie dwóch.
_MAX_ID_W_ALERCIE = 10


def opis_kregu_odbiorcow(settings: Settings) -> str:
    """Zdanie o tym, KTO może dostać NOWĄ prośbę — do alertu startowego.

    To jedyne miejsce, w którym człowiek widzi skutek zawężenia odbiorców **zanim** ktokolwiek
    dostanie wiadomość: bramka pilotażu sprawdza spójność dwóch zmiennych, ale nie powie, czy
    lista trafia w te osoby, o które chodziło. Literówka w identyfikatorze AAD przechodzi każdą
    walidację — to poprawny napis — a wychodzi dopiero w logu przebiegu, po piątkowej 16:00.

    **Zawężenie dotyczy wyłącznie nowych próśb** (`nudge.py` filtruje `missing`). Nasłuch bierze
    otwarte wpisy z pliku stanu bez tego filtra, więc rozmowa zaczęta przed zawężeniem toczy się
    dalej — razem z potwierdzeniem, zapisem do grafiku i domknięciem okna. Zdanie musi to mówić,
    bo inaczej alert obiecuje ciszę, której kod nie gwarantuje: wystarczy ustawić `ONLY_USER_IDS`
    po przebiegu, który poszedł do całego zespołu.

    Alert NIE mówi, ilu z tych identyfikatorów istnieje w zespole — to wymagałoby odczytu rosteru
    na starcie, czyli żądania do Graph w miejscu, które ma być best-effort. Osobna pozycja planu.

    Identyfikatory, nie nazwiska (N28): alert zostaje w kanale Teams bezterminowo i jest
    przeszukiwalny. Operator rozwiąże je `scripts/lista_czlonkow.py`.
    """
    if not settings.only_user_ids:
        # Tryb warunkowy, nie oznajmujący: ten sam alert idzie w trybie próbnym, w którym zdanie
        # „prośby pójdą do każdego" jest po prostu nieprawdziwe. Sprzeczność wewnątrz jednego
        # alertu uczy operatora, że tego kanału nie czyta się dosłownie — a cała ta pozycja stoi
        # na dosłowności.
        return (
            "Krąg odbiorców: CAŁY ZESPÓŁ — POWIADOMIENIA_ONLY_USER_IDS jest puste, więc przy "
            "pracy na serio prośby pójdą do każdego bez uzupełnionego grafiku."
        )
    widoczne = settings.only_user_ids[:_MAX_ID_W_ALERCIE]
    ogon = ""
    if len(settings.only_user_ids) > _MAX_ID_W_ALERCIE:
        ogon = f" i {len(settings.only_user_ids) - _MAX_ID_W_ALERCIE} więcej"
    flaga = (
        "POWIADOMIENIA_PILOTAZ=true"
        if settings.pilotaz
        else "POWIADOMIENIA_PILOTAZ nie jest włączone — lista i tak obowiązuje"
    )
    return (
        f"Krąg odbiorców ZAWĘŻONY ({flaga}). Na liście: {len(settings.only_user_ids)} "
        f"(identyfikatory znormalizowane — małe litery, bez klamr, więc mogą wyglądać inaczej "
        f"niż wpis w env) — "
        + ", ".join(widoczne)
        + ogon
        + ". Zawężenie dotyczy NOWYCH próśb; rozmowy otwarte w pliku stanu są dokańczane."
    )


def odczekaj_przed_wyjsciem(settings: Settings, sleep: Callable[[float], None]) -> None:
    """Odczekaj przed zakończeniem procesu — hamulec na pętlę restartów `unless-stopped`.

    Wspólne dla wszystkich zakończeń z powodu uwierzytelnienia. Bez tego opóźnienia Docker podnosi
    proces natychmiast, więc trwała przyczyna (cofnięta zgoda, zła konfiguracja, brak sieci)
    zamienia się w restart co sekundę zamiast w spokojne czekanie na interwencję człowieka.
    """
    if settings.auth_failure_exit_delay_s > 0:
        logger.info("Czekam %ds przed wyjściem (ogranicza pętlę restartów).",
                    settings.auth_failure_exit_delay_s)
        sleep(float(settings.auth_failure_exit_delay_s))


def tresc_publiczna(blad: Exception) -> str:
    """Treść wyjątku bezpieczna dla kanału POZA organizacją (patrz ADR 0006).

    Webhook alertów jest z założenia niezależny od Graph — najważniejszy alert powstaje wtedy, gdy
    token do Graph przestał działać. Ta niezależność znaczy jednak także, że kanał leży POZA
    granicą tożsamości organizacji: w tej instalacji jest nim Discord. Co tam trafi, opuszcza
    tenant, zostaje u osoby trzeciej bezterminowo i jest przeszukiwalne.

    Domyślnie zwracamy `str(blad)` BEZ ZMIAN. Hurtowe czyszczenie każdego alertu do zdania „coś
    padło, zajrzyj do logu" wymieniłoby wyciek na ciszę — a cisza jest tu gorsza: w instalacji bez
    monitoringu webhook jest jedynym kanałem, jaki operator ma, i alert bez treści szybko przestaje
    być czytany. Redakcja jest więc OPT-IN: wyjątek, który WIE, że niesie dane osobowe, deklaruje
    atrybut `publiczny`, a my go tu preferujemy.

    Wiedza siedzi po właściwej stronie: `operator` nie rozpozna adresu e-mail w `RuntimeError`,
    ale `graph.auth._jedyne_konto` wie dokładnie, co przed chwilą wkleił w komunikat.

    Log dostaje pełną treść zawsze — logi rotują i leżą na hoście, do którego operator i tak ma
    dostęp. Redakcja dotyczy KANAŁU, nie faktu.
    """
    return getattr(blad, "publiczny", "") or str(blad)


def zglos_utrate_sesji(
    settings: Settings, blad: Exception, sleep: Callable[[float], None]
) -> None:
    """Zgłoś utratę sesji i odczekaj, zanim proces się zakończy.

    Alert idzie webhookiem, NIE przez Teams: wiadomość na Teams wymaga tego samego tokenu, który
    właśnie przestał działać. Opóźnienie przed wyjściem jest konieczne, bo `restart: unless-stopped`
    podniósłby proces natychmiast — martwy token zamieniłby się w restart co sekundę zamiast
    w spokojne czekanie na `--login`, po którym usługa wraca sama.
    """
    logger.critical("Utracono uwierzytelnienie — zatrzymuję usługę. Zaloguj się: `--login`. (%s)",
                    blad)
    alert(settings, "Utracono uwierzytelnienie", tresc_publiczna(blad), waga=alerts.KRYTYCZNY)
    odczekaj_przed_wyjsciem(settings, sleep)
