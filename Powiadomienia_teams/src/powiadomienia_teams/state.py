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
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SUFIKS_KOPII = ".bak"

# statusy obiegu
AWAITING_REPLY = "awaiting_reply"
AWAITING_CONFIRM = "awaiting_confirm"
# Zapis do Shifts ROZPOCZĘTY, ale niepotwierdzony. Utrwalany PRZED wysłaniem wpisów (cena
# semantyki „co najwyżej raz"), więc wpis, który TU utknął, jest jawną dziurą do naprawy przez
# człowieka: albo zapis padł, albo proces zginął w oknie między commitem a POST-em. NIE jest
# ponawiany automatycznie — ponowienie mogłoby zdublować wpisy w grafiku klienta.
APPLYING = "applying"
APPLIED = "applied"
DECLINED = "declined"
EXPIRED = "expired"  # minęło okno odpowiedzi bez reakcji pracownika (koniec odpytywania)
SELF_FILLED = "self_filled"  # pracownik sam uzupełnił grafik w Shifts (koniec odpytywania)

# Statusy, po których obieg nie zrobi już kroku sam z siebie. ``APPLYING`` jest tu mimo że opisuje
# zapis NIEPOTWIERDZONY: ponowienie mogłoby zdublować wpisy w grafiku, więc dalszy ruch należy do
# człowieka.
#
# Zbiór mieszka TU, obok samych statusów, a nie w ``reminders.lifecycle``, bo mają go dwaj różni
# odbiorcy o różnych celach: sprzątanie stanu (``prune_terminal``) i wygaszanie pamięci rozmowy
# przy zapisie (``_do_zapisu`` niżej). Dwie kopie tej listy rozjechałyby się przy pierwszym nowym
# statusie, a rozjazd znaczyłby, że surowe wiadomości pracownika przeżywają domknięcie tematu.
TERMINALNE = frozenset({APPLYING, APPLIED, DECLINED, EXPIRED, SELF_FILLED})


