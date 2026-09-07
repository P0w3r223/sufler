"""Pętla usługi: KIEDY uruchomić przebieg i jak dotrwać do następnego terminu.

Terminarz, nadrabianie zaległego przebiegu w oknie łaski, adaptacyjny odstęp nasłuchu, puls sesji
i cotygodniowe podsumowanie dla administratora. Ten moduł nie zna treści wiadomości ani sposobu
interpretacji odpowiedzi — od tego są ``runtime.nudge`` i ``runtime.listener``.

Podsumowanie jest tu „dead man's switchem": w instalacji bez monitoringu brak wiadomości w piątek
wieczorem jest jedynym sygnałem awarii, dlatego jest nierozłączne z przebiegiem.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from powiadomienia_teams import alerts
from powiadomienia_teams import state as st
from powiadomienia_teams.agent.interpreter import LlmClient, LlmNiedostepnyError
from powiadomienia_teams.config import Settings
from powiadomienia_teams.graph.auth import AuthExpiredError
from powiadomienia_teams.graph.client import (
    GraphClient,
    GraphPermissionError,
    GraphTruncatedReadError,
)
from powiadomienia_teams.healthcheck import odswiez_puls
from powiadomienia_teams.messages import LiczbyTygodnia, build_summary_text, to_html
from powiadomienia_teams.runtime import etykiety, operator
from powiadomienia_teams.runtime.budzet import BudzetPrzebiegu, PrzebiegPrzekroczylCzasError
from powiadomienia_teams.runtime.cisza import (
    CiszaWstrzymalaPrzebieg,
    cisza_pomiedzy,
    najblizsza_dozwolona,
    wolno_pisac,
)
from powiadomienia_teams.runtime.listener import PollOutcome, poll_replies
from powiadomienia_teams.runtime.nudge import run_once
from powiadomienia_teams.runtime.pamiec_grafiku import PamiecSamouzupelnien
from powiadomienia_teams.runtime.wysylka import do_administratora
from powiadomienia_teams.scheduler.backoff import next_poll_delay
from powiadomienia_teams.scheduler.weekly import next_run, previous_run

logger = logging.getLogger(__name__)
_UTC = timezone.utc


def _poll_delay(settings: Settings, outcome: PollOutcome | None, now: datetime) -> float:
    """Odstęp do następnego odpytania: błąd → bazowy (spróbuj wkrótce); brak otwartych → limit;
    otwarte → adaptacyjny backoff od ostatniej aktywności (cisza wydłuża, odpowiedź skraca).

    W GODZINACH CISZY odstęp jest przycięty do jej końca — pętla ma wrócić do pracy dokładnie po
    ciszy, nie do godziny później (`poll_max_interval_s`). Ten sufit wygląda na redundantny wobec
    bramki w `run_forever` (ta w ciszy w ogóle nie wchodzi w obieg nasłuchu), ale **nie jest**:
    `poll_replies` zwraca w ciszy `PollOutcome(0, None)`, czyli DOKŁADNIE to samo, co przy braku
    otwartych spraw. Gdyby sufit ciszy przesunąć poniżej warunku `open_count == 0`, odłożony obieg
    dostawałby odstęp „nic otwartego", czyli do godziny — i odpowiedź napisana tuż po ciszy czekałaby
    bez powodu. Kolejność warunków JEST tu więc regułą, nie stylem; osobnej reprezentacji odmowy po
    stronie nasłuchu świadomie nie wprowadzamy (dwa wołające miejsca, jeden konsument), ale niech to
    zdanie stoi tu zamiast słowa „redundantny".
    """
    do_konca_ciszy = (najblizsza_dozwolona(now, settings.okno_ciszy) - now).total_seconds()
    if do_konca_ciszy > 0:
        return max(float(settings.poll_interval_s), do_konca_ciszy)
    if outcome is None:
        return float(settings.poll_interval_s)  # transientny błąd — ponów wkrótce
    if outcome.open_count == 0:
        return float(settings.poll_max_interval_s)  # nic otwartego → rzadkie sprawdzanie
    return next_poll_delay(
        now=now,
        last_activity=outcome.last_activity or now,
        base_s=float(settings.poll_interval_s),
        max_s=float(settings.poll_max_interval_s),
    )


_RUN_RETRY_ATTEMPTS = 3
_RUN_RETRY_BACKOFF_S = 30


def _run_once_with_retry(
    settings: Settings,
    client: GraphClient,
    *,
    now: datetime,
    teraz: datetime,
    attempts: int = _RUN_RETRY_ATTEMPTS,
    backoff_s: int = _RUN_RETRY_BACKOFF_S,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Uruchom ``run_once``, ponawiając transientne błędy z narastającym backoffem, zanim odpuścisz.

    Utrata tokenu (``AuthExpiredError``), brak uprawnień (``GraphPermissionError``), awaria zapisu
    stanu (``StateWriteError``) i ucięte stronicowanie (``GraphTruncatedReadError``) nie są
    transientne — propagują od razu, bo kolejna próba nie naprawi cofniętej zgody, utraconej roli,
    pełnego dysku ani kolekcji, która jest po prostu za duża.

    ``StateWriteError`` jest szczególnie ważny: idempotencja ``run_once`` opiera się na ZAPISANYM
    stanie, więc ponawianie przebiegu, w którym zapis nie działa, wysyła tę samą prośbę tyle razy,
    ile jest prób.

    ``GraphTruncatedReadError`` dołączył tu z powodu KOSZTU, nie poprawności: kolekcja zespołu
    rośnie i nigdy nie maleje, więc odczyt ucięty na limicie stron będzie ucięty także za trzydzieści
    sekund. Każda próba to ``_MAX_PAGES`` pełnych żądań, a przebieg jest jeszcze ponawiany w oknie
    łaski — bez tej klasyfikacji trwałe przekroczenie sufitu zamieniało się w dziesiątki pełnych
    odczytów, czyli usługa zaczynała dławić Graph dokładnie wtedy, gdy już sobie z nim nie radzi.
    Zewnętrzna pętla nadal ponawia przebieg w oknie łaski: to świadome, bo administrator może
    w tym czasie posprzątać kolekcję i wtedy tydzień jeszcze się uratuje.

    ``PrzebiegPrzekroczylCzasError`` dołączył z tego samego powodu co ucięte stronicowanie: skoro
    przebieg nie zmieścił się w limicie, powtórzenie go OD ZERA tym bardziej się nie zmieści —
    a każda próba to kolejne pełne odczyty tej samej kolekcji, czyli usługa dokładałaby obciążenia
    dokładnie wtedy, gdy Graph już jej nie nadąża.
    """
    for attempt in range(1, attempts + 1):
        # Puls PRZED każdą próbą: przebieg z ponowieniami i dławieniem Graph potrafi trwać minuty,
        # a bez odświeżenia healthcheck zgłosiłby „niezdrowy" dla usługi, która właśnie pracuje.
        odswiez_puls(settings)
        try:
            run_once(settings, client, now=now, teraz=teraz)
            return
        except (
            AuthExpiredError,
            CiszaWstrzymalaPrzebieg,
            GraphPermissionError,
            GraphTruncatedReadError,
            PrzebiegPrzekroczylCzasError,
            st.StateWriteError,
        ):
            # `CiszaWstrzymalaPrzebieg` jest tu z innego powodu niż reszta: to nie awaria, tylko
            # odmowa z reguły, a ponawianie jej przez trzy próby z backoffem (30 s + 60 s) niczego
            # nie zmieni — okno ciszy trwa godzinami. Propaguje do `_safe_run_once`, gdzie termin
            # zostaje ZALEGŁY.
            raise
        except Exception:
            if attempt >= attempts:
                raise
            wait = backoff_s * attempt
            logger.warning(
                "Przebieg powiadomień nieudany (próba %d/%d) — ponawiam za %ds",
                attempt,
                attempts,
                wait,
            )
            sleep(wait)


