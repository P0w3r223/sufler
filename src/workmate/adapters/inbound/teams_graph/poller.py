"""Pętla pollingu kanałów Teams (tryb delegowany) na wstrzykniętym porcie Graph.

Wolna od ``httpx``/``msal``: klient Graph (port ``GraphChannelClient``) i handler są
wstrzykiwane, więc pełną pętlę testujemy atrapą portu bez sieci. Wszystkie decyzje
(co nowe, watermark per wątek, dedup, self-skip) delegujemy do czystej
``selection.plan_channel`` — tu zostaje tylko orkiestracja I/O i utrwalanie stanu.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from workmate.adapters.inbound.teams_graph import selection
from workmate.adapters.inbound.teams_graph.selection import ChannelMessage, ReplyPolicy

if TYPE_CHECKING:
    from workmate.adapters.inbound.teams_graph.attachments import AttachmentMaterializer

logger = logging.getLogger(__name__)

# Górna granica listy dedup (id odpisanych wiadomości) — chroni przed nieskończonym wzrostem.
_REPLIED_CAP = 500
# Ile razy wolno PODEJŚĆ do jednej wiadomości, zanim ją odpuścimy (ADR 0069). Dwa, bo to najmniejsza
# wartość dająca obie potrzebne własności: jedno ponowienie ratuje błąd przejściowy (503 na
# załączniku, timeout LLM, 429 przy wysyłce), a skończona granica zamyka pętlę restartów, gdy
# obsługa ubija proces. Wyżej nie idziemy: każde podejście do wiadomości-bomby to kolejny
# restart CAŁYCH drzwi, więc cena rośnie liniowo, a szansa powodzenia nie.
_MAX_ATTEMPTS = 2
# Backstop rozmiaru licznika prób — patrz ``_prune_attempts``.
_ATTEMPTS_CAP = 500
# Przerwa między kanałami w jednej rundzie — respektuje limit zapytań na kanał.
_INTER_CHANNEL_SLEEP_S = 1
# Nazwa drzwi w kwarantannie wiadomości (klucz idempotencji obok id wiadomości).
_DOOR = "teams_graph"
# Powód w kwarantannie, gdy porzucamy wiadomość, o której PORAŻCE nic nie wiemy: licznik prób
# jest trwały, a powód ostatniej porażki żyje w pamięci procesu. Tak wygląda ścieżka, dla której
# licznik w ogóle powstał — obsługa ubiła proces, więc żaden ``except`` nie zdążył nic zapisać.
_NO_REASON = "brak zapisanego powodu — proces nie dotrwał do obsługi wyjątku albo został wznowiony"
# Ile znaków pierwszej linii komunikatu wyjątku wpuszczamy do powodu — patrz ``_failure_reason``.
_MAX_REASON_HEAD = 200


def _utcnow() -> datetime:
    """Bieżąca chwila jako aware UTC — spójna z parsowaniem znaczników Graph (selection)."""
    return datetime.now(UTC)


def _status_of(exc: BaseException) -> int | None:
    """Kod HTTP z wyjątku klienta Graph (duck typing — bez zależności od httpx w pętli)."""
    return getattr(getattr(exc, "response", None), "status_code", None)


def _failure_reason(exc: BaseException) -> str:
    """Powód porażki do kwarantanny: TYP wyjątku + pierwsza linia komunikatu, przycięta.

    ``repr(exc)`` niósł cały ładunek wyjątku, a potok respondera potrafi podnieść cokolwiek —
    ``ValidationError`` pydantica wypisuje ``input_value``, czyli fragment TREŚCI rozmówcy, a
    magazyn deklaruje wprost, że treści nie trzyma. Sufit znaków w adapterze ogranicza rozmiar,
    nie rodzaj, więc zawężamy u ŹRÓDŁA: typ mówi operatorowi, co się stało, pierwsza linia
    doprecyzowuje. To REDUKCJA ekspozycji, nie granica — komunikat wyjątku bywa sklejany z
    danymi wejściowymi i żadna reguła tego nie zamknie (twarda reguła 4).
    """
    linie = str(exc).strip().splitlines()
    head = linie[0][:_MAX_REASON_HEAD] if linie else ""
    return f"{type(exc).__name__}: {head}" if head else type(exc).__name__


def _is_permanent(exc: BaseException) -> bool:
    """Czy błąd wysyłki jest TRWAŁY (4xx poza 429) — nie ma sensu go ponawiać."""
    status = _status_of(exc)
    return status is not None and 400 <= status < 500 and status != 429


class GraphChannelClient(Protocol):
    """Port drzwi na Microsoft Graph — tylko to, czego potrzebuje pętla pollingu.

    Konkretną implementację (``graph.HttpxGraphChannelClient``) wstrzykuje ``app.py``;
    testy podają atrapę spełniającą ten protokół strukturalnie.
    """

    async def refresh_auth(self) -> None: ...

    async def get_me_id(self) -> str: ...

    async def list_root_messages(
        self, team_id: str, channel_id: str, *, top: int
    ) -> list[dict[str, Any]]: ...

    async def list_replies(
        self, team_id: str, channel_id: str, root_id: str, *, top: int
    ) -> list[dict[str, Any]]: ...

    async def post_reply(self, team_id: str, channel_id: str, root_id: str, text: str) -> None: ...

    async def get_hosted_content(
        self, team_id: str, channel_id: str, root_id: str, message_id: str, hosted_id: str
    ) -> bytes: ...

    async def download_shared_url(self, url: str) -> bytes: ...

    async def download_public_url(self, url: str) -> bytes: ...


class MessageDeadLetterStore(Protocol):
    """Port kwarantanny wiadomości PORZUCONYCH przez drzwi (ADR 0069).

    Deklarowany TU, obok ``GraphChannelClient``, bo to potrzeba tej pętli, a nie rdzenia:
    porzucenie wiadomości jest własnością drzwi wejściowych. Implementację
    (``SqliteInboundDeadLetterStore``) wstrzykuje ``app.py``; testy podają atrapę.

    Wpis MUSI być idempotentny po ``(door, message_id)`` — po restarcie ta sama wiadomość
    potrafi trafić tu ponownie, a pierwszy powód i pierwszy czas mają przeżyć.
    """

    def record(
        self,
        *,
        door: str,
        message_id: str,
        channel: str,
        thread_root_id: str,
        sender: str,
        reason: str,
        attempts: int,
    ) -> None: ...


HandleMessage = Callable[[ChannelMessage, str], Awaitable[str | None]]


class ChannelPoller:
    """Nasłuch kanałów Teams (delegowany Graph). Decyzje deleguje do ``selection``.

    ``state`` to mutowalny słownik utrwalany przez ``persist`` po każdej rundzie; jego
    kształt inicjuje ``_seed`` (per kanał: ``since_roots`` + ``threads``; globalnie:
    ``replied`` i ``attempts``). Zegar (``clock``) jest wstrzykiwalny, żeby testy sterowały
    czasem (eksmisja martwych wątków).
    """

    def __init__(
        self,
        client: GraphChannelClient,
        handle: HandleMessage,
        *,
        watch: tuple[tuple[str, str], ...],
        state: dict[str, Any],
        persist: Callable[[dict[str, Any]], None],
        top_roots: int,
        top_replies: int,
        poll_interval: int,
        active_idle: timedelta,
        clock: Callable[[], datetime] = _utcnow,
        materializer: AttachmentMaterializer | None = None,
        stop: asyncio.Event | None = None,
        heartbeat: Callable[[], None] | None = None,
        policy: ReplyPolicy | None = None,
        dead_letters: MessageDeadLetterStore | None = None,
    ) -> None:
        self._client = client
        self._handle = handle
        self._watch = watch
        self._state = state
        self._persist = persist
        self._top_roots = top_roots
        self._top_replies = top_replies
        self._poll_interval = poll_interval
        self._active_idle = active_idle
        self._clock = clock
        # Bramka „czy w ogóle odpowiadać" (SZKIELET wielokanałowy) — ``None`` (domyślnie)
        # = bez bramki, zachowanie identyczne jak przed jej wprowadzeniem.
        self._policy = policy
        # Materializacja załączników (I/O) — ``None`` wyłącza obsługę plików/obrazów
        # (drzwi tekstowe, testy bez sieci); wpięta w ``app.py`` na kliencie Graph.
        self._materializer = materializer
        self._stop = stop
        self._heartbeat = heartbeat
        # Kwarantanna wiadomości porzuconych po wyczerpaniu prób (ADR 0069); ``None`` → dawne
        # zachowanie (ślad zostaje wyłącznie w logu). Powód ostatniej porażki trzymamy w PAMIĘCI:
        # trwały jest licznik, bo to on rozstrzyga o porzuceniu — powód jest opisem dla operatora.
        self._dead_letters = dead_letters
        self._last_error: dict[str, str] = {}
        # Czy OSTATNI zapis stanu się nie udał — gasi puls, żeby healthcheck zobaczył wolumen,
        # który przestał przyjmować zapis (bez tego proces „żyje", nie robiąc nic).
        self._write_failed = False

    async def run(self) -> None:
        """Pętla główna: co ``poll_interval`` odpytaj każdy kanał i odpowiedz na nowe wpisy.

        Sygnał ``stop`` (SIGTERM w ``app.py``) kończy pętlę PO utrwaleniu bieżącej rundy —
        graceful shutdown: bieżąca runda kanałów dochodzi do zapisu, zanim proces wyjdzie (R1).
        """
        await self._client.refresh_auth()
        me_id = await self._client.get_me_id()
        # Watermark startowy = teraz: nie odpowiadamy na backlog sprzed uruchomienia.
        self._seed(self._clock().isoformat())
        logger.info(
            "Nasłuch %d kanałów Teams (delegowany, jako user_id=%s). Ctrl+C/SIGTERM kończy.",
            len(self._watch),
            me_id,
        )
        while not self._stopping():
            try:
                await self._client.refresh_auth()
            except Exception:
                # Odświeżenie tokenu padło → NIE bijemy pulsu (jałowa pętla auth), healthcheck
                # po wieku pulsu wykryje token, którego nie da się odnowić bez re-primingu.
                logger.exception("Nie udało się odświeżyć tokenu Graph — ponowię za chwilę")
                if await self._sleep_or_stop():
                    break
                continue
            for team_id, channel_id in self._watch:
                try:
                    await self._poll_channel(team_id, channel_id, me_id)
                except Exception:
                    # Błąd jednego kanału nie kładzie pozostałych ani całej pętli.
                    logger.exception("Błąd pollingu kanału %s/%s", team_id, channel_id)
                # Puls PO KAŻDYM kanale (R5), a nie raz na rundę wszystkich: runda z N kanałami
                # zawiera N pełnych tur agenta, więc jej długość mówi o ruchu na kanałach, nie
                # o tym, czy proces żyje. Przy `--max-age 180` kontener bywał `unhealthy` na
                # ścieżce SZCZĘŚLIWEJ, a wtedy sygnał przestaje być czytany. Wygaszenie pulsu
                # zostaje tam, gdzie ma sens: jałowa pętla auth (wyżej) go nie bije.
                self._beat()
                await asyncio.sleep(_INTER_CHANNEL_SLEEP_S)
            try:
                self._write_state()
            except Exception:
                # Zapis stanu na końcu rundy stał POZA jakimkolwiek ``try``, więc wolumen, który
                # przestał przyjmować zapis, kładł CAŁĄ pętlę — a nadzorca wznawiał proces prosto
                # w ``require_writable``. Pętla ma przeżyć: licznik prób jest cofnięty
                # (``_record_attempt``), watermark nieprzesunięty, więc nic nie ginie — zostaje
                # głośny błąd co rundę i WYGASZONY puls, żeby healthcheck to zobaczył.
                logger.exception(
                    "Nie udało się utrwalić stanu po rundzie — wolumen stanu nie przyjmuje "
                    "zapisu; pulsu NIE odświeżam, kolejne rundy nie ruszą do przodu"
                )
            else:
                # Puls po domkniętej rundzie — stan utrwalony (zachowane z czasów jednego pulsu).
                self._beat()
            if await self._sleep_or_stop():
                break
        logger.info("Drzwi Teams: zatrzymanie na sygnał, stan zapisany.")

    def _write_state(self) -> None:
        """Utrwal stan; ZAPAMIĘTAJ porażkę (gasi puls — patrz ``_beat``) i propaguj wyjątek."""
        try:
            self._persist(self._state)
        except Exception:
            self._write_failed = True
            raise
        self._write_failed = False

    def _beat(self) -> None:
        """Odśwież puls żywotności, jeśli wstrzyknięto (R5). Bez callbacku — no-op (dev/testy).

        Runda, w której stan NIE dał się utrwalić, pulsu nie bije — ta sama reguła co u notifiera
        („bijemy po rundzie produktywnej", ADR 0067 §2). Bez tego wolumen zamontowany ``ro`` w
        trakcie pracy dawał kontener ZDROWY, mimo że żadna wiadomość nie mogła już przejść:
        licznik prób nie utrwala się, więc obsługa w ogóle nie rusza.
        """
        if self._heartbeat is not None and not self._write_failed:
            self._heartbeat()

    def _stopping(self) -> bool:
        """True, gdy ``app.py`` ustawił ``stop`` (SIGTERM/SIGINT) — pętla ma się zakończyć."""
        return self._stop is not None and self._stop.is_set()

    async def _sleep_or_stop(self) -> bool:
        """Czekaj ``poll_interval`` albo do sygnału stop; zwróć True, gdy stop (przerwij pętlę)."""
        if self._stop is None:
            await asyncio.sleep(self._poll_interval)
            return False
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
        except TimeoutError:
            return False
        return True

    async def _poll_channel(self, team_id: str, channel_id: str, me_id: str) -> None:
        key = f"{team_id}/{channel_id}"
        channel_state = self._state["channels"][key]

        roots = await self._client.list_root_messages(team_id, channel_id, top=self._top_roots)
        replies_by_root: dict[str, list[dict[str, Any]]] = {}
        for root_id in selection.roots_to_poll(roots, channel_state):
            replies_by_root[root_id] = await self._client.list_replies(
                team_id, channel_id, root_id, top=self._top_replies
            )

        messages, new_channel_state = selection.plan_channel(
            roots,
            replies_by_root,
            channel_state,
            me_id=me_id,
            replied=set(self._state["replied"]),
            now=self._clock(),
            active_idle=self._active_idle,
            policy=self._policy,
            channel=(team_id, channel_id),
        )

        for msg in messages:
            conversation_id = f"{team_id}/{channel_id}/{msg.thread_root_id}"
            # LICZNIK PRÓB, nie flaga „już brana". Obsługa jednej wiadomości potrafi zabić
            # proces (materializacja bomby dekompresyjnej wyczerpuje pamięć, nadzorca
            # restartuje drzwi), a stan utrwalany dopiero po rundzie sprawiał, że restart
            # zastawał ten sam stan i brał tę samą wiadomość — awaria POJEDYNCZEJ wiadomości
            # stawała się TRWAŁĄ pętlą restartów, w której nie przechodziło już nic innego.
            #
            # Samo „oznacz odpisane przed obsługą" zamykało pętlę, ale płaciło za to szerzej,
            # niż trzeba: KAŻDY przejściowy błąd (503 na ``hostedContents``, timeout LLM,
            # 429 przy wysyłce) kasował wiadomość, na którą rozmówca nigdy nie dostawał
            # odpowiedzi. Licznik daje obie własności naraz: pierwsza porażka wraca do
            # ponowienia, druga kończy sprawę. Crash-loop wysyca licznik tak samo jak błąd
            # przejściowy, więc granica jest jedna dla obu przyczyn.
            attempts: dict[str, int] = self._state["attempts"]
            taken = int(attempts.get(msg.id, 0))
            if taken >= _MAX_ATTEMPTS:
                self._abandon(msg, team_id, channel_id, taken)
                continue
            # Próba MUSI dotrwać restartu, inaczej licznik nie liczy — ale NIENALICZONA próba
            # jest lepsza niż naliczona po nieudanym zapisie (patrz ``_record_attempt``).
            self._record_attempt(msg.id, taken)
            try:
                # Materializuj załączniki (I/O) tuż przed obsługą — bajty trafiają na kopię
                # wiadomości, którą handler przekłada na treść multimodalną dla agenta.
                if self._materializer is not None and msg.attachment_refs:
                    attachments = await self._materializer.materialize(team_id, channel_id, msg)
                    msg = replace(msg, attachments=attachments)
                reply = await self._handle(msg, conversation_id)
                if reply:
                    # Log bez TREŚCI (treść kanału to dane) — sam fakt odpowiedzi: obserwowalność.
                    logger.info(
                        "Odpowiadam w %s/%s na wiadomość od %s (wątek %s)",
                        team_id,
                        channel_id,
                        msg.sender_name,
                        msg.thread_root_id,
                    )
                    await self._post_reply(team_id, channel_id, msg, reply)
            except Exception as exc:
                # Powód dla operatora: gdy licznik się wysyci, kwarantanna ma powiedzieć NA CZYM
                # ta wiadomość padła, a nie tylko że padła (ADR 0069).
                self._last_error[msg.id] = _failure_reason(exc)
                # Log NAZYWA wiadomość i mówi, czy będzie ponowienie. Goły ``logger.exception``
                # piętro wyżej zostawiał operatora z tracebackiem bez tej jednej informacji,
                # która pozwala odróżnić „chwilowo padło" od „ta wiadomość jest trująca".
                logger.exception(
                    "Obsługa wiadomości %s w %s/%s nie powiodła się (próba %d z %d) — %s",
                    msg.id,
                    team_id,
                    channel_id,
                    taken + 1,
                    _MAX_ATTEMPTS,
                    "ponowię w następnej rundzie"
                    if taken + 1 < _MAX_ATTEMPTS
                    else "kolejna runda ją porzuci i odłoży do kwarantanny",
                )
                raise
            self._forget_attempt(msg.id)
            self._mark_replied(msg.id)
            self._write_state()  # sukces też utrwalamy od razu — inaczej dubel po restarcie
            # Puls PO KAŻDEJ obsłużonej wiadomości (a nie raz na rundę wszystkich kanałów):
            # runda zawiera pełne tury agenta, więc na ścieżce SZCZĘŚLIWEJ potrafi trwać
            # dłużej niż wiek pulsu dopuszczony przez healthcheck — kontener bywał wtedy
            # `unhealthy` przy poprawnej pracy, a sygnał przestawał być czytany.
            self._beat()

        # Watermark przesuwamy DOPIERO po obsłudze: gdy wysyłka zawiedzie PRZEJŚCIOWO (i
        # wyjdzie z pętli), nie zapisujemy postępu — następna runda ponowi listowanie od tego
        # samego miejsca, a dedup ``replied`` (utrwalony wyżej) pominie już obsłużone.
        self._state["channels"][key] = new_channel_state

    async def _post_reply(
        self, team_id: str, channel_id: str, msg: ChannelMessage, reply: str
    ) -> None:
        """Wyślij odpowiedź; TRWAŁY (4xx) błąd połknij, PRZEJŚCIOWY propaguj do ponowienia.

        Trwały błąd wysyłki (np. 403 bez zgody ``ChannelMessage.Send`` na kanale albo
        wiadomość nie do wysłania) NIE może zapętlić kanału — inaczej ta sama wiadomość
        wracałaby co rundę, blokując kolejne i wołając LLM w kółko. Połykamy go (wiadomość i
        tak zostanie oznaczona jako odpisana), a przejściowy (429/5xx/sieć) podnosimy —
        pętla wyżej nie przesunie watermarku i ponowi w następnej rundzie.
        """
        try:
            await self._client.post_reply(team_id, channel_id, msg.thread_root_id, reply)
        except Exception as exc:  # rozróżnienie po statusie HTTP, bez importu httpx tutaj
            if not _is_permanent(exc):
                raise
            logger.error(
                "Trwały błąd wysyłki (%s) w %s/%s — pomijam wiadomość %s",
                _status_of(exc),
                team_id,
                channel_id,
                msg.id,
            )

    def _mark_replied(self, msg_id: str) -> None:
        replied: list[str] = self._state["replied"]
        replied.append(msg_id)
        del replied[:-_REPLIED_CAP]  # zostaw ostatnie _REPLIED_CAP (no-op gdy krótsza)

    def _record_attempt(self, msg_id: str, taken: int) -> None:
        """Podnieś licznik prób tej wiadomości i UTRWAL go; porażka zapisu COFA podniesienie.

        Rozróżnienie „nie da się zapisać stanu" od „ta wiadomość jest trująca" jest tu całą
        rzeczą (ADR 0069). Zapis stał POZA blokiem ``try`` obsługi, a wolumen stanu potrafi
        przestać przyjmować zapis W TRAKCIE pracy (pełny dysk, remount ``ro`` — ``require_writable``
        sonduje wyłącznie stan startowy). Licznik rósł wtedy w PAMIĘCI, wyjątek łapał ``except``
        per kanał, a po ``_MAX_ATTEMPTS`` rundach wiadomość dostawała ``_mark_replied`` i znikała
        ze strumienia na stałe — mimo że obsługa nie ruszyła ANI RAZU, a log mówił „po dwóch
        nieudanych próbach obsługi". Skoro próba nie dotrwa restartu, to jej nie było: cofamy
        licznik i podnosimy wyjątek, więc runda kanału kończy się jak każda inna awaria
        infrastruktury — bez przesunięcia watermarku i bez naliczenia próby.
        """
        attempts: dict[str, int] = self._state["attempts"]
        # Wstawienie na ISTNIEJĄCY klucz nie przesuwa go na koniec, a ``_prune_attempts`` obcina
        # wg kolejności wstawienia — bez tego ``pop`` przycięta pod sufitem zostałaby wiadomość
        # aktualnie w obiegu, nie ta porzucona najdawniej.
        attempts.pop(msg_id, None)
        attempts[msg_id] = taken + 1
        self._prune_attempts()
        try:
            self._write_state()
        except Exception:
            if taken:
                attempts[msg_id] = taken
            else:
                attempts.pop(msg_id, None)
            logger.exception(
                "Nie udało się utrwalić licznika prób wiadomości %s — próby NIE naliczam "
                "(awaria zapisu stanu, nie wina wiadomości); wrócę do niej w następnej rundzie",
                msg_id,
            )
            raise

    def _abandon(self, msg: ChannelMessage, team_id: str, channel_id: str, taken: int) -> None:
        """Porzuć wiadomość po wyczerpaniu prób: NAJPIERW kwarantanna, potem dedup i zapis.

        Kolejność (wpis trwały, ZANIM wiadomość zniknie ze strumienia) jest ta sama co przy
        dead-letterze notifiera (ADR 0067 §2, tam „zapis przed ruchem kursora"). Rozjeżdża się
        za to reakcja na ODMOWĘ magazynu: notifier zostawia kursor i ponawia, a my kwarantannę
        POMIJAMY i idziemy dalej (ADR 0069, korekta po przeglądzie 4).

        Wyjątek z ``record`` przerywał ``for msg in messages`` i całe ``_poll_channel``, więc
        trwała awaria magazynu (``SQLITE_CORRUPT``, ``disk I/O error``, ``SQLITE_BUSY`` ponad
        ``busy_timeout``) zatrzymywała CAŁY kanał: zdrowa wiadomość stojąca za trującą nie
        wchodziła do handlera ani razu. To strata szersza niż ta, przed którą blokada miała
        chronić — a chroniła nie przed utratą TREŚCI (ta zostaje w Teams; wpis jest tylko
        wskaźnikiem), lecz przed utratą ŚLADU. Ponawianie i tak nic nie dawało: watermark rundy
        przesuwa się na końcu ``_poll_channel``, więc kolejna runda tej wiadomości już nie
        wybierze. Zamiast tego log ERROR niesie KOMPLET pola po polu — to on jest wtedy
        zapasowym rejestrem.
        """
        entry = {
            "door": _DOOR,
            "message_id": msg.id,
            "channel": f"{team_id}/{channel_id}",
            "thread_root_id": msg.thread_root_id,
            "sender": msg.sender_id,
            "reason": self._last_error.get(msg.id, _NO_REASON),
            "attempts": taken,
        }
        quarantined = False
        if self._dead_letters is not None:
            try:
                self._dead_letters.record(**entry)  # type: ignore[arg-type]
                quarantined = True
            except Exception:
                logger.exception(
                    "Kwarantanna ODRZUCIŁA wpis dla wiadomości %s — porzucam ją mimo to, żeby "
                    "awaria magazynu nie zatrzymała kanału. Ślad zostaje w tym logu: %s",
                    msg.id,
                    entry,
                )
        logger.error(
            "Porzucam wiadomość %s w %s/%s po %d nieudanych próbach obsługi — rozmówca nie "
            "dostanie odpowiedzi (dalsze ponawianie zapętliłoby kanał). %s",
            msg.id,
            team_id,
            channel_id,
            taken,
            "Wpis jest w kwarantannie (`inbound_dead_letters` w events.db)."
            if quarantined
            else "BEZ wpisu w kwarantannie — ślad zostaje wyłącznie w logu.",
        )
        self._forget_attempt(msg.id)
        self._mark_replied(msg.id)
        self._write_state()

    def _forget_attempt(self, msg_id: str) -> None:
        """Zapomnij licznik prób tej wiadomości — sprawa zamknięta (sukces albo rezygnacja)."""
        self._state["attempts"].pop(msg_id, None)
        self._last_error.pop(msg_id, None)

    def _prune_attempts(self) -> None:
        """Przytnij licznik prób do ``_ATTEMPTS_CAP`` najdawniej DOTKNIĘTYCH wpisów.

        Wpisy znikają same przy sukcesie i przy rezygnacji, więc słownik jest z natury mały.
        Backstop jest na przypadek, którego sam nie domknie: proces ubity między zapisaniem
        próby a jej zamknięciem, a wiadomość w międzyczasie usunięta z kanału — wtedy klucz nie
        ma już czego dotyczyć i zostałby w stanie na zawsze. Słowniki zachowują kolejność
        wstawienia, a ``_record_attempt`` przed podniesieniem licznika USUWA klucz, więc wpis
        wiadomości w obiegu ląduje na końcu — obcinamy to, czego nikt dawno nie tknął.
        """
        attempts: dict[str, int] = self._state["attempts"]
        for stale in list(attempts)[: max(0, len(attempts) - _ATTEMPTS_CAP)]:
            del attempts[stale]
            self._last_error.pop(stale, None)

    def _seed(self, startup_iso: str) -> None:
        """Zainicjuj brakujące gałęzie stanu (idempotentnie), nie ruszając zapisanych pozycji.

        Sprawdzamy TYP, nie samą obecność klucza. ``state.load`` jest tolerancyjny (uszkodzony
        plik → pusty stan), ale plik POPRAWNY składniowo i pusty znaczeniowo — ``{"attempts":
        null}`` po ręcznej edycji albo starszym zapisie — przechodził przez ``setdefault``
        nietknięty i wywracał każdą rundę każdego kanału na ``AttributeError``, TRWALE: wyjście
        wymagało skasowania pliku stanu. Gałąź złego kształtu zastępujemy pustą, bo dla obu
        (dedup, licznik prób) pusta znaczy „zacznij liczyć od nowa", a nie utratę odpowiedzi.
        """
        if not isinstance(self._state.get("replied"), list):
            self._state["replied"] = []
        if not isinstance(self._state.get("attempts"), dict):
            self._state["attempts"] = {}
        # Wartość spoza liczb całkowitych (``{"root-1": "abc"}`` po ręcznej edycji) wywracała
        # ``int(...)`` w pętli wiadomości — ta sama trwała awaria co ``null`` pod kluczem, tyle że
        # o poziom głębiej. Wpis nieczytelny kasujemy: licznik od zera znaczy „podejdź ponownie",
        # a to jest bezpieczniejsza strona pomyłki niż porzucenie wiadomości.
        attempts: dict[str, Any] = self._state["attempts"]
        for msg_id in [
            k for k, v in attempts.items() if not isinstance(v, int) or isinstance(v, bool)
        ]:
            del attempts[msg_id]
        if not isinstance(self._state.get("channels"), dict):
            self._state["channels"] = {}
        channels: dict[str, Any] = self._state["channels"]
        for team_id, channel_id in self._watch:
            key = f"{team_id}/{channel_id}"
            if not isinstance(channels.get(key), dict):
                channels[key] = {}
            channel: dict[str, Any] = channels[key]
            if not isinstance(channel.get("since_roots"), str):
                channel["since_roots"] = startup_iso
            if not isinstance(channel.get("threads"), dict):
                channel["threads"] = {}