@dataclass
class PendingReminder:
    member_id: str
    # `repr=False` na polach z danymi osobowymi — tak jak klucz API w `Settings` (N28).
    # `logger.debug("%s", pending)` przy diagnozie wygląda niewinnie i nie psuje żadnego testu,
    # a wypisywałby nazwisko i dosłowne wiadomości pracownika do logu podlegającego rotacji.
    # Ten sam `repr` trafia też do tracebacków i do bibliotek, których nie kontrolujemy.
    member_name: str = field(repr=False)
    chat_id: str
    week_start: str  # ISO date (poniedziałek przyszłego tygodnia)
    status: str
    watermark: str = ""  # createdDateTime ostatniej przetworzonej wiadomości pracownika
    # createdDateTime nudge'a (niezmienny). Od 0.2.13 NIE jest bazą terminu — ten liczy się od
    # `week_start` — a wyznacza dolną granicę kurtuazji (`lifecycle.termin_odpowiedzi`).
    nudged_at: str = ""
    proposal: list[dict[str, Any]] = field(default_factory=list)  # gotowiec z zeszłego tygodnia
    resolved: list[dict[str, Any]] = field(default_factory=list)  # grafik ustalony po odpowiedzi
    # Czas wolny ustalony po odpowiedzi: [{weekday, reason_id, reason_name}] (powód rozstrzygnięty).
    # `repr=False` z tego samego powodu co przy `member_name`, ale stawka jest wyższa: `reason_name`
    # bywa „chorobowe" albo „urlop rodzicielski" (`agent/schema.py::POWODY_WOLNEGO`), czyli
    # KATEGORIĄ SZCZEGÓLNĄ. Ochrona, którą A10 dało nazwisku i pamięci rozmowy, to pole omijała.
    resolved_time_off: list[dict[str, Any]] = field(default_factory=list, repr=False)
    # Dni (0=pon…6=nd) już objęte urlopem w Graphie w chwili nudge'a. Przy zapisie pomijamy je,
    # by nie utworzyć DRUGIEGO timeOff, gdyby pracownik zgłosił je ponownie (create_time_off nie
    # deduplikuje). Pole opcjonalne — stare pliki stanu bez niego dostają pustą listę.
    known_time_off_weekdays: list[int] = field(default_factory=list)
    # Nieudane próby obsługi od ostatniego UDANEGO commitu (licznik zeruje wyłącznie `_commit`,
    # więc obejmuje też kolejne różne wiadomości, jeśli żadna nie doszła do końca).
    # Chroni przed zapętleniem na błędzie deterministycznym (patrz ``runtime.listener._record_failure``).
    fail_count: int = 0
    # Pamięć rozmowy: WYŁĄCZNIE wiadomości pracownika (nie bota), od najstarszej do najnowszej,
    # przycięta do ostatnich 10 (``replies.MEMORY_CAP``). Kontekst wieloturowy dla interpretera.
    # Pole opcjonalne — stare pliki stanu bez niego dostają pustą listę.
    employee_memory: list[str] = field(default_factory=list, repr=False)
    # Kotwica STAŁEGO okna pamięci (``replies.MEMORY_WINDOW``): createdDateTime PIERWSZEJ wiadomości
    # w pamięci. Osobne pole (a nie ``employee_memory[0]``), by przycięcie do 10 NIE przesuwało okna
    # — inaczej okno stałoby się kroczące zamiast liczonym od pierwszej interakcji.
    memory_started_at: str = ""
    # createdDateTime OSTATNIEJ wiadomości, którą bot wysłał do tej osoby w otwartym temacie
    # (prośba o potwierdzenie, prośba o doprecyzowanie). Wyznacza dolną granicę kurtuazji
    # (``lifecycle.termin_odpowiedzi``): tematu nie wolno zamknąć zaraz po tym, jak bot o coś
    # poprosił. Bez tego pola po przestoju sięgającym za termin bot wysyłał prośbę
    # i cykl później — 10 sekund — zamykał temat komunikatem „nie doczekałem się potwierdzenia",
    # a pending stawał się terminalny, więc »tak« pracownika nie było już nigdy czytane.
    # Pole opcjonalne — stare pliki stanu bez niego dostają pusty napis i zachowują się jak dotąd.
    bot_last_message_at: str = ""
    # Czy OSTATNIĄ rzeczą, o którą bot poprosił, było potwierdzenie („napisz »tak«"). To jedyna
    # przesłanka uprawniająca szybką ścieżkę zapisu (`listener` + `is_pure_affirmation`).
    #
    # Osobne pole, a nie sam status `AWAITING_CONFIRM`, bo status ma DRUGIEGO konsumenta:
    # rozstrzyga, KTÓRY komunikat domknięcia dostanie pracownik. Po wymianie „propozycja → pytanie
    # o cokolwiek → prośba o doprecyzowanie" zapis musi być zablokowany (samo późniejsze „tak"
    # nie wiadomo czego dotyczy), ale cofnięcie statusu na `AWAITING_REPLY` kazałoby przy
    # wygaśnięciu wysłać „Nie dostałem odpowiedzi" — czyli zarzucić milczenie komuś, kto napisał.
    # `messages.NO_CONFIRM_TEXT` istnieje dokładnie po to, żeby tego nie robić.
    #
    # Pole opcjonalne: stary plik stanu bez niego dostaje `False`, więc rozmowa otwarta w chwili
    # podmiany obrazu przechodzi przez reinterpretację zamiast szybkiej ścieżki. To kierunek
    # bezpieczny — cena jest jedna prośba o potwierdzenie więcej, a nie zapis, którego pracownik
    # nie widział.
    awaiting_yes: bool = False


_FIELDS = {f.name for f in fields(PendingReminder)}


class StateUnreadableError(RuntimeError):
    """Plik stanu ISTNIEJE, ale nie dało się go odczytać — treść jest NIEZNANA, nie pusta.

    Wydzielone z „uszkodzony plik", bo skutki są przeciwne. Uszkodzony JSON znaczy „ten plik jest
    bezużyteczny" i wolno sięgnąć po kopię. Błąd I/O na istniejącym pliku (awaria dysku, EACCES po
    zmianie właściciela wolumenu przy podmianie obrazu) znaczy „nie wiem, co tam jest" — a start
    z pustego stanu oznacza wtedy prośby wysłane DRUGI RAZ do wszystkich i zerwanie wszystkich
    otwartych rozmów. Pierwszy ``save_state`` nadpisałby przy tym oryginał, bo o powodzeniu
    ``os.replace`` decydują prawa katalogu, nie pliku. Lepiej paść głośno i dać się zrestartować.
    """