def _kolejny_termin(settings: Settings, now: datetime) -> datetime:
    """Najbliższy zaplanowany termin przebiegu (opakowanie na ``next_run`` z ustawieniami)."""
    return next_run(
        now,
        tz=settings.tz,
        weekday=settings.run_weekday,
        hour=settings.run_hour,
        minute=settings.run_minute,
    )


_PULS_PONOWIENIE_S = 900  # odstęp między próbami po nieudanym pulsie (15 min)
_PULS_PROG_ALERTU = 3  # po tylu nieudanych próbach z rzędu powiadamiamy operatora


@dataclass
class StanPulsu:
    """Kiedy wypada następna próba pulsu i ile z rzędu się nie powiodło.

    Semantyka „kiedy następna", a nie „kiedy ostatnia udana", jest tu celowa: po nieudanej próbie
    musimy odsunąć kolejną o własny odstęp, niezależny od tempa pobudek pętli. Pobudki potrafią
    następować co 10 s (otwarta rozmowa), więc przy znaczniku „ostatnia udana" trwała awaria sieci
    dawała próbę i alert PRZY KAŻDEJ POBUDCE — do kilkuset alertów na godzinę, co zatyka jedyny
    kanał niezależny od AAD dokładnie wtedy, gdy jest najbardziej potrzebny.
    """

    nastepny: datetime
    nieudane: int = 0


def _puls_sesji(
    settings: Settings, client: GraphClient, stan: StanPulsu, *, teraz: datetime
) -> StanPulsu:
    """Sprawdź ważność sesji nie częściej niż co ``heartbeat_interval_h``; zwróć nowy stan pulsu.

    Bez pulsu utrata sesji w poniedziałek wychodzi dopiero w piątek o 16:00 — czyli w chwili, gdy
    przebieg miał się odbyć i nikt nie zdąży już zareagować. Puls kosztuje jedno ciche odświeżenie
    MSAL na dobę i NIE odpytuje Graph.

    Alert idzie DOKŁADNIE RAZ, po ``_PULS_PROG_ALERTU`` nieudanych próbach z rzędu — pojedyncze
    mrugnięcie sieci nie zasługuje na alarm, a trwała awaria nie ma prawa go powtarzać. Powrót
    sprawności też jest zgłaszany, żeby operator wiedział, że nie musi już nic robić.
    Utrata sesji propaguje wyżej: to nie jest błąd przejściowy.

    ``teraz`` przychodzi z pętli, a nie z zegara systemowego — puls i odstęp nasłuchu MUSZĄ mierzyć
    czas tym samym zegarem, inaczej w teście „minęła doba" i „minęła sekunda" dają ten sam wynik.
    """
    if teraz < stan.nastepny:
        return stan
    try:
        client.refresh_auth()
    except AuthExpiredError:
        raise
    except Exception as blad:
        nieudane = stan.nieudane + 1
        logger.warning("Puls sesji nie powiódł się (%d. raz z rzędu): %s", nieudane, blad)
        if nieudane == _PULS_PROG_ALERTU:
            operator.alert(
                settings,
                "Puls sesji nie powiódł się",
                f"{nieudane} nieudane próby z rzędu. Ostatni błąd: {blad}",
            )
        return StanPulsu(teraz + timedelta(seconds=_PULS_PONOWIENIE_S), nieudane)
    if stan.nieudane >= _PULS_PROG_ALERTU:
        operator.alert(
            settings, "Puls sesji wrócił", "Uwierzytelnienie znów działa.", waga=alerts.INFO
        )
    logger.info("Puls sesji: uwierzytelnienie nadal ważne.")
    return StanPulsu(teraz + timedelta(hours=settings.heartbeat_interval_h), 0)


def _puls_z_obsluga_utraty(
    settings: Settings,
    client: GraphClient,
    stan: StanPulsu,
    sleep: Callable[[float], None],
    *,
    teraz: datetime,
) -> StanPulsu:
    """``_puls_sesji`` razem z reakcją na utratę sesji — jedno miejsce dla obu wołających.

    Puls bije w DWÓCH miejscach pętli (obieg nasłuchu i gałąź godzin ciszy), a utrata tokenu ma
    w obu znaczyć to samo: zgłoś i zatrzymaj się czysto. Wydzielone, bo skopiowana obsługa
    wyjątku rozjeżdża się przy pierwszej zmianie, a rozjazd znaczyłby, że w jednej z gałęzi
    wygaśnięcie sesji przechodzi bezgłośnie.
    """
    try:
        return _puls_sesji(settings, client, stan, teraz=teraz)
    except AuthExpiredError as blad:
        # Utrata tokenu to NIE transientny błąd — zatrzymaj się czysto po zgłoszeniu alertu;
        # `unless-stopped` podniesie usługę, gdy człowiek wykona `--login`.
        operator.zglos_utrate_sesji(settings, blad, sleep)
        raise


