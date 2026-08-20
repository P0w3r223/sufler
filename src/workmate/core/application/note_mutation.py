"""Bramka mutacji bazy wiedzy (ADR 0065) — jedyna droga do zmiany istniejącej notatki.

Kolejność jest tu treścią, nie porządkiem czytania. Twarde kontrole padają PRZED sędzią i
niezależnie od jego zdania; sędzia stoi na wierzchu i potrafi tylko ZAWĘZIĆ:

1. **Adresowanie po identyfikatorze**, nigdy po ścieżce od modelu. Notatka musi istnieć.
2. **Ścieżki deterministyczne zostają create-only** — notatki ze spotkania i wątku opierają
   idempotencję na kolizji ``os.link`` (ADR 0043/0048); mutacja by tę gwarancję rozbroiła.
3. **Strażnik treści** (``reject_dangerous_content``) na nowej treści, jak przy zapisie.
4. **Migawka** — i to ona odmawia operacji, gdy się nie uda (patrz port ``NoteSnapshots``).
5. **Sędzia** — dopiero teraz, na komplecie faktów.

Autoryzacja nadawcy po AAD (ADR 0042/0062) pada jeszcze wyżej, w drzwiach: do tej bramki nie
dochodzi nikt nierozpoznany, więc sędzia nigdy nie decyduje o tym, KTO może pisać — wyłącznie
o tym, czy TA zmiana jest rozsądna.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from workmate.core.domain.mutation import JudgeVerdict, MutationRequest, Verdict, refusal
from workmate.core.domain.sanitize import reject_dangerous_content
from workmate.core.errors import WriteError

if TYPE_CHECKING:
    from workmate.core.domain.models import Note
    from workmate.core.ports.confirmations import ConfirmationLedger
    from workmate.core.ports.judge import MutationJudge
    from workmate.core.ports.repositories import NotesRepository, NotesWriter
    from workmate.core.ports.snapshots import NoteSnapshots

# Znaczniki identyfikatorów, których treść jest wyprowadzana deterministycznie z ŹRÓDŁA
# (spotkanie, wątek Teams). Ich niezmienność to nie ostrożność, tylko mechanizm idempotencji:
# ponowne przetworzenie tego samego spotkania ma trafić na istniejący plik i odbić się, a nie
# nadpisać wynik poprzedniego przebiegu.
_DETERMINISTIC_MARKERS = ("-mtg-", "-thr-")
# Znacznik liczy się tylko w części identyfikatora po DACIE (``…/RRRR-MM-DD-<znacznik>-<hash>``).
# Test podłańcucha na całym id brałby zwykłą notatkę „Notatka mtg z klientem" za wygenerowaną
# ze spotkania i zamrażał ją na zawsze — z komunikatem, który dodatkowo kłamie o jej pochodzeniu.
_DATE_PREFIX_LEN = len("RRRR-MM-DD")

logger = logging.getLogger(__name__)

# Ujście werdyktu sędziego: (werdykt, uzasadnienie). Wołane w chwili orzeczenia, best-effort po
# stronie WOŁAJĄCEGO — bramka mutacji nie ma prawa paść przez dziennik (ADR 0067 §1.1).
VerdictSink = Callable[[Verdict, str], None]


@dataclass(frozen=True)
class MutationOutcome:
    """Wynik próby mutacji: co się stało i co powiedzieć człowiekowi.

    ``applied`` rozróżnia „zmieniono" od „nie zmieniono"; ``verdict`` niesie powód, a
    ``snapshot`` — gdzie leży kopia sprzed zmiany, żeby cofnięcie nie wymagało śledztwa.
    """

    applied: bool
    verdict: JudgeVerdict
    snapshot: str = ""


class NoteMutationService:
    """Zmiana i usunięcie notatki — z migawką i sędzią, bez wyjątków od tej drogi."""

    def __init__(
        self,
        notes: NotesRepository,
        writer: NotesWriter,
        snapshots: NoteSnapshots,
        judge: MutationJudge,
        confirmations: ConfirmationLedger,
        *,
        allow_delete: bool = False,
    ) -> None:
        self._notes = notes
        self._writer = writer
        self._snapshots = snapshots
        self._judge = judge
        self._confirmations = confirmations
        # Kasowanie ma WŁASNY przełącznik, osobny od edycji. ADR 0065 wiąże jego wypuszczenie
        # z działającą nocną kopią wolumenu: migawka cofa jedną pomyłkę, ale przed złym dniem
        # ratuje dopiero kopia poza hostem. Dopóki operator jej nie uruchomi, ta zdolność ma
        # zostać niedostępna — nie „dostępna z ostrzeżeniem".
        self._allow_delete = allow_delete

    @property
    def allow_delete(self) -> bool:
        """Czy kasowanie jest wypuszczone — do ZBUDOWANIA powierzchni, nie tylko do odmowy.

        Wystawione, bo katalog narzędzi musi znać tę bramkę PRZED złożeniem sygnatury: wzorzec
        „bramka w ``Literal``, nie w ciele" (ADR 0006) wymaga, żeby wyłączona zdolność w ogóle
        nie istniała w schemacie. Odczyt z serwisu, a nie druga flaga przekazywana obok, bo dwie
        kopie tej samej reguły rozjeżdżają się dokładnie tak, jak rozjechał się ``shell_available``
        (ADR 0068 §12): model widziałby wtedy `delete` w enumie i tracił rundę na odmowę z ciała.
        """
        return self._allow_delete

    def edit_note(
        self,
        note_id: str,
        new_body: str,
        *,
        requester: str,
        intent: str,
        turn_token: str = "",
        trust_class: str = "unknown",
        tainted: bool = True,
        verdict_sink: VerdictSink | None = None,
    ) -> MutationOutcome:
        """Podmień TREŚĆ istniejącej notatki; metadane zostają nietknięte.

        Metadane (projekt, data, uczestnicy) są poza zasięgiem rozmyślnie: to z nich wywodzi się
        identyfikator i miejsce pliku, więc ich zmiana byłaby w istocie przeniesieniem notatki
        pod inny adres — inną operacją, z innym promieniem rażenia niż „popraw treść".

        Zwraca ``MutationOutcome``, tak samo jak ``delete_note``, a nie zmienioną notatkę. Werdykt
        sędziego ma trafić do wiersza audytu (ADR 0065 §8) TAKŻE wtedy, gdy zmiana przeszła —
        dziennik, w którym widać wyłącznie odmowy, opisuje inny system niż ten, który działa.
        Notatki i tak nikt tu nie odbierał: jedyny wołający (``build_file_catalog``) potwierdza
        człowiekowi sam fakt zmiany, a treść po mutacji zna, bo sam ją podał.
        """
        # ``wersja`` jest starsza LUB równa treści ``note`` — patrz ``_require_mutable``. To ona
        # domyka okno między odczytem a zapisem: między nimi leży wywołanie sieciowe sędziego,
        # a drzwi obsługują tury równolegle.
        wersja, note, migawka = self._require_mutable(note_id)
        reject_dangerous_content(new_body)
        odrzuc_wlasny_frontmatter(new_body)
        request = MutationRequest(
            kind="edit",
            note_id=note_id,
            requester=requester,
            intent=intent,
            current_body=note.body,
            new_body=new_body.strip(),
            turn_token=turn_token,
            trust_class=trust_class,
            tainted=tainted,
        )
        outcome = self._decide(request, migawka, verdict_sink)
        if not outcome.applied:
            raise MutationRefused(outcome)
        zmieniona = note.model_copy(update={"body": new_body.strip()})
        # Skrót liczony z TEGO SAMEGO renderu, który leży na dysku — kontrola wersji ma
        # porównywać plik z plikiem, nie model z plikiem.
        self._writer.overwrite(zmieniona, expected_sha256=wersja)
        return outcome

    def delete_note(
        self,
        note_id: str,
        *,
        requester: str,
        intent: str,
        turn_token: str = "",
        trust_class: str = "unknown",
        tainted: bool = True,
        verdict_sink: VerdictSink | None = None,
    ) -> MutationOutcome:
        """Usuń POJEDYNCZĄ notatkę — po migawce i po werdykcie sędziego."""
        if not self._allow_delete:
            raise WriteError(
                "usuwanie notatek jest wyłączone (ADR 0065 wiąże je z działającą kopią zapasową)"
            )
        # Kontrola wersji jak przy edycji — okno jest tu nawet szersze, bo przy werdykcie
        # „confirm" między odczytem a usunięciem leży CAŁA tura, nie samo wywołanie sędziego.
        # Bez niej równoległa edycja z tego okna znikała BEZ MIGAWKI.
        wersja, note, migawka = self._require_mutable(note_id)
        request = MutationRequest(
            kind="delete",
            note_id=note_id,
            requester=requester,
            intent=intent,
            current_body=note.body,
            turn_token=turn_token,
            trust_class=trust_class,
            tainted=tainted,
        )
        outcome = self._decide(request, migawka, verdict_sink)
        if not outcome.applied:
            raise MutationRefused(outcome)
        self._writer.delete(note_id, expected_sha256=wersja)
        return outcome

    def _require_mutable(self, note_id: str) -> tuple[str, Note, str]:
        """Zwróć ``(znacznik wersji, notatka, treść pliku)``, jeśli wolno ją ruszać; inaczej
        ``WriteError``.

        **Skrót i materiał migawki pochodzą z JEDNEGO odczytu bajtów** (``content_with_digest``)
        i to jest cała treść tej kolejności. Przy dwóch osobnych odczytach dzieli je okno, w które
        może wejść równoległa tura — a wtedy skrót opisuje inną wersję pliku niż ta, którą
        zabezpiecza kopia: kontrola wersji przepuszcza operację, bo skrót się zgadza, a migawka
        trzyma treść, której już nie ma. Wcześniejsza redakcja radziła sobie z tym kolejnością
        (skrót przed treścią), co gwarantowało tylko tyle, że migawka jest NIE STARSZA niż skrót;
        jeden odczyt gwarantuje, że jest DOKŁADNIE tą wersją.

        Materiałem migawki są BAJTY PLIKU, nie render z modelu — patrz port ``NoteSnapshots``.
        Notatka z ``self._notes`` służy dalej do oceny zmiany (sędzia dostaje ``current_body``)
        i do zapisu; do zabezpieczenia — nie, bo model gubi to, czego nie zna.

        Pusty skrót znaczy „nie ma czego zabezpieczyć" (plik zniknął w oknie, jest nieczytelny
        albo nie jest poprawnym UTF-8) i jest ODMOWĄ: mutacja bez kopii to dokładnie ten stan,
        którego ta warstwa ma nie dopuszczać.
        """
        if _jest_deterministyczna(note_id):
            raise WriteError(
                f"notatka {note_id} pochodzi ze spotkania lub wątku i jest tylko do odczytu "
                "— jej niezmienność jest gwarancją, że powtórne przetworzenie źródła niczego "
                "nie nadpisze"
            )
        tresc_pliku, wersja = self._writer.content_with_digest(note_id)
        note = self._notes.get(note_id)
        if note is None:
            raise WriteError(f"notatka nie istnieje: {note_id}")
        if not wersja:
            raise WriteError(
                f"nie udało się odczytać pliku notatki {note_id} — nie ruszam jej bez kopii"
            )
        return wersja, note, tresc_pliku

    def _decide(
        self,
        request: MutationRequest,
        snapshot_material: str,
        verdict_sink: VerdictSink | None = None,
    ) -> MutationOutcome:
        """Migawka, potem sędzia. Awaria któregokolwiek kroku = odmowa.

        Migawka PRZED werdyktem, choć przy odmowie okaże się niepotrzebna: gdyby powstawała po
        werdykcie, jej awaria zostawiałaby operację zatwierdzoną i niezabezpieczoną, a to gorszy
        stan niż jedna zbędna kopia. Kopia jest tania, utrata notatki nie.

        ``verdict_sink`` dostaje orzeczenie **w chwili, w której padło** — nie po udanym zapisie.
        Zapis może jeszcze paść na kontroli wersji (równoległa tura w oknie, które
        ``_require_mutable`` opisuje), a wtedy zgłoszenie po fakcie gubiłoby werdykt ``allow``
        i zostawiało wiersz audytu nieodróżnialny od „sędzia w ogóle nie biegł".

        Dwie odmowy WYŻEJ nie zgłaszają nic i to jest różnica merytoryczna, nie przeoczenie:
        awaria migawki i niedostępność sędziego to odmowy **techniczne**, przy których nikt nie
        orzekał. Wiersz „refuse" z tekstem wyjątku udawałby orzeczenie — i to akurat w sytuacji,
        w której dziennik ma wyjaśnić awarię infrastruktury, a nie decyzję o treści.
        """
        try:
            location = self._snapshots.save(request.note_id, snapshot_material)
        except Exception as exc:
            return MutationOutcome(False, refusal(f"nie udało się zabezpieczyć kopii: {exc}"))
        try:
            verdict = self._judge.review(request)
        except Exception as exc:  # implementacja portu ma nie rzucać — ale to bramka, nie ufa
            return MutationOutcome(False, refusal(f"sędzia niedostępny: {exc}"), location)
        if verdict_sink is not None:
            verdict_sink(verdict.verdict, verdict.reason)
        applied = (
            self._confirmed(request) if verdict.verdict == "confirm" else verdict.verdict == "allow"
        )
        # Ślad w dzienniku procesu: BEZ treści notatki i bez uzasadnienia sędziego (oba mogą
        # nieść fragmenty bazy wiedzy) — sama decyzja, kto, co i czy weszła w życie. To jest
        # ta połowa mitygacji R11, która czyni usunięcie głośnym PO fakcie. Uzasadnienie idzie
        # do wiersza audytu (ADR 0065 §8) — bazy o innej retencji i innym czytelniku — po
        # redakcji ``project_verdict``, a nie tutaj.
        logger.warning(
            "Mutacja bazy wiedzy: %s %s przez %s — werdykt %s, wykonana=%s, kopia=%s",
            request.kind,
            request.note_id,
            request.requester,
            verdict.verdict,
            applied,
            location,
        )
        return MutationOutcome(applied, verdict, location)

    def _confirmed(self, request: MutationRequest) -> bool:
        """Czy ta dokładnie prośba wróciła po zapowiedzi (punkt kontrolny człowieka).

        Pierwsze wystąpienie: zapamiętujemy i ODMAWIAMY — model ma powiedzieć człowiekowi, co
        miałoby się stać. Przechodzi dopiero powtórzenie z INNEJ tury, bo tura powstaje tylko
        wtedy, gdy ktoś napisał. Zgoda jest jednorazowa: po wykonaniu wpis znika, więc kolejna
        zmiana znów zaczyna od zapowiedzi.

        Czego to nie dowodzi: że człowiek się ZGODZIŁ. Dowodzi, że napisał. Mocniejszy dowód
        wymagałby kanału poza modelem, którego ta instalacja nie ma.
        """
        key = self._confirmation_key(request)
        zapowiedziana_w = self._confirmations.turn_of(key)
        if zapowiedziana_w is not None and zapowiedziana_w != request.turn_token:
            self._confirmations.forget(key)
            return True
        # Ta sama tura (albo brak zapowiedzi) → tylko zapowiadamy. Warunek na RÓŻNICĘ tur jest
        # tu całą treścią: bez niego model zapowiadał i wykonywał zmianę sam, w jednej turze —
        # pętla narzędzi ma na to osiem rund, a każda runda może nieść wiele wywołań naraz.
        self._confirmations.remember(key, request.turn_token)
        return False

    @staticmethod
    def _confirmation_key(request: MutationRequest) -> str:
        """Klucz zapowiedzi: człowiek + rodzaj + notatka + TREŚĆ.

        Treść wchodzi w klucz, żeby zapowiedź „popraw akapit o terminie" nie autoryzowała
        podmiany całej notatki na coś innego przy powtórzeniu — potwierdzeniu ma podlegać
        konkretna zmiana, nie sama chęć zmieniania.
        """
        material = f"{request.requester}|{request.kind}|{request.note_id}|{request.new_body}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def odrzuc_wlasny_frontmatter(new_body: str) -> None:
    """Podnieś ``WriteError``, gdy treść niesie WŁASNY nagłówek notatki — zamiast dołożyć go drugi
    raz.

    ``edit_note`` przyjmuje SAMĄ TREŚĆ; metadane zostają nietknięte i pisarz dokłada je sam
    (``render_note``). Model, który notatkę najpierw ODCZYTAŁ — przez `File(read)` albo przez
    `cat` po włączeniu powłoki — dostaje plik RAZEM z nagłówkiem, więc oddanie całości z powrotem
    jest zachowaniem naturalnym, nie egzotycznym.

    Bez tej bramki kończyło się to notatką z frontmatterem DWA RAZY: raz jako tekst na początku
    treści, raz dołożonym przez pisarza. Odtworzone na produkcji przy pierwszej realnej mutacji
    (2026-08-20): sędzia orzekł ``allow``, audyt zapisał ``status: ok``, człowiek dostał
    „Zrobione ✅" — a plik był uszkodzony, cicho i trwale. Kolejna edycja dokładałaby trzeci.

    ODMOWA, nie ciche obcięcie. Obcinanie musiałoby zgadywać, czy blok na początku jest
    nagłówkiem, czy treścią (poziomą linią, blokiem kodu, cytatem), a pomyłka kasowałaby
    użytkownikowi tekst bez śladu. Zdanie z komunikatu model czyta w tej samej turze i poprawia
    wywołanie; obcięcia nie zauważyłby nikt.

    **Rozpoznajemy NASZ nagłówek, nie „coś między kreskami" — i to jest cała ostrożność tej
    bramki.** Pierwsza redakcja pytała tylko, czy dalej stoi druga linia ``---``; tak szeroki
    warunek odmawiał treści całkiem poprawnej (dwie poziome kreski wokół akapitu), a komunikat
    kazał wtedy usunąć pola YAML, których w treści nie ma — polecenie niewykonalne inaczej niż
    przez skasowanie tekstu człowieka. Pytamy więc o POLE ze schematu ``NoteMetadata``: korupcja,
    o którą chodzi, to zawsze oddany z powrotem plik pisarza, a ten nosi ``title``/``project``.
    """
    # BOM nie jest białym znakiem dla ``lstrip()`` bez argumentu, a plik zapisany pod Windows
    # zaczyna się właśnie od niego — treść przechodziła wtedy bramkę i dawała dokładnie tę
    # korupcję, przed którą ta bramka stoi.
    linie = new_body.lstrip("\ufeff \t\r\n").splitlines()
    if not linie or linie[0].strip() != "---":
        return
    domkniecie = next(
        (nr for nr, linia in enumerate(linie[1:], start=1) if linia.strip() == "---"), None
    )
    # Sama pierwsza linia to w markdownie pozioma kreska i nią ma zostać.
    if domkniecie is None:
        return
    if not _niesie_pole_naglowka(linie[1:domkniecie]):
        return
    raise WriteError(
        "`content` zaczyna się od frontmatteru (`---`), a `edit` przyjmuje SAMĄ TREŚĆ — "
        "metadane (tytuł, projekt, data, uczestnicy) są poza zasięgiem tej operacji i pisarz "
        "dokłada je sam. Przekazanie całego pliku dałoby notatkę z nagłówkiem dwa razy. "
        "Ponów z treścią spod nagłówka, bez linii `---` i bez pól YAML."
    )


def _niesie_pole_naglowka(blok: list[str]) -> bool:
    """Czy blok między znacznikami niesie POLE nagłówka notatki — czy prozę między kreskami.

    Zbiór pól bierzemy z ``NoteMetadata``, a nie z listy przepisanej tutaj: nagłówek składa
    ``render_note`` z tego samego schematu, więc dopisanie pola w modelu ma domykać bramkę samo.
    Lista przepisana obok rozjechałaby się przy pierwszej takiej zmianie — i to po cichu, bo
    rozjazd widać dopiero na uszkodzonej notatce.
    """
    from workmate.core.domain.models import NoteMetadata

    pola = set(NoteMetadata.model_fields)
    return any(
        separator and klucz.strip() in pola
        for klucz, separator, _ in (linia.partition(":") for linia in blok)
    )


def _jest_deterministyczna(note_id: str) -> bool:
    """Czy identyfikator pochodzi ze ŹRÓDŁA (spotkanie, wątek) — sprawdzane po dacie w nazwie.

    Nazwa pliku ma kształt ``<data>-<reszta>``; znacznika szukamy dopiero w reszcie, żeby tytuł
    użytkownika nie mógł przypadkiem uczynić notatki niezmienną.
    """
    nazwa = note_id.rsplit("/", 1)[-1]
    ogon = nazwa[_DATE_PREFIX_LEN:]
    # PREFIKS ogona, nie podłańcuch: ``paths`` składa te identyfikatory jako ``<data>-mtg-<skrót>``,
    # więc znacznik stoi ZARAZ za datą. Test podłańcucha zamrażał każdą notatkę, której slug tytułu
    # miał w środku „-mtg-"/„-thr-" („Ustalenia mtg tygodniowy" → ``…-ustalenia-mtg-tygodniowy``) —
    # trwale nieedytowalną i nieusuwalną, z komunikatem kłamiącym o jej pochodzeniu.
    return any(ogon.startswith(marker) for marker in _DETERMINISTIC_MARKERS)


class MutationRefused(WriteError):
    """Mutacja nie doszła do skutku — niesie werdykt, żeby wołający mógł go pokazać.

    Dziedziczy z ``WriteError``, więc każda istniejąca koperta błędów zapisu łapie ją bez zmian
    i żaden wołający nie zamieni odmowy w traceback.
    """

    def __init__(self, outcome: MutationOutcome) -> None:
        super().__init__(outcome.verdict.reason)
        self.outcome = outcome