def sciezka_kopii(path: Path) -> Path:
    """Ścieżka kopii zapasowej stanu — JEDNO miejsce, w którym powstaje ta nazwa.

    Wcześniej to samo wyrażenie stało w dwóch miejscach (odczyt awaryjny i zapis kopii), a od
    A4 potrzebuje jej też diagnostyka. Trzy kopie tego samego sklejania rozjechałyby się przy
    pierwszej zmianie sufiksu, a rozjazd znaczyłby, że ``load_state`` szuka kopii tam, gdzie jej
    nikt nie zapisuje — czyli że jedyne zabezpieczenie przed drugą wysyłką do wszystkich milczy.
    """
    return path.with_suffix(path.suffix + _SUFIKS_KOPII)


def _wczytaj(path: Path, *, cicho: bool = False) -> dict[str, PendingReminder] | None:
    """Odczytaj jeden plik stanu. ``None`` = nie da się użyć (brak albo uszkodzona treść).

    Rzuca ``StateUnreadableError``, gdy plik istnieje, ale nie daje się przeczytać — patrz tam.

    ``cicho`` wyłącza ostrzeżenia dla wołającego, który tylko PYTA o czytelność
    (``daje_sie_odczytac``). Bez tego jeden uszkodzony plik dawał w logu dwa identyczne
    ostrzeżenia — a dwa ostrzeżenia czytają się jak dwie awarie.
    """
    ostrzez = (lambda *_: None) if cicho else logger.warning
    if not path.exists():
        return None
    try:
        tresc = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateUnreadableError(f"Nie udało się odczytać pliku stanu {path}: {exc}") from exc
    try:
        raw = json.loads(tresc)
    except json.JSONDecodeError:
        ostrzez("Uszkodzony plik stanu %s", path)
        return None
    if not isinstance(raw, dict):
        ostrzez("Plik stanu %s nie jest obiektem", path)
        return None
    # Ignoruj nieznane pola (dryf schematu) i POMIJAJ pojedyncze nieczytelne wpisy zamiast kłaść
    # cały nasłuch — zgodnie z deklarowaną tolerancyjnością odczytu.
    #
    # Pomijamy też pola z wartością ``null``: przekazane do konstruktora nadpisałyby domyślną
    # wartość (``field(default_factory=list)`` / ``""``) jawnym ``None``, a warstwa wyżej zakłada, że
    # ``employee_memory`` jest listą, a znaczniki czasu napisem. ``employee_memory=null`` (ręczna
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
            ostrzez("Pomijam nieczytelny wpis stanu %r w %s", key, path)
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
    kopia = sciezka_kopii(path)
    stan = _wczytaj(kopia)
    if stan is not None:
        logger.warning("Odtworzono stan z kopii %s (%d wpisów)", kopia, len(stan))
        return stan
    return {}


def daje_sie_odczytac(path: Path) -> bool:
    """Czy ``load_state`` przyjmie TEN plik — to samo kryterium, a nie jego kopia.

    Potrzebne diagnostyce (``--stan``), która musi odróżnić „stan jest pusty" od „stanu nie da się
    odczytać". Skutki są przeciwne: pierwsze znaczy „obieg nie ma nic w toku", drugie znaczy, że
    najbliższy przebieg uzna wszystkich za nienagabywanych i wyśle prośby DRUGI RAZ.

    Kryterium wolno mieć wyłącznie jedno. Własna próba ``json.loads`` w module raportu wyglądałaby
    identycznie i rozjechałaby się przy pierwszej zmianie tolerancji odczytu — a rozjazd znaczyłby
    raport twierdzący „plik czytelny" o pliku, którego usługa nie wczytała.
    """
    try:
        return _wczytaj(path, cicho=True) is not None
    except StateUnreadableError:
        return False