def _powitanie(settings: Settings, termin: datetime, *, wyslane: bool) -> bool:
    """Alert startowy — dokładnie raz na sesję. Zwraca nową wartość znacznika.

    Potwierdzenie powrotu po reboocie hosta — bez tego restart usługi jest niewidoczny.

    Tryb próbny MUSI być widoczny w TYM alercie, bo to jedyny kanał, który operator obserwuje
    przed pierwszym uruchomieniem. W ``dry_run`` nasłuch kończy się natychmiast
    (``listener.poll_replies``), przebieg tylko loguje, a podsumowanie nie wychodzi do
    administratora — czyli instalacja z przeoczoną linią w ``env`` (a ``DRY_RUN=true`` jest
    DOMYŚLNE i takie stoi w ``env.example``) wyglądałaby stąd identycznie jak działająca.
    Zdanie „Nasłuch aktywny" było w tym przypadku po prostu nieprawdziwe.

    Krąg odbiorców jest w OBU gałęziach, bo sekwencja wdrożenia każe przećwiczyć pilotaż najpierw
    w trybie próbnym — gdyby alert próbny milczał o odbiorcach, próba nie sprawdzałaby dokładnie
    tego, co ma ochronić przy przełączeniu na serio.

    **Wydzielone z pętli, bo powitanie musi wyjść także w godzinach ciszy.** Dopóki stało pod
    bramką ciszy, usługa wstająca po 20:00 milczała do 07:00 — a to jest wprost sprzeczne
    z kontraktem „alerty operatorskie idą zawsze, dotyczą stanu USŁUGI" i kosztuje dwie rzeczy
    opisane w dokumentach: kontrolę ``ONLY_USER_IDS`` na alercie startowym
    (``deploy/README-docker.md``) oraz jedyny ślad samoleczenia po restarcie
    (``deploy/README-serwer.md``).
    Nocne wdrożenie i nocny restart to przypadki typowe, nie skrajne.
    """
    if wyslane:
        return True
    krag = operator.opis_kregu_odbiorcow(settings)
    # Także do logu, nie tylko na webhook: `validate()` dopuszcza produkcję bez webhooka przy
    # jawnym `ALERTY_WYLACZONE=true`, a w takiej instalacji log kontenera jest jedynym kanałem
    # operatora — zdanie o kręgu odbiorców przepadałoby w całości.
    logger.info("%s", krag)
    if settings.dry_run:
        operator.alert(
            settings,
            "Usługa wystartowała w TRYBIE PRÓBNYM",
            "Nic nie zostanie wysłane ani zapisane, a odpowiedzi pracowników NIE są "
            "czytane. Aby pracować naprawdę, ustaw POWIADOMIENIA_DRY_RUN=false. "
            f"{krag} "
            f"Najbliższy przebieg (tylko wpis w logu): {termin.isoformat()}",
            waga=alerts.INFO,
        )
    else:
        operator.alert(
            settings,
            "Usługa wystartowała",
            f"Nasłuch aktywny. {krag} Najbliższy przebieg: {termin.isoformat()}",
            waga=alerts.INFO,
        )
    return True


def _catchup_due(settings: Settings, now: datetime) -> datetime | None:
    """Czas MINIONEGO terminu, jeśli minął niedawno (w oknie łaski) — inaczej ``None``.

    Sygnał do nadrobienia zaległego przebiegu (np. po awarii/restarcie tuż po terminie), zamiast
    czekania cały tydzień. NIE sprawdzamy tu, czy przebieg „już był" — nadrobienie polega na
    IDEMPOTENCJI ``run_once`` (pomija osoby z otwartym pendingiem na ten tydzień), więc powtórka
    jest bezpieczna. Zwracamy czas TERMINU (nie „teraz"), by ``run_once`` liczył tydzień docelowy od
    terminu — restart po północy nie przesunie tygodnia o 7 dni. ``catchup_grace_hours=0`` wyłącza.

    **Godziny ciszy NIE zjadają okna łaski.** Czas ciszy minięty między terminem a teraz jest
    odliczany, bo w ciszy nadrobienie i tak nie może się wykonać (``run_once`` odmawia). Bez tego
    usługa wstająca po awarii w piątek o 21:00 miałaby nadrobić przebieg do 22:00, przez cały ten
    czas nie wolno by jej było pisać — i CAŁY ZESPÓŁ nie dostałby w tym tygodniu prośby o grafik,
    bezgłośnie. Cisza ma nadrabianie PRZESUWAĆ, a nie unieważniać (`plan-rozwoju.md` B5).
    """
    if settings.catchup_grace_hours <= 0:
        return None
    prev = previous_run(
        now,
        tz=settings.tz,
        weekday=settings.run_weekday,
        hour=settings.run_hour,
        minute=settings.run_minute,
    )
    if now < prev:
        return None
    zuzyte = (now - prev) - cisza_pomiedzy(prev, now, settings.okno_ciszy)
    if zuzyte <= timedelta(hours=settings.catchup_grace_hours):
        return prev
    return None


_PULS_CO_S = 60.0  # jak często odświeżamy plik pulsu w trakcie czekania
_PONOWIENIE_PRZEBIEGU_S = 1800  # po nieudanym przebiegu wróć po 30 min, o ile trwa okno łaski
# Po tylu obiegach nasłuchu Z RZĘDU przerwanych limitem czasu powiadamiamy operatora. Próg, a nie
# alert za każdym razem, z tego samego powodu co w `_puls_sesji`: pobudki potrafią następować co
# 10 s, więc trwała przyczyna zatkałaby jedyny kanał niezależny od AAD setkami wiadomości.
_PROG_ALERTU_PRZEKROCZEN = 3


def spij_z_pulsem(settings: Settings, sekundy: float, sleep: Callable[[float], None]) -> None:
    """Śpij podaną liczbę sekund, odświeżając puls co ``_PULS_CO_S``.

    Wiek pliku pulsu ma mówić „czy proces żyje", a nie „jak często odpytujemy Graph". Bez cięcia
    snu na kawałki puls bił co najwyżej raz na godzinę (sufit nasłuchu), więc próg healthchecku
    musiałby wynosić 2 h — czyli stojąca pętla byłaby wykrywana dopiero po dwóch godzinach.
    """
    pozostalo = sekundy
    while pozostalo > 0:
        odswiez_puls(settings)
        krok = min(pozostalo, _PULS_CO_S)
        sleep(krok)
        pozostalo -= krok
    odswiez_puls(settings)


_MAX_ZAWIESZONYCH_W_ALERCIE = 10  # dłuższa lista i tak nie zmieści się w powiadomieniu na telefon


def zglos_zawieszone_zapisy(settings: Settings) -> None:
    """Zgłoś wpisy ``APPLYING`` zastane w stanie przy starcie usługi.

    ``APPLYING`` znaczy: pracownik potwierdził, commit stanu poszedł, a proces zniknął ZANIM zapis
    do Shifts się domknął. Grafik może być wtedy pusty, kompletny albo uzupełniony w połowie i nie
    da się tego rozstrzygnąć z zewnątrz — dlatego status jest terminalny i obieg go NIE wznawia
    (drugie podejście mogłoby zdublować wpisy, a to jest nieodwracalne).

    Do tej pory jedynym śladem był licznik w cotygodniowym podsumowaniu, czyli sygnał spóźniony
    nawet o siedem dni — a ``prune_terminal`` po czasie kasuje sam dowód. Start procesu jest
    najwcześniejszą chwilą, w której da się o tym powiedzieć: usługa właśnie wstała po tym, co
    przerwało zapis.

    Waga ``BLAD``, nie ``KRYTYCZNY``: usługa pracuje dalej, tylko ktoś musi zajrzeć do grafiku.
    ``KRYTYCZNY`` jest tu zarezerwowany dla „usługa się zatrzymuje" (utrata sesji) i zlanie tych
    dwóch znaczeń odebrałoby progowi całą wartość.
    """
    zawieszone = sorted(
        (p for p in st.load_state(settings.state_path).values() if p.status == st.APPLYING),
        key=lambda p: (p.week_start, p.member_id),
    )
    if not zawieszone:
        return
    wiersze = [
        f"• {etykiety.osoba(p, settings)} — tydzień od {p.week_start}"
        for p in zawieszone[:_MAX_ZAWIESZONYCH_W_ALERCIE]
    ]
    if len(zawieszone) > _MAX_ZAWIESZONYCH_W_ALERCIE:
        wiersze.append(f"• …i {len(zawieszone) - _MAX_ZAWIESZONYCH_W_ALERCIE} więcej")
    logger.error("Zastano %d zapis(ów) przerwanych w połowie (status APPLYING).", len(zawieszone))
    operator.alert(
        settings,
        "Zapis do grafiku przerwany w połowie",
        "Proces zniknął po potwierdzeniu pracownika, a przed domknięciem zapisu do Shifts. "
        "Te grafiki mogą być uzupełnione częściowo albo wcale — usługa ich NIE wznowi, bo drugie "
        "podejście zdublowałoby wpisy nieodwracalnie. Sprawdź je ręcznie:\n" + "\n".join(wiersze),
    )


