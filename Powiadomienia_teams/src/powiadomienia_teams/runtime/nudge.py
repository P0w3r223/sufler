"""Przebieg tygodniowy: kto nie ma grafiku na przyszły tydzień i co mu napisać.

Jedna odpowiedzialność — wykrycie luki i wysłanie prośby. Ten moduł nie wie nic o odpowiedziach
pracowników ani o terminarzu: kiedy go uruchomić, decyduje ``runtime.service``, a co zrobić
z odpowiedzią — ``runtime.listener``.

Idempotencja przebiegu opiera się WYŁĄCZNIE na pliku stanu: jedna prośba na osobę na dany tydzień.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TypeGuard

from powiadomienia_teams import state as st
from powiadomienia_teams.agent.interpreter import schedule_to_intervals
from powiadomienia_teams.config import Settings
from powiadomienia_teams.domain.czas import to_graph_iso
from powiadomienia_teams.domain.models import Member
from powiadomienia_teams.domain.tozsamosc import ten_sam, znormalizuj
from powiadomienia_teams.graph.client import GraphClient
from powiadomienia_teams.messages import build_nudge_text, to_html
from powiadomienia_teams.reminders.detect import members_without_shifts, off_weekdays_by_member
from powiadomienia_teams.reminders.lifecycle import prune_terminal, termin_dla_nowej_prosby
from powiadomienia_teams.reminders.propose import TYGODNIE_HISTORII, proposal_from_history
from powiadomienia_teams.runtime import etykiety, operator
from powiadomienia_teams.runtime.budzet import PrzebiegPrzekroczylCzasError
from powiadomienia_teams.runtime.cisza import (
    CiszaWstrzymalaPrzebieg,
    najblizsza_dozwolona,
    wolno_pisac,
)
from powiadomienia_teams.runtime.wysylka import NIE_POLYKAJ, do_pracownika
from powiadomienia_teams.scheduler.weekly import week_windows

logger = logging.getLogger(__name__)
_UTC = timezone.utc


def _nadpisanie_warte_zgloszenia(
    existing: st.PendingReminder | None,
) -> TypeGuard[st.PendingReminder]:
    """Czy zastąpienie tego wpisu nowym tygodniem jest stratą, o której operator MUSI wiedzieć.

    Dwa przypadki, z różnych powodów:

    * **wpis OTWARTY** — przepada uzgodniony grafik, którego nikt nie zapisał (od 0.2.11);
    * **``APPLYING``** — przepada JEDYNY ślad zapisu przerwanego w połowie.

    Ten drugi był do 2026-09-08 nadpisywany po cichu, bo ``APPLYING`` należy do ``TERMINALNE``
    i wypadał z warunku „status not in TERMINALNE". Powstawała z tego sprzeczność między dwoma
    dobrze uzasadnionymi regułami: ``lifecycle.prune_terminal`` chroni ``APPLYING`` BEZTERMINOWO
    („to DOWÓD, nie ślad", N4), a ten przebieg kasował go w najbliższy piątek — i to dokładnie
    w najgorszym wariancie, bo osoba, której zapis NIE doszedł, wraca do ``missing``. Wtedy też
    milkł ``service.zglos_zawieszone_zapisy``, czyli cotygodniowe przypomnienie „sprawdź ten
    grafik ręcznie" po prostu znikało.

    ``APPLIED``/``DECLINED``/``EXPIRED``/``SELF_FILLED`` zostają poza tą regułą: ich nadpisanie
    niczego nie gubi, bo temat domknął się rozstrzygnięciem.
    """
    if existing is None:
        return False
    return existing.status not in st.TERMINALNE or existing.status == st.APPLYING


def _alert_o_nadpisaniu(
    existing: st.PendingReminder, osoba: str, week_start_iso: str
) -> tuple[str, str]:
    """(tytuł, treść) alertu o nadpisaniu — komunikat MUSI opisywać to, co faktycznie przepadło.

    Wspólna treść byłaby dla ``APPLYING`` nieprawdziwa: nie chodzi o uzgodnienie, które nie
    trafiło do grafiku, tylko o zapis, który mógł trafić CZĘŚCIOWO — i to jest zupełnie inna
    instrukcja dla człowieka, który to sprawdza.
    """
    if existing.status == st.APPLYING:
        return (
            "Skasowany ślad zapisu przerwanego w połowie",
            f"{osoba}: wpis o tygodniu od {existing.week_start} miał status APPLYING — zapis do "
            f"Shifts zaczął się i nigdy nie potwierdził. Przebieg zaczyna tydzień {week_start_iso} "
            "i ten wpis zastępuje, więc znika JEDYNY jego ślad. Grafik tamtego tygodnia może być "
            "uzupełniony częściowo albo wcale — sprawdź go ręcznie.",
        )
    return (
        "Nadpisana otwarta rozmowa z poprzedniego tygodnia",
        f"{osoba}: rozmowa o tygodniu od {existing.week_start} była wciąż otwarta "
        f"(status {existing.status}), a przebieg zaczyna tydzień {week_start_iso}. "
        "Jeśli pracownik zdążył coś uzgodnić, to uzgodnienie NIE trafiło do grafiku — "
        "sprawdź ręcznie.",
    )


# Sufit funkcji przekroczony ŚWIADOMIE: jeden przebieg tygodniowy, od wykrycia luk do utrwalenia
# stanu. Wysyłka jest tu nierozdzielna od zapisu pendingu (pending bez wiadomości znaczy, że
# człowiek nie dostanie prośby w ogóle). Dług, nie usprawiedliwienie.
def run_once(  # noqa: PLR0915
    settings: Settings,
    client: GraphClient,
    *,
    now: datetime,
    teraz: datetime,
    ignoruj_cisze: bool = False,
) -> list[Member]:
    """Jeden przebieg powiadomień: wykryj luki, zbuduj propozycje, wyślij (lub loguj w dry-run).

    W godzinach ciszy przebieg NIE zaczyna się wcale i nie tworzy żadnego pendingu — zgłasza to
    wyjątkiem ``CiszaWstrzymalaPrzebieg``, bo odmowa MUSI być odróżnialna od „nikomu nie brakuje
    grafiku" (oba wyglądały jak pusta lista, a wołający odhaczał na tym termin). Wysyłka prośby
    jest tu nierozdzielna od zapisu stanu (pending powstaje po udanej wysyłce), a pending bez
    wiadomości byłby najgorszym wariantem: idempotencja `run_once` nie zaczepi tej osoby drugi raz
    w tym tygodniu, więc człowiek nie dostałby prośby o grafik wcale. `ignoruj_cisze` obsługuje
    `--once --ignoruj-cisze`, czyli świadomą decyzję operatora (decyzja 4.1/3 planu).

    **``now`` a ``teraz`` to DWIE różne chwile i mylenie ich kosztowało nocną wysyłkę.** ``now``
    jest
    odniesieniem TYGODNIA i przy nadrabianiu równa się MINIONEMU terminowi (piątek 16:00), żeby
    restart po północy nie przesunął tygodnia o siedem dni. ``teraz`` jest chwilą FAKTYCZNĄ i tylko
    ona ma prawo rozstrzygać o godzinach ciszy oraz iść do szwu wysyłki — inaczej przebieg
    nadrabiany o trzeciej nad ranem pytał o zgodę „piątku 16:00" i pisał do ludzi w środku nocy.

    ``teraz`` NIE MA wartości domyślnej i to jest decyzja (0.2.13, po defekcie B5). Wcześniej
    brzmiała ona ``teraz = teraz or now``: wołający, który o rozróżnieniu nie wiedział, dostawał
    ciche zlanie obu chwil — poprawne dla przebiegu o terminie i dla `--once`, a przy NADRABIANIU
    znaczące „pisz do ludzi wedle zegara sprzed kilku godzin". Zegar wchodził tu dwiema drogami,
    z których jedna była niewidoczna w miejscu wywołania. Tam, gdzie obie chwile są tą samą chwilą,
    wołający ma to powiedzieć wprost — kosztuje to jedno słowo i zamienia przeoczenie w błąd
    zgłoszony przy uruchomieniu, a nie o trzeciej nad ranem u pracownika.
    """
    # `ignoruj_cisze` materializuje się jako ustawienia Z WYŁĄCZONĄ ciszą, a nie jako flaga wleczona
    # przez kolejne wywołania. Dwa powody: szew wysyłki zostaje regułą BEZ WYJĄTKU (a więc nadal
    # jest
    # siatką na nowy punkt wysyłki), a cała ścieżka widzi jeden, spójny świat — bez tego `--once
    # --ignoruj-cisze` przechodził bramę pętli i padał dopiero na szwie, czyli operator dostawał
    # „nie udało się powiadomić" zamiast prośby wysłanej świadomie.
    if ignoruj_cisze:
        settings = replace(settings, cisza_od_h=0, cisza_do_h=0)
    # Tryb próbny NIE podlega ciszy — w nim nie wychodzi do pracownika ani jedna wiadomość, więc
    # nie ma przed czym chronić. Do 0.2.13 bramka stała przed tą gałęzią i `--once` w trybie
    # próbnym odmawiał wieczorem, choć krok 6 wdrożenia uruchamia go właśnie po to, żeby zobaczyć
    # listę osób i propozycje. Ta sama zasada stoi już w `poll_replies` (wyjście na `dry_run`
    # PRZED bramką ciszy) i w `cli._ustawienia_proby` dla `--proba-nasluchu`: rozstrzyga to, czy
    # coś wychodzi do ludzi, a nie nazwa polecenia.
    if not settings.dry_run and not wolno_pisac(teraz, settings.okno_ciszy):
        # RZUCAMY, a nie zwracamy `[]`. Pusta lista znaczy „nikomu nie brakuje grafiku" i wołający
        # ma prawo odhaczyć na niej termin jako obsłużony — a to jest odmowa, po której przebieg
        # ma się WYDARZYĆ później (patrz `CiszaWstrzymalaPrzebieg`).
        dozwolona_od = najblizsza_dozwolona(teraz, settings.okno_ciszy)
        logger.info(
            "Godziny ciszy — nie zaczynam przebiegu; najbliższa dozwolona chwila: %s",
            dozwolona_od.isoformat(),
        )
        raise CiszaWstrzymalaPrzebieg(dozwolona_od)
    tz = settings.tz
    ctx = settings.team_context  # jeden zespół dziś; pętla po wielu wepnie się tutaj (ADR 0001)
    client.refresh_auth()
    me_id = client.get_me()
    # Konto bota NIGDY nie jest kandydatem do zagadnięcia. Bez tego bot pisze sam do siebie:
    # jest pełnoprawnym członkiem zespołu, więc `members_without_shifts` widzi je jak każdego
    # innego (potwierdzone na żywo — „Virtual Sufler" trafiło na listę braków). Filtr w KODZIE,
    # nie tylko w `ONLY_USER_IDS`, bo pusta lista odbiorców oznacza „wszyscy" i wtedy konfiguracja
    # nie chroni przed niczym.
    members = [m for m in client.list_members(ctx.team_id) if not ten_sam(m.user_id, me_id)]

    _, target_monday, target_end = week_windows(now, tz)

    # JEDNO pobranie na oba okna. `read_shifts` ściąga całą kolekcję zespołu i filtruje po stronie
    # klienta (`$filter` odpada — powody w jego docstringu), więc dwa wywołania znaczyły dwa pełne
    # przejścia przez `_MAX_PAGES` po te same ~2000 wpisów. Przy okazji oba okna widzą teraz TEN SAM
    # stan grafiku: gotowiec nie może już pochodzić z innej chwili niż wykrycie luk.
    #
    # Okno historii to TYGODNIE_HISTORII tygodni przed docelowym — z nich liczona jest propozycja
    # (`propose.proposal_from_history`). Liczone od poniedziałku LOKALNEGO, więc zmiana czasu
    # w środku okna nie ucina pierwszego dnia.
    historia_od = (target_monday.date() - timedelta(weeks=TYGODNIE_HISTORII)).isoformat()
    historia_start = datetime.fromisoformat(historia_od).replace(tzinfo=tz).astimezone(_UTC)
    next_shifts, history_shifts = client.read_shifts_w_oknach(
        ctx.team_id,
        (
            (target_monday.astimezone(_UTC), target_end.astimezone(_UTC)),
            (historia_start, target_monday.astimezone(_UTC)),
        ),
    )
    # JEDNO pobranie czasu wolnego na oba okna — ten sam powód co przy zmianach wyżej.
    wszystkie_wolne = client.read_time_off(ctx.team_id, historia_start, target_end.astimezone(_UTC))
    next_time_off = tuple(
        t for t in wszystkie_wolne if t.start < target_end and t.end > target_monday
    )
    # Dni urlopu per osoba w docelowym tygodniu (liczone w strefie zespołu, target_monday lokalne).
    # Jedno źródło prawdy dla: detekcji (pełny tydzień wolny → pomiń), propozycji (nie proponuj
    # pracy w dniu wolnym) i treści (wspomnij o dniach wolnych). Urlop CZĘŚCIOWY nie wycisza już
    # prośby — piszemy o pozostałe dni.
    off_by_member = off_weekdays_by_member(next_time_off, target_monday, tz)
    missing = list(members_without_shifts(members, next_shifts, off_by_member))
    if settings.only_user_ids:  # tryb pilotażowy — ogranicz do wskazanych osób
        # Log podaje OBIE liczby, bo samo „0 osób" na końcu przebiegu pokrywa dwa różne
        # zdarzenia: „nikomu nie brakuje grafiku" i „lista pilotażu nie trafia w nikogo".
        # To drugie jest zwykłą literówką w identyfikatorze AAD (zły GUID, obcięty znak)
        # i jest kierunkowo bezpieczne — nic nie wychodzi — ale operator pilotażu nie ma
        # się z czego dowiedzieć, że jego pilotaż milczy z jego własnego powodu.
        przed = len(missing)
        # `only_user_ids` jest już znormalizowane przy budowie `Settings`; normalizujemy więc
        # drugą stronę — tę z Graph. Porównanie, NIE podmiana: `m.user_id` idzie dalej surowe,
        # bo staje się kluczem stanu i `userId` w zapisie do Shifts.
        missing = [m for m in missing if znormalizuj(m.user_id) in settings.only_user_ids]
        logger.info(
            "Zakres pilotażowy: %d z %d osób bez grafiku (lista ma %d pozycji)",
            len(missing),
            przed,
            len(settings.only_user_ids),
        )
    week_start_iso = target_monday.date().isoformat()
    week_label = f"{target_monday:%d.%m}–{(target_end - timedelta(days=1)):%d.%m}"

    state = st.load_state(settings.state_path)
    # Tygodniowe GC: usuń dawne wpisy terminalne (applied/declined/expired), by stan nie puchł.
    # `biezacy_tydzien` chroni strażnika idempotencji: wpis terminalny na TEN tydzień zostaje
    # niezależnie od wieku, żeby nadrobienie ani ręczne `--once` nie zaczepiło osoby, która
    # już odmówiła albo której okno minęło.
    # Retencja ma WŁASNĄ zmienną (`TERMINAL_RETAIN_HOURS`), niezależną od terminu odpowiedzi:
    # zwinięte w jedną liczbę robiły z przestawienia terminu ciche przestawienie strażnika N15.
    state = prune_terminal(
        state, now, settings.terminal_retain_hours, biezacy_tydzien=week_start_iso, tz=tz
    )
    # DOWÓD ZAPISYWALNOŚCI, zanim ktokolwiek dostanie wiadomość.
    #
    # Kolejność „wyślij, potem utrwal" niżej jest ŚWIADOMA (patrz docstring): pending bez
    # wiadomości byłby najgorszym wariantem. Ale ma cenę — przy niezapisywalnym wolumenie prośby
    # wychodziły do ludzi i nie zostawał po nich ślad, więc następny przebieg startował od
    # `load_state`, który ich nie widział, i wysyłał je DRUGI RAZ. Idempotencja opiera się
    # WYŁĄCZNIE na tym pliku.
    #
    # Ten zapis nie jest nową maszynerią: to dokładnie ten sam `save_state`, który i tak stoi na
    # końcu funkcji („utrwal prune nawet gdy nic nie wysłano"), przesunięty tak, żeby padł PRZED
    # pierwszą wysyłką. `StateWriteError` ma już właściwą obsługę — `_run_once_with_retry`
    # przepuszcza go bez ponowień, bo ponawianie przebiegu z zepsutym zapisem wysyła tę samą
    # prośbę tyle razy, ile jest prób.
    #
    # Sonda nie daje gwarancji: dysk może zapełnić się między nią a właściwym zapisem. Zabiera
    # jednak przypadek TRWAŁY (wolumen tylko-do-odczytu, brak katalogu, złe prawa), czyli ten,
    # który powtarzałby się w każdym przebiegu.
    # Sonda działa w OBU trybach — patrz `state.sprawdz_zapisywalnosc`. Próba na sucho, którą
    # runbook wdrożenia stawia przed wejściem na żywo, ma wyłapać właśnie tę klasę awarii;
    # sonda oparta na `save_state` byłaby wtedy pominięta, bo w trybie próbnym stanu nie piszemy.
    st.sprawdz_zapisywalnosc(settings.state_path)
    if not settings.dry_run:
        st.save_state(settings.state_path, state)
    sent = 0
    for member in missing:
        # Szukamy po TOŻSAMOŚCI, nie znak w znak — uzasadnienie i cena pomyłki są
        # w `state.klucz_wpisu`.
        # `klucz` bywa inny niż `member.user_id` tylko wtedy, gdy Graph oddał dziś inną wielkość
        # liter niż w tygodniu, w którym wpis powstał.
        klucz = st.klucz_wpisu(state, member.user_id)
        existing = state.get(klucz)
        # Idempotencja przebiegu: JEDNA prośba na osobę na TEN tydzień. Pomijamy każdy istniejący
        # wpis na bieżący tydzień — nie tylko otwarty (ponowienie po transientnym błędzie albo
        # nadrobienie nie wyśle drugi raz tej samej prośby), ale też TERMINALNY DECLINED/EXPIRED:
        # osoba już odmówiła albo jej okno minęło, więc restart/nadrobienie w tym samym tygodniu nie
        # może nadpisać jej stanu i zaczepić ponownie (sprzeczne z „kończę przypominanie"). Osoba z
        # udanym zapisem ma już zmianę w grafiku i nie występuje w `missing`.
        if existing is not None and existing.week_start == week_start_iso:
            continue
        if _nadpisanie_warte_zgloszenia(existing):
            # Klucz stanu to `member_id`, więc wpis na NOWY tydzień nadpisze otwartą rozmowę
            # z tygodnia poprzedniego — razem z uzgodnionym już grafikiem, którego nikt nie
            # zapisał. Do 0.2.11 działo się to bez śladu. Osiągalne, gdy odczyt czatu uporczywie
            # zawodzi (`ReadOutcome.UNKNOWN` blokuje wygaszenie) albo gdy rozmowa żyje dłużej niż
            # do najbliższego piątku. Od 0.2.13 termin jest kalendarzowy, więc wiadomość pracownika
            # go NIE przesuwa, a prośba bota najwyżej o `REPLY_MIN_HOURS` — ogon jest krótszy, ale
            # ciąg wymian „pytanie bota → odpowiedź" nadal potrafi dożyć kolejnego przebiegu.
            # Log — TU, bo opisuje zamiar i ma być widoczny także w trybie próbnym (krok 6 wdrożenia
            # każe ten log przeczytać). Alert do operatora idzie DOPIERO PO faktycznym nadpisaniu,
            # niżej: jego treść mówi w czasie przeszłym „uzgodnienie NIE trafiło do grafiku —
            # sprawdź ręcznie", a stąd wychodziła, zanim cokolwiek się wydarzyło. W `DRY_RUN=true`
            # (wartość DOMYŚLNA) wpis nie jest nadpisywany NIGDY, a przy nieudanej wysyłce pętla
            # robi `continue` z nietkniętym stanem — w obu przypadkach operator dostawał polecenie
            # ręcznego sprawdzenia rozmowy, która stoi nienaruszona.
            logger.warning(
                "Nadpisuję otwartą rozmowę z %s (status %s) dla %s — zaczynam tydzień %s",
                existing.week_start,
                existing.status,
                etykiety.czlonek(member, settings),
                week_start_iso,
            )
        member_off = off_by_member.get(znormalizuj(member.user_id), frozenset())
        propozycja = proposal_from_history(
            member.user_id,
            history_shifts,
            wszystkie_wolne,
            target_monday.date(),
            tz=tz,
            skip_weekdays=member_off,
        )
        proposal = propozycja.grafik
        text = build_nudge_text(
            member,
            proposal,
            week_label,
            tz,
            off_weekdays=member_off,
            podstawa=propozycja.opis_podstawy,
            # Termin liczony TĄ SAMĄ funkcją, którą wygasza `runtime.listener` — inaczej treść
            # obiecywałaby co innego, niż robi runtime, a rozjazd wychodzi dopiero w chwili,
            # w której ktoś traci tydzień grafiku (pozycja B7 planu).
            termin=termin_dla_nowej_prosby(week_start_iso, teraz, settings.okno_odpowiedzi),
        )
        if settings.dry_run:
            # Sama TREŚĆ zaczyna się od „Cześć <imię>" (`messages.build_nudge_text`), więc
            # wypisanie jej w logu obchodziłoby całe A10 — i to w konfiguracji domyślnej,
            # bo `DRY_RUN` jest domyślnie włączony, a krok 6 wdrożenia każe ten log
            # przeczytać. Log mówi więc KTO i Z JAKĄ PROPOZYCJĄ (czyli to, co operator ma
            # tu sprawdzić), a pełna treść ląduje w nim tylko w trybie diagnostycznym.
            logger.info(
                "[dry-run] powiadomienie do %s — propozycja: %s",
                etykiety.czlonek(member, settings),
                schedule_to_intervals(proposal, tz),
            )
            if settings.loguj_nazwiska:
                logger.info("[dry-run] treść dla %s:\n%s", member.user_id, text)
            continue
        try:
            chat_id = client.create_or_get_chat(me_id, member.user_id)
            # Watermark = czas SERWERA wysłanego przypomnienia: listener bierze pod uwagę TYLKO
            # odpowiedzi po nudge'u, a nie stare wiadomości z czatu ani (przy przesuniętym lokalnym
            # zegarze) samą odpowiedź. Fallback na czas lokalny, gdyby Graph nie zwrócił znacznika.
            sent_at = do_pracownika(settings, client, chat_id, to_html(text), teraz=teraz)
        # Patrz `wysylka.NIE_POLYKAJ`. Utrata tokenu dotyczy WSZYSTKICH — zatrzymuje cały przebieg,
        # nie jedną osobę. `CiszaError` jest tu praktycznie nieosiągalny (przebieg ma bramkę na
        # wejściu), ale połknięty znaczyłby, że bramka przecieka i nikt się o tym nie dowie.
        except NIE_POLYKAJ:
            raise
        except PrzebiegPrzekroczylCzasError:
            # Z tego samego powodu co wyżej: wyczerpany czas dotyczy PRZEBIEGU, nie tej osoby.
            # Połknięty tutaj degradowałby limit do „każdy z osobna zawiódł" — przebieg brnąłby
            # przez całą listę, raportując sukces, a operator nie dostałby żadnego sygnału.
            raise
        except Exception:
            # Izolacja per-osoba: awaria jednej wysyłki nie blokuje reszty. Bez pendingu, więc
            # kolejny przebieg (nadrobienie/następny termin) spróbuje ponownie tej osoby.
            logger.exception(
                "Nie udało się powiadomić %s — pomijam, ponowię w kolejnym przebiegu",
                etykiety.czlonek(member, settings),
            )
            continue
        # Fallback, gdy Graph nie zwrócił znacznika: realny „teraz", NIE `now`. Przy nadrobieniu
        # (`_catchup_due`) `now` to PRZESZŁY termin — użycie go cofnęłoby watermark przed faktyczny
        # czas wysyłki, przez co listener mógłby wziąć wcześniejszą wiadomość z czatu za odpowiedź.
        sent_iso = sent_at or to_graph_iso(datetime.now(_UTC))
        # Wpis tej osoby leżący pod STARĄ postacią identyfikatora zabieramy, zamiast zostawiać go
        # obok nowego. Bez tego naprawa szukania po tożsamości cofnęłaby się przy pierwszym
        # zapisie: w stanie byłyby DWA wpisy tej samej osoby, a `_dogladaj_nierozstrzygniete`
        # indeksuje stan kluczem wziętym z `pending.member_id`, więc niezgodność klucza z tym
        # polem gubi wpis w kroku 1.6. Niezmiennik „klucz == member_id" zostaje utrzymany,
        # a przejście na świeżą postać jest jednorazowe i dzieje się dopiero PO udanej wysyłce.
        #
        # `pop` BEZ warunku: gdy klucz już jest świeży, zabiera wpis, który i tak zaraz
        # nadpiszemy — ten sam skutek bez gałęzi, której sufit złożoności `run_once` nie uniesie.
        state.pop(klucz, None)
        state[member.user_id] = st.PendingReminder(
            member_id=member.user_id,
            member_name=member.display_name,
            chat_id=chat_id,
            week_start=week_start_iso,
            status=st.AWAITING_REPLY,
            watermark=sent_iso,
            nudged_at=sent_iso,  # niezmienny czas nudge'a — baza dolnej granicy kurtuazji
            proposal=schedule_to_intervals(proposal, tz),
            # Dni już objęte urlopem w Graphie: przy zapisie NIE tworzymy dla nich drugiego
            # timeOff, gdyby pracownik powtórzył je w odpowiedzi (`create_time_off` nie
            # deduplikuje).
            known_time_off_weekdays=sorted(member_off),
        )
        # Zapis PO KAŻDEJ wysyłce: awaria w połowie nie gubi już-wysłanych pendingów (ich odpowiedzi
        # będą czytane), a ponowienie pominie ich dzięki sprawdzeniu wyżej („co najmniej raz").
        st.save_state(settings.state_path, state)
        if _nadpisanie_warte_zgloszenia(existing):
            # Dopiero TERAZ wpis naprawdę przepadł — został zastąpiony i utrwalony. Alert stoi za
            # `save_state`, nie przed wysyłką, bo obiecuje operatorowi szkodę DOKONANĄ i każe ją
            # ręcznie sprawdzić.
            tytul, tresc = _alert_o_nadpisaniu(
                existing, etykiety.czlonek(member, settings), week_start_iso
            )
            operator.alert(settings, tytul, tresc)
        logger.info("Wysłano powiadomienie do %s", etykiety.czlonek(member, settings))
        sent += 1

    if not settings.dry_run:
        st.save_state(settings.state_path, state)  # utrwal prune nawet gdy nic nie wysłano
    logger.info(
        "Przebieg zakończony: %d osób do powiadomienia; %s",
        len(missing),
        "dry-run (nic nie wysłano)" if settings.dry_run else f"wysłano {sent}",
    )
    return missing