class StateWriteError(RuntimeError):
    """Nie udało się utrwalić stanu — awaria NIETRANSIENTNA, nie wolno jej ponawiać przebiegiem.

    ``run_once`` zapisuje stan po KAŻDEJ wysyłce, więc semantyka jest „co najmniej raz". Gdy zapis
    padnie (pełny dysk, wolumen tylko-do-odczytu), wyjątek wychodzi z całego przebiegu, a ponowienie
    startuje od ``load_state``, który nie widzi już wysłanych pendingów — czyli ta sama osoba
    dostaje tę samą prośbę w każdej próbie. Przyczyna jest zwykle trwała, więc trzy próby to trzy
    identyczne wiadomości u pracownika. Orkiestracja wyłącza ten błąd z pętli ponowień.
    """


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
    try:
        _zapisz(path, state)
    except OSError as exc:
        # Awaria magazynu stanu NIE jest transientna — patrz `StateWriteError`.
        raise StateWriteError(f"Nie udało się zapisać stanu {path}: {exc}") from exc


def _wygaszone() -> dict[str, Any]:
    """Pola gaszone w chwili domknięcia tematu — JEDNO źródło dla pliku głównego i dla kopii.

    Rozjazd tych dwóch miejsc znaczy ochronę zależną od tego, który plik ktoś otworzy, więc nie
    wolno mu być kwestią pamięci autora: dopisanie pola tutaj obowiązuje w obu przejściach naraz.
    Do 0.2.16 były to dwie listy pisane ręcznie i właśnie tak się rozjechały (``.bak`` trzymał treść
    rozmów domkniętych do najbliższego zapisu, czyli po zatrzymaniu usługi bezterminowo).

    **Funkcja, a nie stała** (0.2.17): stała rozdawałaby przez ``dict.update`` TEN SAM obiekt listy
    każdemu wpisowi terminalnemu i samej sobie. Dziś nieszkodliwe — oba przejścia idą prosto
    w ``json.dumps`` — ale komentarz wyżej wprost zaprasza do dopisywania pól, a jeden ``append``
    na wyniku ``_do_zapisu`` skaziłby wzorzec na całe życie procesu. ``TERMINALNE`` obok jest
    ``frozenset`` z tego samego powodu.

    **Czego tu NIE ma i dlaczego** (ustalenie przeglądu 0.2.18): ``awaiting_yes`` obowiązuje w obu
    przejściach, ale jest gaszony w każdym z nich osobno. To lista pól PRYWATNOŚCIOWYCH — cytuje ją
    w tej roli ``docs/dane-osobowe.md`` — a ``awaiting_yes`` jest bramką nieodwracalnego zapisu
    (**N38**), nie danymi o osobie; wpisany tutaj zniknąłby z tamtej listy i z tamtego uzasadnienia.
    Cena rozdziału jest jawna: obie strony pilnuje sonda parzystości i zdjęcie którejkolwiek daje
    test czerwony, ale trzecie pole o tym kształcie należy dopisać do sondy, a nie do tej funkcji.
    """
    return {
        "employee_memory": [],
        "memory_started_at": "",
        "resolved_time_off": [],
    }