# JEDYNE miejsce łączące słownik statusów ze słownikiem pozycji raportu. Wcześniej te same
# informacje stały w dwóch miejscach (zbiór „co zliczamy" plus wypisane przypisania) i nic nie
# pilnowało, żeby się zgadzały. Klucz: status z `state`. Wartość: nazwa pola `LiczbyTygodnia`,
# czyli POZYCJA RAPORTU — dwa różne słowniki spotykają się dokładnie tutaj i nigdzie indziej.
#
# Zgodność map z polami pilnuje `test_kontrakty.py`: literówka w wartości daje padnięty test,
# a nie `TypeError` przy piątkowym przebiegu u klienta.
STATUS_DO_POZYCJI = {
    st.AWAITING_REPLY: "oczekuje",
    st.AWAITING_CONFIRM: "do_potwierdzenia",
    st.APPLIED: "zapisane",
    st.APPLYING: "niepotwierdzone",
    st.DECLINED: "odmowy",
    st.EXPIRED: "wygasle",
    st.SELF_FILLED: "samodzielne",
}
ZLICZANE_STATUSY = frozenset(STATUS_DO_POZYCJI)


def _liczby_per_tydzien(stan: dict[str, st.PendingReminder]) -> list[LiczbyTygodnia]:
    """Przełóż statusy wpisów na pozycje raportu, osobno dla każdego tygodnia docelowego.

    To JEST miejsce na tłumaczenie słownictwa: `messages` nie zna statusów, a `state` nie zna
    raportu.

    Status spoza `STATUS_DO_POZYCJI` trafia do osobnej pozycji `nierozpoznane`, a nie w próżnię.
    Bez niej wpis znikał z LICZB (choć blok tygodnia zostawał), więc tydzień z samymi takimi
    wpisami dostawał same zera i etykietę „domknięty" — czyli raport potwierdzał, że sprawa się
    skończyła, o tygodniu, o którym nic nie wiadomo. Taki wpis nie jest terminalny, więc
    `runtime.nudge` traktuje go jak otwartą rozmowę, a sprzątanie stanu nigdy go nie ruszy.

    Skąd w ogóle nieznany status: proponowany kontrakt zgodności (**N34**, `plan-rozwoju.md` §11)
    ZAKAZUJE dokładania wartości do istniejących enumów właśnie po to, żeby cofnięcie obrazu było
    samą podmianą wersji. Ta gałąź jest siatką na jego złamanie, nie realizacją.
    """
    per_tydzien: dict[str, Counter[str]] = defaultdict(Counter)
    for pending in stan.values():
        per_tydzien[pending.week_start][pending.status] += 1

    bloki = []
    for week_start, licznik in per_tydzien.items():
        pozycje = {pozycja: licznik[status] for status, pozycja in STATUS_DO_POZYCJI.items()}
        nierozpoznane = sum(n for s, n in licznik.items() if s not in ZLICZANE_STATUSY)
        if nierozpoznane:
            # Jedno ostrzeżenie na tydzień, nie na wpis: log ma zwrócić uwagę, a nie zostać zalany.
            logger.warning(
                "Tydzień %s ma %d wpisów o statusie nieznanym temu wydaniu (%s) — trafiają do "
                "pozycji »nierozpoznane« i NIE pozwalają nazwać tygodnia domkniętym",
                week_start,
                nierozpoznane,
                ", ".join(sorted(s for s in licznik if s not in ZLICZANE_STATUSY)),
            )
        bloki.append(LiczbyTygodnia(week_start=week_start, nierozpoznane=nierozpoznane, **pozycje))
    return bloki


def _send_summary(settings: Settings, client: GraphClient, nastepny_przebieg: datetime) -> None:
    """Wyślij administratorowi podsumowanie stanu po przebiegu (sygnał życia usługi).

    W trybie próbnym TYLKO loguje. Podsumowanie idzie na Teams do konkretnego człowieka, więc
    podlega tej samej obietnicy co powiadomienia dla pracowników: „nic nie zostanie wysłane".
    Alerty webhookiem to inna kategoria i celowo działają także w dry-run — dotyczą stanu samej
    usługi, lecą na endpoint należący do operatora i muszą dać się przetestować przed startem.
    """
    if not settings.admin_user_ids:
        return
    try:
        tresc = build_summary_text(
            tygodnie=_liczby_per_tydzien(st.load_state(settings.state_path)),
            nastepny_przebieg=nastepny_przebieg.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M"),
        )
        if settings.dry_run:
            logger.info(
                "[dry-run] podsumowanie do %d adresatów:\n%s", len(settings.admin_user_ids), tresc
            )
            return
        html = to_html(tresc)
    except Exception:
        # Budowa treści dotyczy wszystkich adresatów naraz — tu nie ma czego izolować.
        # Podsumowanie to raport, nie praca: jego awaria nie może przewrócić usługi.
        logger.exception("Nie udało się przygotować podsumowania dla administratorów")
        _alert_o_niedostarczonym_podsumowaniu(settings, ilu=len(settings.admin_user_ids))
        return

    # Izolacja PER ADRESAT, nie jeden `try` na całość. Powód jest ten sam, dla którego ta lista
    # w ogóle powstała: podsumowanie jest dead man's switchem, a przy wspólnym `try` awaria
    # wysyłki do PIERWSZEJ osoby (jej czat, jej uprawnienia, jej 404) kasowała sygnał życia
    # wszystkim pozostałym — czyli redundancja adresatów byłaby pozorna.
    #
    # `get_me()` jest W PĘTLI, choć wynik jest ten sam dla wszystkich: to wywołanie SIECIOWE
    # (`GET /me`) bez ponowień, więc przed pętlą byłoby wspólnym punktem awarii — jeden przejściowy
    # 5xx gasiłby sygnał wszystkim, mimo sprawnych czatów. Koszt: jedno dodatkowe GET tygodniowo
    # na adresata. Klient buforuje odpowiedź w obrębie przebiegu tylko dla `run_once`, więc liczymy
    # to jawnie, zamiast zakładać.
    dostarczone = 0
    for admin_id in settings.admin_user_ids:
        try:
            chat_id = client.create_or_get_chat(client.get_me(), admin_id)
            do_administratora(client, chat_id, html)
            dostarczone += 1
        # Świadomie BEZ `wysylka.NIE_POLYKAJ`: to kanał administratora, nie pracownika. Ciszy nie
        # podlega (więc `CiszaError` tu nie powstaje), a utrata sesji nie ma czego tu przerywać —
        # niedostarczone podsumowanie jest już opisane alertem „nie dotarło do nikogo", który idzie
        # webhookiem, czyli kanałem niezależnym od Graph. Sama sesja i tak zatrzyma usługę w pulsie
        # w tym samym obiegu. Połknięcie tutaj kosztuje więc jeden cykl, a propagacja kosztowałaby
        # dead man's switcha dokładnie wtedy, gdy jest najbardziej potrzebny.
        except Exception:
            logger.exception("Nie udało się wysłać podsumowania do administratora %s", admin_id)

    if not dostarczone:
        _alert_o_niedostarczonym_podsumowaniu(settings, ilu=len(settings.admin_user_ids))


