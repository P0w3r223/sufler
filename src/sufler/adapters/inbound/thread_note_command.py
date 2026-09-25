"""Przechwycenie „zapisz to" — notatka z WĄTKU po @wzmiance bota (ADR 0048, F2).

Lustro ``MeetingNoteRouter`` dla innego wyzwalacza: nie komenda ``/notatka`` z argami, lecz
@WZMIANKA bota niosąca dyrektywę ``zapisz to`` z kluczem projektu — po kresce
(``zapisz to | <projekt>``) albo wprost w zdaniu, gdy tekst wzmianki niesie klucz
z rejestru. Świadomie OSOBNY router (nie read-only
``CommandRouter`` — ADR 0017 zostaje read-only), budowany tylko przy włączonej bramce
``enable_thread_note_capture``, konsultowany przez respondera OBOK komend i routera spotkań.

Bezpieczeństwo (ADR 0009 §3 / 0048): ``project`` pochodzi z JAWNEGO argumentu wzmianki (źródło
ZAUFANE), NIGDY z treści wątku — wątek nie przekieruje notatki do cudzego projektu. Miejsce/data z
kontekstu (``source_timestamp`` = Graph timestamp wzmianki, DETERMINISTYCZNY — nie zegar obsługi,
inaczej retry złamałby idempotencję). Zapis idzie przez bramkowany, create-only
``ThreadNoteService`` (ADR 0006). Wyzwalacz wymaga @wzmianki bota (``mentions_bot``) — sama fraza
w treści nie uruchamia zapisu (bot i tak odpowiada na każdą wiadomość człowieka).

Autoryzacja (B2 / ADR 0042): reuse ``MeetingNoteAuthorizer`` — nadawca musi być rozpoznanym
członkiem pionu, rozstrzygane SYNCHRONICZNIE PRZED poborem wątku. Async (B3 / ADR 0043): jak
``/notatka`` — ACK teraz, łańcuch w tle, wynik do wątku; idempotencja (deterministyczny id) czyni
retry bezpiecznym.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sufler.core.errors import NoteAuthorizationError, SuflerError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sufler.core.application.meeting_authz import MeetingNoteAuthorizer
    from sufler.core.application.thread_notes import ThreadNoteService
    from sufler.core.ports.repositories import ProjectsRepository

logger = logging.getLogger(__name__)

# Dyrektywa wyzwalacza (case-insensitive). Składnia: ``@Sufler zapisz to | <projekt>`` albo
# forma naturalna (``zapisz to jako notatkę projektu <klucz>``) — patrz ``_klucz_projektu``.
_DIRECTIVE = "zapisz to"
# Token klucza projektu: rejestr używa kluczy typu ``workmate`` / ``scada-integration``.
_TOKEN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
# Adresy wypadają z szukania klucza: segment ścieżki bywa równy kluczowi projektu
# (``https://github.com/…/workmate/…``), a wklejony link nie jest poleceniem zapisu.
_URL = re.compile(r"https?://\S+")
# Ile kluczy wypisać w podpowiedzi, zanim urwiemy wielokropkiem (podpowiedź, nie katalog).
_USAGE_MAX_KEYS = 8


def _bez_wzmianek(rest: str, wzmianki: Sequence[str]) -> str:
    """Wytnij z tekstu NAZWY @wzmianek — zostaje to, co nadawca napisał jako argument.

    Wycinamy POZYCYJNIE (całe wystąpienie nazwy), nie po wartości tokenu. Różnica jest tu
    wszystkim: nazwa bota brzmi „Virtual Sufler", a klucz projektu to `workmate`, więc
    wykluczanie po wartości zabiłoby zdanie „@Virtual Sufler zapisz to jako notatkę projektu
    workmate" — czyli dokładnie tę formę, dla której ta ścieżka powstała.
    """
    for nazwa in wzmianki:
        if nazwa:
            rest = re.sub(re.escape(nazwa), " ", rest, flags=re.IGNORECASE)
    return rest


def _usage(klucze: Sequence[str]) -> str:
    """Podpowiedź składni z REALNYMI kluczami rejestru.

    Poprzednia wersja podawała jeden przykład na sztywno („np. scada-integration") i był to ten
    sam defekt co martwa obietnica ``/mnt/user/outputs``: klucza nie było ani w rejestrze, ani
    w notatkach, więc każdy, kto skopiował przykład, dostawał odmowę. Router ma rejestr pod ręką
    — ma z niego czytać, a nie powielać literał, który zgnije przy pierwszej zmianie rejestru.

    Z przykładu znika też ``@Sufler`` — i to jest szersza poprawka niż podmiana nazwy.
    Wyzwalacz stoi na ``mentions[]`` z Graph, NIE na tekście, więc wzmianka musi zostać wybrana
    z podpowiedzi Teams; wklejony ``@cokolwiek`` jest zwykłym słowem i nic nie uruchamia.
    Drukowanie jakiejkolwiek nazwy dawało więc przykład NIEKOPIOWALNY — a dodatkowo widniała tam
    nazwa `Sufler`, gdy bot na produkcji nazywa się `Virtual Sufler`. Zdanie mówi teraz, co
    człowiek ma zrobić (wybrać wzmiankę), zamiast pokazywać znaki do przepisania.

    Ostatnie zdanie zyskało powód. Sonda 2026-08-21 po migracji 1.13.0: człowiek napisał
    „Zapisz to, @Virtual Sufler", dostał tę podpowiedź i odczytał ją jako „bot nie zrozumiał".
    Router zachował się POPRAWNIE — bez tego zapisałby wątek pod projekt `workmate`, bo nazwa
    bota niesie klucz rejestru — ale odmowa bez powodu wygląda jak awaria.
    """
    if klucze:
        lista = ", ".join(klucze[:_USAGE_MAX_KEYS])
        if len(klucze) > _USAGE_MAX_KEYS:
            lista += ", …"
        projekt = f"Klucze z rejestru: {lista}."
    else:
        projekt = "Rejestr projektów jest pusty — poproś operatora o dodanie projektu."
    return (
        "Aby zapisać ten wątek jako notatkę, wzmiankuj mnie (wybierz mnie z podpowiedzi Teams) "
        "i podaj projekt:\n"
        "  zapisz to | <projekt>\n"
        "  albo wprost: zapisz to jako notatkę projektu <projekt>\n"
        f"  {projekt} Projekt wskazujesz Ty — treść wątku ani moja nazwa o tym nie decydują."
    )


_ACK = (
    "Przyjąłem — zapisuję ten wątek jako notatkę (streszczam ustalenia). "
    "Wynik odeślę w tym wątku za chwilę."
)
_ASYNC_INTERNAL_ERROR = (
    "Nie udało się zapisać notatki w tle (błąd wewnętrzny). Spróbuj ponownie — "
    "ponowienie jest bezpieczne (notatka nie zostanie zduplikowana)."
)
_BAD_TIMESTAMP = (
    "Nie udało się ustalić daty wątku (brak znacznika czasu wiadomości). Spróbuj ponownie."
)


@dataclass(frozen=True)
class ThreadNoteContext:
    """Kontekst wywołania „zapisz to" z respondera (odpowiednik ``CommandContext`` dla wątku).

    ``external_id`` (``team/channel/root``) — cel poboru wątku i odpowiedzi. ``source_message_id``
    — id wzmianki (klucz idempotencji, ADR 0048 §5). ``source_timestamp`` — Graph ``created``
    wzmianki (deterministyczna data notatki). ``sender_id`` — AAD id nadawcy (autoryzacja B2).
    ``mentions_bot`` — czy wiadomość @wzmiankuje bota (warunek wyzwalacza).
    """

    external_id: str
    source_message_id: str
    source_timestamp: str
    sender_id: str
    mentions_bot: bool
    # NAZWY @wzmianek (``mentions[].mentionText``) — wykluczane z szukania klucza projektu.
    # Addytywne: drzwi bez tego pojęcia (i testy) zostawiają puste.
    mention_texts: tuple[str, ...] = ()


class ThreadNoteRouter:
    """Router „zapisz to": wątek (Graph) → streszczenie → zapis (idempotentny, gated).

    ``dispatch`` zwraca tekst odpowiedzi, gdy wiadomość jest wyzwalaczem „zapisz to" (wzmianka
    bota + dyrektywa), albo ``None`` (to zwykła wiadomość → responder obsłuży ją turą agenta).
    Błędy (nieznany projekt, brak treści, błąd Claude) degradują do CZYTELNEGO komunikatu.
    """

    def __init__(
        self,
        service: ThreadNoteService,
        *,
        projects: ProjectsRepository | None = None,
        authorizer: MeetingNoteAuthorizer | None = None,
        scheduler: Callable[[Callable[[], None]], None] | None = None,
        callback: Callable[[str, str], None] | None = None,
        tz: ZoneInfo | None = None,
    ) -> None:
        self._service = service
        # Strefa drzwi — patrz ``_iso_date``. ``None`` zostawia dawne liczenie w UTC.
        self._tz = tz
        # Rejestr projektów — WYŁĄCZNIE do podpowiedzi i do rozpoznania klucza w formie
        # naturalnej. O tym, czy projekt istnieje, i tak rozstrzyga ``require_project``
        # w serwisie: ta ścieżka niczego nie autoryzuje. ``None`` (ścieżki operatorskie,
        # testy) → podpowiedź bez kluczy i wyłącznie składnia z kreską.
        self._projects = projects
        # Bramka członkostwa (B2 / ADR 0042); ``None`` → bez autoryzacji (operatorskie ścieżki).
        self._authorizer = authorizer
        # Async (B3 / ADR 0043): ``scheduler`` zleca thunk do tła, ``callback`` odsyła wynik do
        # wątku. Tryb async wymaga OBU — inaczej router liczy inline.
        self._scheduler = scheduler
        self._callback = callback

    def dispatch(self, text: str, ctx: ThreadNoteContext) -> str | None:
        # Wyzwalacz wymaga @wzmianki bota: bez niej to zwykła wiadomość (bot odpowie normalną turą).
        if not ctx.mentions_bot:
            return None
        rest = _po_dyrektywie(text)
        if rest is None:
            return None  # wzmianka bez „zapisz to" → normalna tura agenta
        # Rejestr czytamy DOPIERO tu, gdy dyrektywa faktycznie padła. Wyżej statowalibyśmy plik
        # rejestru przy KAŻDEJ wzmiance — także przy zwykłym pytaniu, które router przepuszcza
        # dalej — i przy zepsutym pliku logowali ostrzeżenie w każdej takiej turze.
        klucze = self._klucze()
        project = _klucz_projektu(rest, klucze, ctx.mention_texts)
        if not project:
            # „zapisz to" bez rozpoznanego projektu → podpowiedz składnię REALNYMI kluczami.
            return _usage(sorted(klucze.values()))
        return self._handle(project, ctx)

    def _klucze(self) -> Mapping[str, str]:
        """Mapa ``klucz zmałowany → postać kanoniczna``; bez repozytorium albo przy błędzie pusta.

        Rejestr jest tu wygodą, nie bramką, więc jego awaria ma degradować podpowiedź, a nie
        wywracać wyzwalacz: ze składnią z kreską „zapisz to" działa dalej.
        """
        if self._projects is None:
            return {}
        try:
            return {p.key.lower(): p.key for p in self._projects.all()}
        except Exception:
            logger.warning("Nie udało się odczytać rejestru projektów do podpowiedzi 'zapisz to'")
            return {}

    def _handle(self, project: str, ctx: ThreadNoteContext) -> str:
        try:
            on = _iso_date(ctx.source_timestamp, self._tz)
        except ValueError:
            return _BAD_TIMESTAMP
        # Autoryzacja PRZED poborem wątku (B2 / ADR 0042): nieznany nadawca → odmowa, zero pracy.
        if self._authorizer is not None:
            try:
                self._authorizer.authorize(ctx.sender_id, project=project)
            except NoteAuthorizationError as exc:
                return f"Brak uprawnień do zapisania notatki z wątku: {exc}"
        # Async (B3 / ADR 0043): ACK teraz, łańcuch w tle, wynik do wątku. Autoryzacja już przeszła.
        if self._scheduler is not None and self._callback is not None:
            eid, mid = ctx.external_id, ctx.source_message_id
            self._scheduler(lambda: self._run_and_post(eid, mid, project, on, eid))
            return _ACK
        return self._compose_reply(ctx.external_id, ctx.source_message_id, project, on)

    def _compose_reply(
        self, external_id: str, source_message_id: str, project: str, on: date
    ) -> str:
        """Złóż notatkę i ZWRÓĆ tekst wyniku (zapisana / już była / błąd). Bez I/O drzwi.

        Wspólny rdzeń dla trybu SYNC (zwrot wprost) i ASYNC (zwrot posyłany do wątku). Błędy
        oczekiwane degradują do czytelnego tekstu — wyzwalacz nie wywraca tury ani wątku.
        """
        try:
            outcome = self._service.note_from_thread(
                external_id, source_message_id, project=project, on=on
            )
        except SuflerError as exc:
            # Nieznany projekt, kolizja/odrzucenie zapisu, błąd Claude API — czytelnie.
            return f"Nie udało się zapisać notatki z wątku: {exc}"
        except (ValueError, KeyError) as exc:
            # Błąd pobrania/parsowania wątku z Graph (zły external_id, brak wątku, 4xx).
            return f"Nie udało się pobrać treści wątku: {exc}"
        note = outcome.note
        if note is None:
            # Notatka tej wzmianki już była (idempotencja, ADR 0048) — pobór/Claude pominięte.
            return (
                "✓ Ten wątek był już zapisany wcześniej (idempotencja, ADR 0048).\n"
                f"  id: {outcome.note_id}"
            )
        meta = note.metadata
        return (
            "✓ Notatka z wątku zapisana ('zapisz to', ADR 0048).\n"
            f"  id:         {note.id}\n"
            f"  tytuł:      {meta.title}\n"
            f"  uczestnicy: {', '.join(meta.participants) or '—'}\n"
            f"  decyzje: {len(meta.decisions)} · action items: {len(meta.action_items)} · "
            f"pytania: {len(meta.open_questions)}"
        )

    def _run_and_post(
        self, external_id: str, source_message_id: str, project: str, on: date, target: str
    ) -> None:
        """Zadanie w tle (B3 / ADR 0043): policz wynik i ODEŚLIJ go do wątku ``target``.

        Nic nie połykamy po cichu: nieoczekiwany błąd łańcucha ląduje w logu i mimo to posyłamy
        userowi czytelny komunikat; błąd samej wysyłki też logujemy. ``self._callback`` jest
        niepuste (sprawdzone w ``_handle``).
        """
        assert self._callback is not None  # tryb async: callback zawsze wstrzyknięty
        try:
            text = self._compose_reply(external_id, source_message_id, project, on)
        except Exception:
            logger.exception("Błąd w tle przy zapisie notatki z wątku (wątek %r)", target)
            text = _ASYNC_INTERNAL_ERROR
        try:
            self._callback(target, text)
        except Exception:
            logger.exception("Nie udało się odesłać wyniku 'zapisz to' do wątku %r", target)


def _po_dyrektywie(text: str) -> str | None:
    """Tekst ZA dyrektywą „zapisz to" albo ``None``, gdy dyrektywy w wiadomości nie ma.

    Wydzielone z rozpoznania klucza, żeby wykrycie dyrektywy było TANIE: router konsultuje
    każdą wzmiankę bota, a rejestr projektów ma czytać wyłącznie ta garstka, która niesie
    „zapisz to".
    """
    idx = text.lower().find(_DIRECTIVE)
    return None if idx == -1 else text[idx + len(_DIRECTIVE) :]


def _klucz_projektu(rest: str, klucze: Mapping[str, str], wzmianki: Sequence[str]) -> str:
    """Klucz projektu z tekstu za dyrektywą; ``""`` → nie da się ustalić jednoznacznie.

    Dwie drogi, ta sama gwarancja. Z kreską: wszystko po ``|`` — tak brzmi ADR 0048 i tak stoi
    w dokumentacji; o ISTNIENIU projektu i tak rozstrzyga ``require_project`` w serwisie, nie
    ten parser. Bez kreski: szukamy tokenu, który JEST kluczem rejestru; przy zerze albo dwóch
    trafieniach oddajemy ``""``, bo zgadywanie miejsca zapisu jest gorsze niż pytanie.

    Rozluźnienie NIE osłabia niczego, co chroni ADR 0009 §3. Gwarancją jest tam POCHODZENIE
    klucza — z tekstu wzmianki nadawcy, nigdy z treści wątku — a zdanie „zapisz to jako notatkę
    projektu workmate" też jest tekstem wzmianki. Sztywna kreska chroniła prostotę parsera,
    nie użytkownika: na demo 2026-08-21 człowiek napisał formę naturalną i dostał odmowę.

    Trzy rzeczy WYPADAJĄ z szukania, bo żadna nie jest argumentem nadawcy:

    1. **Nazwy @wzmianek.** ``_strip_html`` spłaszcza ``<at>Virtual Sufler</at>`` do gołej
       nazwy, a `workmate` jest kluczem rejestru — bez tego „Zapisz to, @Virtual Sufler"
       zapisywało wątek pod projekt `workmate` PO CICHU, a notatki `-thr-` są niezmienne.
    2. **Adresy.** Wklejony link, którego segment ścieżki równa się kluczowi, nie jest poleceniem.
    3. **Wielkość liter.** Dopasowanie idzie po kluczu zmałowanym (``require_project`` też
       porównuje bez względu na wielkość), a zwracamy postać KANONICZNĄ z rejestru — inaczej
       klucz z wersalikiem dawałoby się wypisać w podpowiedzi, ale nie dałoby się go użyć.

    Kreska ma jeszcze jedno ustępstwo: gdy tekst po ``|`` NIE jest kluczem rejestru, a zdanie
    niesie dokładnie jeden klucz, wygrywa zdanie. Podpowiedź reklamuje teraz formę naturalną,
    a wiadomości Teams rutynowo niosą ``|`` (tabele, kod, adresy) — bez tego ustępstwa
    „zapisz to jako notatkę projektu workmate | dzięki" ginęło na „nieznanym projekcie".
    """
    if "|" in rest:
        po_kresce = rest.split("|", 1)[1].strip()
        if not klucze or po_kresce.lower() in klucze:
            return klucze.get(po_kresce.lower(), po_kresce)
        # Kreska trafiła w tekst, nie w argument. Gdy zdanie nie rozstrzyga, oddajemy to, co
        # człowiek NAPISAŁ po kresce — komunikat o nieznanym projekcie ma go cytować.
        return _ze_zdania(rest, klucze, wzmianki) or po_kresce
    return _ze_zdania(rest, klucze, wzmianki)


def _ze_zdania(rest: str, klucze: Mapping[str, str], wzmianki: Sequence[str]) -> str:
    """Jedyny klucz rejestru w zdaniu (po odjęciu wzmianek i adresów) albo ``""``."""
    tekst = _URL.sub(" ", _bez_wzmianek(rest, wzmianki)).lower()
    trafienia = {klucze[token] for token in _TOKEN.findall(tekst) if token in klucze}
    return trafienia.pop() if len(trafienia) == 1 else ""


def _iso_date(timestamp: str, tz: ZoneInfo | None = None) -> date:
    """Data KALENDARZOWA znacznika z Graph w strefie drzwi; pusty/zły → ``ValueError``.

    Konwersja jest tu istotą, nie kosmetyką: Graph podaje czas w UTC, więc „zapisz to" wysłane
    o 23:30 czasu warszawskiego wypadało na POPRZEDNI dzień. Data wchodzi do ``build_note_id``,
    czyli notatka dostawała zarówno inny dzień w treści, jak i inny identyfikator — a notatki
    wątkowe są niezmienne, więc korekta wymaga założenia nowej. ``None`` (ścieżki operatorskie
    i testy) zachowuje dawne zachowanie, czyli datę w UTC.
    """
    if not timestamp:
        raise ValueError("pusty znacznik czasu wiadomości")
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return moment.astimezone(tz).date() if tz is not None else moment.date()