def _do_zapisu(pending: PendingReminder) -> dict[str, Any]:
    """Postać wpisu do utrwalenia — temat domknięty NIE zabiera ze sobą danych o osobie.

    ``employee_memory`` to dosłowne zdania pracownika, a prompt wprost zachęca do pisania o
    powodach nieobecności (L4, opieka). Dopóki rozmowa trwa, ta pamięć jest potrzebna: bez niej
    druga poprawka kasuje pierwszą. W chwili domknięcia tematu przestaje mieć jakiegokolwiek
    odbiorcę — nic w kodzie jej już nie czyta — a zostaje na wolumenie do najbliższego
    ``prune_terminal``, czyli nawet tygodniami.

    ``resolved_time_off`` dołączyło do wygaszania w 0.2.16 i niesie stawkę WYŻSZĄ niż pamięć
    rozmowy: to lista ``[{weekday, reason_id, reason_name}]``, a ``reason_name`` bywa „chorobowe"
    albo „urlop rodzicielski" — kategoria szczególna. Wygaszanie pamięci usunęło ten kształt dla
    TREŚCI wiadomości i zostawiło go w polu obok, przez które ta sama informacja przechodzi
    w postaci uporządkowanej. Czytelnicy tego pola (``reminders/propose.baza_interpretacji``,
    ``runtime/listener``) pracują wyłącznie na wpisach OTWARTYCH.

    Wygaszamy więc przy ZAPISIE, a nie w miejscach ustawiania statusu: tych jest pięć i każde
    kolejne domknięcie musiałoby o tym pamiętać. Tu jest jedno przejście, przez które przechodzi
    każdy stan trafiający na dysk.

    Dwa skutki poza prywatnością. Wpis terminalny zostaje strażnikiem idempotencji (potrzebuje
    tylko ``member_id``, ``week_start`` i statusu), więc nic nie traci. I znika trwała powierzchnia
    wstrzyknięcia: treść, która trafiła do pamięci, wracała do modelu w każdej kolejnej turze
    i przeżywała restarty.

    **Razem z treścią zamykamy szybką ścieżkę zapisu** (``awaiting_yes``, **N38**) — od 0.2.17,
    bo dotąd robił to wyłącznie zapis kopii, a plik główny polegał na tym, że wszystkie pięć ścieżek
    domykających przepuści flagę przez ``_commit``. To jest dokładnie ta asymetria, na której N38
    raz już padł (regresja ``953cd6d``): strażnik stał, a przedmiot ochrony wyjechał spod niego przy
    przesunięciu warunku. Argument z akapitu wyżej — „tych jest pięć i każde kolejne domknięcie
    musiałoby o tym pamiętać" — obowiązuje tu tak samo jak dla pól treści.
    """
    dane = asdict(pending)
    if pending.status in TERMINALNE:
        dane.update(_wygaszone())
        dane["awaiting_yes"] = False
    return dane