def _alert_o_niedostarczonym_podsumowaniu(settings: Settings, *, ilu: int) -> None:
    """Druga połowa dead man's switcha: cisza z powodu awarii ma wyglądać inaczej niż cisza.

    Sama lista adresatów broni przed awarią JEDNEGO kanału. Nie broni przed awarią wspólną
    (odczyt stanu, budowa treści, Graph niedostępny dla wszystkich) — a wtedy jedynym śladem był
    `logger.exception` w instalacji, o której cała ta pozycja zakłada, że nikt nie czyta jej logów.
    Odbiorca dostawał wtedy sygnał „usługa nie żyje", choć usługa żyje i pracuje.

    Webhook jest tu właściwym kanałem, bo jako jedyny nie zależy od Graph ani AAD — czyli od tego,
    co właśnie zawiodło. Ryzyka zalewu nie ma: ta ścieżka biegnie najwyżej raz na przebieg.
    """
    operator.alert(
        settings,
        "Podsumowanie tygodniowe nie dotarło do nikogo",
        f"Żaden z {ilu} adresatów nie dostał podsumowania. Usługa PRACUJE — brak wiadomości "
        f"w Teams nie znaczy w tym tygodniu, że pętla stoi. Szczegóły awarii są w logu kontenera.",
        waga=alerts.BLAD,
    )


def _safe_run_once(
    settings: Settings,
    client: GraphClient,
    now: datetime,
    *,
    teraz: datetime,
    sleep: Callable[[float], None] = time.sleep,
    alertuj: bool = True,
    budzet: BudzetPrzebiegu | None = None,
) -> bool:
    """Przebieg z ponowieniem; zwraca czy się POWIÓDŁ. Utrata tokenu zatrzymuje usługę.

    Wynik jest istotny dla orkiestracji: nieudanego przebiegu nie wolno odhaczyć jako obsłużonego,
    bo wtedy okno łaski nie dałoby drugiej szansy i tydzień przepadłby po jednym dławieniu Graph.

    Okno czasowe obejmuje CAŁE ponawianie, nie pojedynczą próbę: trzy próby po pełnym limicie
    znaczyłyby trzykrotność tego, co operator ustawił, czyli pokrętło mówiłoby nieprawdę.
    Podsumowanie zostaje POZA oknem — to raport dla administratora, a nie część przebiegu, i ma
    wyjść zwłaszcza wtedy, gdy przebieg się nie udał.
    """
    okno = budzet.na_czas("Przebieg tygodniowy") if budzet is not None else nullcontext()
    try:
        # `sleep` MUSI iść dalej: to pętla ponowień faktycznie usypia (30 s + 60 s), więc bez
        # przekazania parametru wstrzyknięcie atrapy nic nie daje i testy śpią naprawdę.
        with okno:
            _run_once_with_retry(settings, client, now=now, teraz=teraz, sleep=sleep)
        return True
    except AuthExpiredError as blad:
        operator.zglos_utrate_sesji(settings, blad, sleep)
        raise
    except CiszaWstrzymalaPrzebieg:
        # NIE alertujemy i NIE zamieniamy na `False` tutaj: rozstrzyga wołający, bo odmowa dotyczy
        # także PODSUMOWANIA, a o nim ta funkcja nic nie wie. Alert „przebieg nie powiódł się" co
        # tydzień o tej samej porze nauczyłby administratora przewijać alerty tej usługi — czyli
        # kosztowałby ten jeden, który coś znaczy.
        raise
    except PrzebiegPrzekroczylCzasError as blad:
        # Osobno od gałęzi niżej, bo tamta mówi „mimo ponowień" — a tutaj ponowień CELOWO nie było
        # (błąd jest na liście nietransientnych). Alert opisujący nieistniejące próby wysyłałby
        # operatora na złą ścieżkę diagnostyczną.
        logger.error("Przebieg powiadomień przerwany limitem czasu: %s", blad)
        if alertuj:
            operator.alert(
                settings,
                "Przebieg powiadomień przekroczył limit czasu",
                f"{blad} Nikt nie dostał prośby w tym tygodniu — ponowię w oknie łaski.",
            )
        return False
    except Exception as blad:
        logger.exception("Przebieg powiadomień nie powiódł się mimo ponowień")
        # `alertuj=False` przy KOLEJNYCH podejściach do tego samego terminu w oknie łaski. Trwała
        # awaria dawała inaczej alert co `_PONOWIENIE_PRZEBIEGU_S`, czyli kilkanaście wiadomości
        # zamiast jednej — dokładnie ten sam problem, który `_puls_sesji` rozwiązuje progiem.
        if alertuj:
            operator.alert(
                settings,
                "Przebieg powiadomień nie powiódł się",
                f"Mimo ponowień: {operator.tresc_publiczna(blad)}. "
                f"Nikt nie dostał prośby w tym tygodniu.",
            )
        return False