def _zapisz_tresc(sciezka: Path, tresc: str) -> None:
    """Zapisz plik utworzony OD RAZU z prawami 0600 i wypchnięty na dysk.

    Wzorem ``graph/auth.py``: ``open("w")`` zakłada plik z umask (zwykle 0644), więc treść
    istniałaby przez moment czytelna dla całego systemu, a ``chmod`` po fakcie tego okna nie
    zamyka. W pliku stanu leżą surowe wiadomości pracowników z OTWARTYCH rozmów, a prompt wprost
    zachęca do pisania o powodach nieobecności — ten sam kod traktował refresh-token jak sekret,
    a te dane jak plik tymczasowy.

    Na Windows ``mode`` jest ignorowane (NTFS używa ACL); tam o dostępie decyduje katalog.
    """
    deskryptor = os.open(sciezka, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(deskryptor, "w", encoding="utf-8") as plik:
        plik.write(tresc)
        plik.flush()
        os.fsync(plik.fileno())  # dane NA DYSKU, nie tylko w buforze systemu


def _zapisz_kopie(path: Path, domkniete: dict[str, PendingReminder]) -> None:
    """Zapisz kopię POPRZEDNIEGO stanu, DOMYKAJĄC w niej rozmowy domknięte w NOWYM stanie.

    Kopia istnieje po to, żeby uszkodzony plik stanu nie oznaczał próśb wysłanych drugi raz do
    wszystkich, i to się nie zmienia. Zmienia się jedno: nie zabiera ze sobą surowych wiadomości
    ani rozstrzygniętych powodów nieobecności z rozmów, które właśnie się domknęły. Bez tego
    wygaszanie było prawdziwe wyłącznie dla ``stan.json`` — ``.bak`` trzymał treść do najbliższego
    zapisu, czyli po zatrzymaniu usługi bezterminowo (zweryfikowane, nie wywnioskowane).

    Lista pól gaszonych tutaj musi nadążać za ``_do_zapisu``; pilnuje tego
    ``tests/test_obieg_stan.py`` sondą porównującą oba miejsca na tym samym wpisie.

    Odsiewamy po kluczach z NOWEGO stanu, bo poprzedni plik nie ma skąd wiedzieć, że rozmowa się
    skończyła — w nim status jest jeszcze otwarty.

    **I dlatego przepisujemy też sam status.** Do 0.2.16 kopia gasiła TREŚĆ, zostawiając wpisowi
    status otwarty — a to jest półuzgodnienie udające całe: po odtworzeniu wpis mówił
    ``awaiting_confirm``, trzymał ustalone GODZINY i nie miał już ustalonego DNIA WOLNEGO, więc
    ``propose.baza_interpretacji`` budowała kolejną poprawkę na uzgodnieniu, z którego urlop
    zniknął. Do tego zostawało ``awaiting_yes``, czyli otwarta szybka ścieżka: samo „tak" szło
    w nieodwracalny zapis (**N38**). Skoro treść gasimy na podstawie NOWEGO stanu, to na tej samej
    podstawie musi jechać status — inaczej kopia opisuje sytuację, której nigdy nie było.

    Wpis domknięty w kopii zostaje pełnoprawnym strażnikiem idempotencji (**N15**): ``member_id``,
    ``week_start`` i status to wszystko, czego potrzebuje. Odtworzenie po awarii nie wznawia więc
    rozmowy, która u klienta jest już zamknięta — a to ono, nie utrata pamięci, było tu realną ceną.

    **Warunek tożsamości rozmowy (0.2.17): ten sam ``week_start``.** Kluczem stanu jest samo
    ``member_id`` (dług policzony w §5.1 architektury), więc wpis pod tym kluczem w POPRZEDNIM
    pliku może dotyczyć innego tygodnia niż wpis domknięty w nowym — ``nudge.run_once`` nadpisuje
    otwartą rozmowę z poprzedniego tygodnia. Dziś to nieosiągalne, bo wpis na nowy tydzień zawsze
    przechodzi najpierw przez zapis ze statusem otwartym, ale własność wywnioskowana z kolejności
    zapisów przestaje być własnością w chwili, gdy ktoś tę kolejność zmieni. Przy rozjeździe
    zostawiamy wpis nietknięty i to jest bezpieczne w obie strony: kopia powstaje z pliku GŁÓWNEGO,
    a tam rozmowa domknięta wcześniej ma już wygaszoną treść — więc pomijamy wpis, który albo trwa
    (i wtedy nie wolno go domykać), albo jest czysty od poprzedniego zapisu.

    Best-effort, tak jak dotąd — brak kopii jest lepszy niż zablokowany zapis stanu, bo bez zapisu
    grozi podwójna wysyłka. Poprzedniej wersji, której nie da się sparsować, NIE kopiujemy wcale:
    ``load_state`` i tak by jej nie przyjął, więc jedyne, co by niosła, to treść do odsiania.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as blad:
        logger.warning("Pomijam kopię stanu — poprzedniej wersji nie da się odczytać (%s)", blad)
        return
    if not isinstance(raw, dict):
        logger.warning("Pomijam kopię stanu — poprzednia wersja nie jest obiektem")
        return
    for klucz, pending in domkniete.items():
        wpis = raw.get(klucz)
        if not isinstance(wpis, dict):
            continue
        if wpis.get("week_start") != pending.week_start:
            logger.info(
                "Kopia stanu: wpis %r dotyczy innego tygodnia niż domknięta rozmowa — zostawiam",
                klucz,
            )
            continue
        wpis.update(_wygaszone())
        wpis["status"] = pending.status
        wpis["awaiting_yes"] = False
    kopia = sciezka_kopii(path)
    tmp = kopia.with_suffix(kopia.suffix + ".tmp")
    try:
        _zapisz_tresc(tmp, json.dumps(raw, ensure_ascii=False, indent=2))
        os.replace(tmp, kopia)  # podmiana JEDNĄ operacją, jak przy pliku głównym
    except OSError:
        logger.warning("Nie udało się utworzyć kopii stanu %s", kopia)


def _zapisz(path: Path, state: dict[str, PendingReminder]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: _do_zapisu(value) for key, value in state.items()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    _zapisz_tresc(tmp, json.dumps(payload, ensure_ascii=False, indent=2))
    if path.exists():
        _zapisz_kopie(path, {k: v for k, v in state.items() if v.status in TERMINALNE})
    os.replace(tmp, path)  # JEDYNA operacja na `path` — brak okna bez pliku stanu
    # `fsync` na pliku utrwala DANE, ale nie wpis katalogowy powstały przy `os.replace`. Bez tego
    # nagła utrata zasilania potrafi cofnąć podmianę mimo „udanego" zapisu — czyli jedna obsłużona
    # odpowiedź wraca. Best-effort: na Windows katalogu nie da się otworzyć do fsync.
    try:
        katalog = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(katalog)
        finally:
            os.close(katalog)
    except OSError:
        pass