def _przebieg_i_podsumowanie(
    settings: Settings,
    client: GraphClient,
    now: datetime,
    sleep: Callable[[float], None],
    *,
    alertuj: bool = True,
    teraz: datetime,
    budzet: BudzetPrzebiegu | None = None,
) -> bool:
    """Przebieg RAZEM z podsumowaniem — nierozłącznie. Zwraca, czy przebieg się powiódł.

    ``now`` i ``teraz`` to DWIE różne rzeczy i nie wolno ich zlewać: ``now`` jest odniesieniem
    tygodnia (przy nadrabianiu to MINIONY termin, żeby restart po północy nie przesunął tygodnia
    o siedem dni), a ``teraz`` to bieżąca chwila, od której liczymy najbliższy przyszły termin
    podawany administratorowi w podsumowaniu.

    Podsumowanie jest „dead man's switchem": brak wiadomości w piątek wieczorem to jedyny sygnał
    awarii w instalacji bez monitoringu. Gdy stało tylko po przebiegu ZAPLANOWANYM, tydzień po
    restarcie hosta wyglądał jak awaria — nadrobienie wysyłało prośby, a administrator nie
    dostawał nic. Związanie obu czynności w jednym miejscu sprawia, że nie da się ich rozdzielić.
    """
    # PRZED przebiegiem, bo to on sprząta stan: zastane zapisy `APPLYING` mają zostać zgłoszone
    # dopóki ktoś ich ręcznie nie uprzątnie. Alert startowy bywa jednorazowy (a webhook potrafi go
    # zgubić), więc powtórzenie raz na tydzień jest tu jedynym mechanizmem, który nie zależy od
    # tego, czy poprzednia wiadomość doleciała.
    zglos_zawieszone_zapisy(settings)
    try:
        udany = _safe_run_once(
            settings,
            client,
            now=now,
            teraz=teraz,
            sleep=sleep,
            alertuj=alertuj,
            budzet=budzet,
        )
    except CiszaWstrzymalaPrzebieg as odmowa:
        # Podsumowanie jest ODKŁADANE razem z przebiegiem, a nie wysyłane mimo odmowy. Inaczej dead
        # man's switch mówi nieprawdę dokładnie na tej ścieżce: przy pustym stanie treść brzmi „brak
        # spraw w toku — nikogo nie trzeba było zagadnąć", czyli twierdzi, że przebieg się odbył
        # i nikomu nie brakowało grafiku — w chwili, w której przebieg się NIE odbył. Administrator
        # dostawał dwie sprzeczne wiadomości na jeden termin (o 21:00 i po nadrobieniu o 07:00),
        # a przy `CATCHUP_GRACE_HOURS=0` fałszywa była JEDYNĄ.
        #
        # Milczenie jest tu właściwym sygnałem: gdy nadrobienie po ciszy też nie dojdzie do skutku,
        # brak podsumowania JEST alarmem — i po to ten kanał istnieje.
        logger.info(
            "Przebieg i podsumowanie odłożone przez godziny ciszy — wracam po %s",
            odmowa.dozwolona_od.isoformat(),
        )
        return False
    # Podsumowanie zawsze po UDANYM przebiegu (dead man's switch), a po nieudanym tylko przy
    # pierwszym podejściu do danego terminu — kolejne w oknie łaski nic nowego nie wnoszą.
    if udany or alertuj:
        _send_summary(settings, client, _kolejny_termin(settings, teraz))
    return udany


# Sufit funkcji przekroczony ŚWIADOMIE: pętla usługi trzyma naraz terminarz, okno łaski, godziny
# ciszy, puls sesji i podsumowanie — a każde z nich czyta ten sam zegar i ten sam `last_run_term`.
# Wyniesienie ich osobno rozdzieliłoby stan od warunków, które go zmieniają. Dług, nie
# usprawiedliwienie.
def run_forever(  # noqa: C901, PLR0915
    settings: Settings,
    client: GraphClient,
    llm: LlmClient,
    *,
    sleep: Callable[[float], None] = time.sleep,
    teraz: Callable[[], datetime] = lambda: datetime.now(_UTC),
    czy_kontynuowac: Callable[[], bool] = lambda: True,
    budzet: BudzetPrzebiegu | None = None,
) -> None:
    """Pętla: nadrób zaległy przebieg, do terminu obsługuj odpowiedzi, w terminie wyślij nowe.

    ``teraz`` i ``czy_kontynuowac`` istnieją po to, żeby tę pętlę dało się w ogóle przetestować.
    Domyślne wartości zachowują dotychczasowe zachowanie co do joty (zegar systemowy, pętla
    nieskończona), więc produkcja ich nie widzi.

    ``czy_kontynuowac`` MUSI stać w OBU pętlach — zewnętrznej i wewnętrznej — ORAZ przy przebiegu
    za pętlą wewnętrzną. Warunek tylko w zewnętrznej zawiesza test na pętli nasłuchu, tylko
    w wewnętrznej — na terminarzu, a pominięty przy przebiegu puszcza pełny przebieg tygodniowy PO
    sygnale stop (warunek pętli jest zwarciowy, więc wychodzimy z niej z zegarem już na terminie).
    Rzucanie wyjątku z ``sleep`` nie jest alternatywą: ``_safe_run_once`` łapie ``Exception``,
    więc atrapa usypiania nie ma jak przerwać pętli w sposób odróżnialny od awarii.

    ``teraz`` i ``sleep`` to DWIE POŁOWY JEDNEGO ZEGARA i wstrzykuje się je razem albo wcale.
    Sam ``teraz`` (stały) przy prawdziwym ``sleep`` nigdy nie dojdzie do ``pobudka`` — pętla stanie
    na realnym czekaniu. Sam ``sleep`` (atrapa) przy prawdziwym zegarze wali w Graph z pełną
    prędkością. Wzorzec, który działa, jest w ``tests/test_petla_uslugi.ZegarPetli``: jeden obiekt,
    w którym uśpienie PRZESUWA czas.
    """
    # Brak budżetu (testy, starsze wywołania) znaczy „bez ograniczenia" — limit 0 wyłącza okno,
    # więc `na_czas` staje się przezroczyste i zachowanie jest co do joty dotychczasowe.
    budzet = budzet if budzet is not None else BudzetPrzebiegu(0, teraz)
    last_run_term: datetime | None = None  # termin już obsłużony w TEJ sesji (dedup nadrobień)
    zgloszony_termin: datetime | None = None  # termin, o którego awarii operator już wie
    przekroczenia_nasluchu = 0  # z RZĘDU; próg niżej decyduje, kiedy operator ma się dowiedzieć
    awarie_modelu = 0  # jak wyżej, dla granicy modelu — obie awarie są niewidoczne bez licznika
    # PRZED pętlą, bo to diagnoza tego, co zastaliśmy po restarcie — a nie zdarzenie z tej sesji.
    # Wpięte tutaj, a nie w `cli`, z tego samego powodu co sufit czasu przebiegu: alerty są dla
    # pracy BEZOBSŁUGOWEJ, a `--once`/`--poll-once` uruchamia człowiek, który widzi log.
    zglos_zawieszone_zapisy(settings)
    # Pamięć grafiku dla kroku 1.5 (ADR 0009) — właścicielem jest PĘTLA, bo to ona przeżywa obiegi.
    # Świadomie NIE `SnapshotGrafiku`: jeden obiekt obsługujący i ścieżkę zapisu, i krok
    # samouzupełnienia byłby o jeden `bool` od podania nieświeżych danych przed POST-em do Shifts.
    pamiec = PamiecSamouzupelnien()
    # Start liczy się jako świeżo potwierdzona sesja (`_ensure_authenticated` właśnie ją sprawdził).
    stan_pulsu = StanPulsu(teraz() + timedelta(hours=settings.heartbeat_interval_h))
    powitanie_wyslane = False
    while czy_kontynuowac():
        now = teraz()
        # GODZINY CISZY rozstrzygamy TUTAJ, a nie w `run_once`/`poll_replies`, i to jest poprawka
        # przeglądu, nie kosmetyka. Pętla jest jedynym miejscem, które trzyma REALNY zegar oraz
        # `last_run_term` — a przebieg nadrabiający dostaje `now` równe MINIONEMU terminowi
        # (odniesienie tygodnia). Bramka wewnątrz `run_once` patrzyła więc na piątek 16:00,
        # przepuszczając wysyłkę wykonywaną faktycznie o trzeciej nad ranem. Drugi defekt tej samej
        # konstrukcji: odmowa z powodu ciszy wracała jako `[]`, czyli nieodróżnialnie od sukcesu,
        # więc pętla odhaczała `last_run_term` i CAŁY ZESPÓŁ nie dostawał prośby w tym tygodniu —
        # bezgłośnie, przy `RUN_HOUR` wpadającym w okno ciszy (konfiguracja legalna).
        if not wolno_pisac(now, settings.okno_ciszy):
            koniec_ciszy = najblizsza_dozwolona(now, settings.okno_ciszy)
            logger.info(
                "Godziny ciszy — nie piszę do pracowników; wracam do pracy o %s",
                koniec_ciszy.isoformat(),
            )
            # Bramka odkłada pracę idącą DO PRACOWNIKÓW, a nie cały obieg pętli. Obowiązki wobec
            # OPERATORA wykonujemy tak samo jak poza ciszą — inaczej nocny start usługi nie
            # zostawia po sobie ani alertu, ani sprawdzenia sesji, a healthcheck świeci na zielono,
            # bo plik pulsu odświeża `spij_z_pulsem`. Ta sama zasada, wedle której `poll_replies`
            # odkłada odpowiadanie, ale nie domykanie tematów.
            # Termin w powitaniu MUSI uwzględnić zaległy przebieg, bo gałąź ciszy stoi PRZED
            # nadrabianiem i kończy się `continue` — poza ciszą powitanie idzie już po nim.
            # Bez tego start w piątek o 21:00 (czyli scenariusz, dla którego ta poprawka powstała)
            # obiecywał operatorowi „najbliższy przebieg za tydzień", a zespół dostawał prośby
            # o 07:00 tego samego poranka. Zamiana jednej nieprawdy na drugą nie jest naprawą.
            #
            # Gdy nadrobienie jest należne, realnym momentem wykonania jest koniec ciszy — i to
            # zachodzi w OBIE strony: `_catchup_due` odlicza ciszę z okna łaski, więc termin
            # należny teraz jest tak samo należny po całym nieprzerwanym oknie ciszy.
            #
            # `najblizsza_dozwolona` na końcu obejmuje drugi wariant tej samej nieprawdy, węższy:
            # `RUN_HOUR` wpadający w okno ciszy jest konfiguracją LEGALNĄ (patrz `_catchup_due`),
            # a wtedy sam `_kolejny_termin` wskazuje godzinę, o której pisać nie wolno — przebieg
            # i tak wykona się dopiero po ciszy, przez nadrobienie. Poza ciszą funkcja zwraca swój
            # argument bez zmiany, więc zwykły przypadek zostaje nietknięty.
            zalegly_w_ciszy = _catchup_due(settings, now)
            termin_powitania = najblizsza_dozwolona(
                koniec_ciszy if zalegly_w_ciszy is not None else _kolejny_termin(settings, now),
                settings.okno_ciszy,
            )
            powitanie_wyslane = _powitanie(settings, termin_powitania, wyslane=powitanie_wyslane)
            stan_pulsu = _puls_z_obsluga_utraty(settings, client, stan_pulsu, sleep, teraz=teraz())
            # Śpimy do KOŃCA CISZY albo do najbliższego pulsu — zależnie od tego, co wypada
            # wcześniej. Jeden sen na całą noc znaczyłby, że `heartbeat_interval_h` przestaje
            # obowiązywać dokładnie wtedy, gdy nikt nie patrzy: utrata sesji o 21:00 wychodziłaby
            # dopiero o 07:00, a plik pulsu odświeżany przez `spij_z_pulsem` trzymałby przez
            # ten czas healthcheck na zielono. Pętla nie zakręci się w miejscu, bo `_puls_sesji`
            # przesuwa `nastepny` w KAŻDEJ gałęzi — także po nieudanej próbie.
            #
            # `last_run_term` NIETKNIĘTY: zaległy termin zostaje zaległy i nadrobi się po ciszy,
            # o ile okno łaski jeszcze trwa (`_catchup_due` nie liczy godzin ciszy).
            pobudka_ciszy = min(koniec_ciszy, stan_pulsu.nastepny)
            spij_z_pulsem(settings, max(0.0, (pobudka_ciszy - teraz()).total_seconds()), sleep)
            continue
        # Nadrobienie: zaplanowany termin właśnie minął (okno łaski) → wykonaj przebieg teraz
        # (idempotentnie), z czasem TERMINU jako odniesieniem tygodnia (nie „teraz").
        # Pomijamy termin obsłużony już w tej sesji: po zaplanowanym przebiegu `previous_run(now)`
        # wskazuje ten sam termin, więc bez znacznika byłby zbędny podwójny odczyt z Graph co cykl.
        # Po restarcie znacznik znika — realna zaległość (awaria po terminie) i tak się nadrobi.
        catchup_term = _catchup_due(settings, now)
        if catchup_term is not None and catchup_term != last_run_term:
            logger.info(
                "Nadrabiam zaległy przebieg powiadomień (okno łaski %dh).",
                settings.catchup_grace_hours,
            )
            pierwsze_podejscie = catchup_term != zgloszony_termin
            if _przebieg_i_podsumowanie(
                settings,
                client,
                catchup_term,
                sleep,
                alertuj=pierwsze_podejscie,
                teraz=teraz(),
                budzet=budzet,
            ):
                last_run_term = catchup_term  # odhaczamy WYŁĄCZNIE udany przebieg
            zgloszony_termin = catchup_term
            # Zegar MUSI być odczytany ponownie: przebieg z ponowieniami i dławieniem Graph
            # (budżet 900 s na żądanie × 3 próby) trwa czasem dłużej niż `_PONOWIENIE_PRZEBIEGU_S`.
            # Na starym `now` `pobudka` wypadałaby wtedy w PRZESZŁOŚCI, więc pętla nasłuchu nie
            # wykonałaby ani jednego obiegu — bot milczałby przez całe okno łaski, mimo że żyje.
            now = teraz()
        termin = _kolejny_termin(settings, now)
        # Pobudka może wypaść WCZEŚNIEJ niż termin: gdy zaległy przebieg wciąż czeka w oknie łaski,
        # wracamy tu za `_PONOWIENIE_PRZEBIEGU_S`, żeby dać mu drugą szansę. Bez tego kilkunasto-
        # minutowe dławienie Graph w piątek o 16:00 kosztowałoby cały tygodniowy cykl.
        zalegly = _catchup_due(settings, now)
        pobudka = termin
        if zalegly is not None and zalegly != last_run_term:
            pobudka = min(termin, now + timedelta(seconds=_PONOWIENIE_PRZEBIEGU_S))
        logger.info("Następny przebieg powiadomień: %s", termin.isoformat())
        powitanie_wyslane = _powitanie(settings, termin, wyslane=powitanie_wyslane)
        while czy_kontynuowac() and teraz() < pobudka:
            odswiez_puls(settings)
            outcome: PollOutcome | None = None
            try:
                # Zegar pętli idzie DALEJ, do nasłuchu. Bez tego `poll_replies` sięgało po własny
                # `datetime.now`, więc pętla i wygaszanie okien odpowiedzi mierzyły czas dwoma
                # niezależnymi zegarami. W produkcji dają tę samą wartość, ale „w produkcji to
                # jedno i to samo" nie jest niezmiennikiem — jest zbiegiem okoliczności.
                with budzet.na_czas("Obieg nasłuchu"):
                    outcome = poll_replies(settings, client, llm, now=teraz(), pamiec=pamiec)
                if przekroczenia_nasluchu >= _PROG_ALERTU_PRZEKROCZEN:
                    operator.alert(
                        settings,
                        "Nasłuch znów mieści się w limicie czasu",
                        "Obieg zakończył się w całości — odpowiedzi są przetwarzane.",
                        waga=alerts.INFO,
                    )
                if awarie_modelu >= _PROG_ALERTU_PRZEKROCZEN:
                    operator.alert(
                        settings,
                        "Interpretacja odpowiedzi znów działa",
                        "Model odpowiedział — odpowiedzi pracowników są przetwarzane. "
                        "Nic nie przepadło: nieobsłużone wiadomości czekały za watermarkiem.",
                        waga=alerts.INFO,
                    )
                przekroczenia_nasluchu = 0
                awarie_modelu = 0
            except AuthExpiredError as blad:
                # Utrata tokenu to NIE transientny błąd — zatrzymaj się czysto po zgłoszeniu
                # alertu; `unless-stopped` podniesie usługę, gdy człowiek wykona `--login`.
                operator.zglos_utrate_sesji(settings, blad, sleep)
                raise
            except PrzebiegPrzekroczylCzasError as blad:
                # Przed gałęzią ogólną, bo różni je nie tylko treść logu: pojedyncze przerwanie
                # jest nieszkodliwe (obieg wróci za `poll_interval_s`), ale TRWAŁE znaczy, że
                # odpowiedzi pracowników przestały być przetwarzane — i nikt by się o tym nie
                # dowiedział, bo pętla żyje, puls bije, a healthcheck świeci na zielono.
                przekroczenia_nasluchu += 1
                logger.warning(
                    "Obieg nasłuchu przerwany limitem czasu (%d. raz z rzędu): %s",
                    przekroczenia_nasluchu,
                    blad,
                )
                if przekroczenia_nasluchu == _PROG_ALERTU_PRZEKROCZEN:
                    operator.alert(
                        settings,
                        "Nasłuch nie mieści się w limicie czasu",
                        f"{przekroczenia_nasluchu} obiegi z rzędu przerwane po "
                        f"{settings.run_deadline_s} s. Odpowiedzi pracowników mogą nie być "
                        f"przetwarzane, mimo że usługa wygląda na zdrową. {blad}",
                    )
            except LlmNiedostepnyError as blad:
                # Granica modelu, symetrycznie do gałęzi wyżej. Bez tej gałęzi zły klucz API,
                # wyczerpany limit i awaria dostawcy wyglądały jak „pracownicy piszą niejasno":
                # każdy dostawał prośbę o doprecyzowanie, watermark ruszał, okna gasły po 48 h,
                # a operator nie dowiadywał się NIGDY. Alert po progu, bo pojedyncze mrugnięcie
                # sieci naprawia się samo, a powtarzalna awaria znaczy, że tydzień jest do
                # wyrzucenia — pracownicy odpisują, a ich odpowiedzi nikt nie czyta.
                awarie_modelu += 1
                logger.error(
                    "Interpretacja odpowiedzi niedostępna (%d. raz z rzędu): %s",
                    awarie_modelu,
                    blad,
                )
                if awarie_modelu == _PROG_ALERTU_PRZEKROCZEN:
                    operator.alert(
                        settings,
                        "Interpretacja odpowiedzi nie działa",
                        f"{awarie_modelu} obiegi z rzędu bez odpowiedzi od usługi modelu: "
                        f"{blad} Odpowiedzi pracowników NIE są przetwarzane — sprawdź klucz "
                        "API, limity konta i status dostawcy. Usługa pracuje dalej i wróci "
                        "do nich, gdy model odpowie.",
                    )
            except Exception:
                # Błąd listenera nie może zabić pętli.
                logger.exception("Listener odpowiedzi zawiódł")
            # Puls w OSOBNYM bloku: gdy Graph jest niedostępny, `poll_replies` rzuca — a wtedy puls
            # w tym samym `try` nie wykonałby się ani razu, czyli przestałby działać dokładnie
            # w awarii, którą ma wykrywać.
            stan_pulsu = _puls_z_obsluga_utraty(settings, client, stan_pulsu, sleep, teraz=teraz())
            now_dt = teraz()
            remaining = (pobudka - now_dt).total_seconds()
            if remaining > 0:
                spij_z_pulsem(
                    settings, min(remaining, _poll_delay(settings, outcome, now_dt)), sleep
                )
        odswiez_puls(settings)
        # Przebieg wykonujemy TYLKO po dojściu do terminu. Wcześniejsza pobudka oznacza ponowienie
        # zaległego przebiegu — obsłuży je `_catchup_due` na górze pętli.
        #
        # `czy_kontynuowac` MUSI być sprawdzone także TUTAJ. Warunek pętli wewnętrznej jest
        # zwarciowy, więc przy sygnale stop drugi człon (`teraz() < pobudka`) nie jest liczony
        # i wychodzimy stąd z zegarem stojącym już na terminie — a wtedy ten `if` puszczał PEŁNY
        # przebieg tygodniowy PO sygnale stop, czyli wysyłał prośby do ludzi wbrew żądaniu
        # zatrzymania. Zweryfikowane sondą: scena „pracuj do terminu i przestań" wysyłała
        # wiadomość. Produkcji to nie dotyka (domyślne `lambda: True`), ale to jedyny szew
        # sterujący warstwą, która pisze do ludzi.
        if (
            czy_kontynuowac()
            and teraz() >= termin
            and _przebieg_i_podsumowanie(
                settings, client, teraz(), sleep, teraz=teraz(), budzet=budzet
            )
        ):
            last_run_term = termin  # odhaczamy WYŁĄCZNIE udany przebieg
