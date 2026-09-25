"""Serwisy aplikacyjne — logika czterech narzędzi Fazy 1.

Świadomie proste: wyszukiwanie po metadanych i treści nad małym, dobrze
uschematyzowanym zbiorem notatek (bez RAG-a — to Faza 3). Serwisy zależą tylko
od portów (``NotesRepository`` / ``ProjectsRepository``), więc są w pełni
testowalne na atrapach w pamięci, bez dotykania dysku.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sufler.core.domain.models import (
    Note,
    NoteMetadata,
    NoteSummary,
    Project,
    ProjectStatus,
)
from sufler.core.domain.notes import notes_of_project
from sufler.core.domain.paths import meeting_note_id as build_meeting_note_id
from sufler.core.domain.paths import note_id as build_note_id
from sufler.core.domain.paths import thread_note_id as build_thread_note_id
from sufler.core.domain.ranking import bm25_rank, reciprocal_rank_fusion
from sufler.core.domain.sanitize import reject_dangerous_content
from sufler.core.errors import WriteError
from sufler.core.ports.repositories import (
    NotesRepository,
    NotesWriter,
    ProjectsRepository,
)

if TYPE_CHECKING:
    from datetime import date, datetime

    from sufler.core.application.events import EventService
    from sufler.core.ports.text import Lemmatizer, SemanticRanker

# Maksymalna długość fragmentu (snippet) zwracanego w wynikach wyszukiwania.
_SNIPPET_LENGTH = 200
# Tytuł waży więcej niż pozostałe pola przy ustalaniu trafności.
_TITLE_WEIGHT = 3
# Pokrycie (ile RÓŻNYCH słów zapytania w ogóle trafiło) dominuje nad ważeniem pól:
# notatka z większą liczbą słów zapytania jest trafniejsza niż taka, która wielokrotnie
# trafia jedno słowo w polu o wysokiej wadze. Stała > maks. ważenia pól dla realnych
# zapytań (suma wag pól ≈ 9 na słowo, więc bezpieczne do ~100 słów w zapytaniu).
_COVERAGE_WEIGHT = 1000


class NotesService:
    """Przypadki użycia dla notatek: wyszukiwanie i odczyt pojedynczej notatki.

    ``lemmatizer`` (opcjonalny, ADR 0023) włącza ranking BM25 nad LEMATAMI — sprowadza polską
    fleksję do lematów, więc „integracji" trafia „integracja". Bez niego (``None``) serwis używa
    dawnego scorera podłańcuchowego (zgodność wsteczna: istniejące wywołania i testy bez zmian).

    ``semantic`` (opcjonalny, ADR 0039, Faza B) dokłada warstwę DENSE: ranking osadzeń fuzowany z
    BM25 przez ``reciprocal_rank_fusion``. Działa TYLKO obok ``lemmatizer`` (fuzja w gałęzi BM25);
    domyślnie ``None`` (za bramką mikro-evalu). ``rrf_k`` stroi dyskonto rang RRF; ``dense_top_n``
    (0 = całość) opcjonalnie przycina ogon rankingu dense.
    """

    def __init__(
        self,
        notes: NotesRepository,
        *,
        lemmatizer: Lemmatizer | None = None,
        semantic: SemanticRanker | None = None,
        rrf_k: int = 60,
        dense_top_n: int = 0,
    ) -> None:
        self._notes = notes
        self._lemmatizer = lemmatizer
        self._semantic = semantic
        self._rrf_k = rrf_k
        self._dense_top_n = dense_top_n

    def search_notes(
        self,
        query: str,
        *,
        project: str | None = None,
        participant: str | None = None,
        limit: int = 10,
    ) -> list[NoteSummary]:
        """Znajdź notatki pasujące do zapytania, z opcjonalnymi filtrami.

        Z lematyzatorem: zapytanie i pola notatki są lematyzowane (odporność na polską fleksję),
        a ranking to BM25 (TF/IDF + normalizacja długości; waga tytułu przez powtórzenie lematów).
        Bez lematyzatora: dawne dopasowanie podłańcuchowe z pokryciem słów. W obu wypadkach
        wyniki sortowane malejąco po trafności, a przy remisie — po dacie; puste zapytanie zwraca
        wszystkie (posortowane po dacie).
        """
        raw_terms = query.lower().split()  # do snippetu (surowe słowa lepiej trafiają w treść)
        candidates = self._filtered(project=project, participant=participant)
        scored = self._score_all(candidates, query, raw_terms)

        summaries = [_summarize(note, raw_terms, score) for note, score in scored]
        summaries.sort(key=lambda s: (s.score, s.date), reverse=True)
        return summaries[: max(0, limit)]

    def _score_all(
        self, candidates: list[Note], query: str, raw_terms: list[str]
    ) -> list[tuple[Note, float]]:
        """Przypisz trafność każdej notatce (BM25 z lematyzacją albo podłańcuch); pomiń zerowe."""
        if not raw_terms:
            return [(note, 1.0) for note in candidates]  # puste zapytanie → wszystko

        if self._lemmatizer is not None:
            query_lemmas = self._lemmatizer.lemmatize(query)
            if not query_lemmas:  # brak tokenów słownych (np. sama interpunkcja) → jak puste
                return [(note, 1.0) for note in candidates]
            docs = {note.id: self._doc_lemmas(note) for note in candidates}
            bm25_scores = bm25_rank(query_lemmas, docs)
            by_id = {note.id: note for note in candidates}
            if self._semantic is None:
                return [(by_id[note_id], score) for note_id, score in bm25_scores.items()]
            return self._fuse(query, candidates, bm25_scores, by_id)

        # Fallback bez lematyzatora — dawny scorer podłańcuchowy (zgodność wsteczna). Warstwa dense
        # (ADR 0039) wpina się TYLKO w gałąź z lematyzatorem; bez niego jest pomijana (wiring drzwi
        # buduje ranker dense wyłącznie obok lematyzatora — brak cichego no-opu).
        return [
            (note, float(score)) for note in candidates if (score := _score(note, raw_terms)) > 0
        ]

    def _fuse(
        self,
        query: str,
        candidates: list[Note],
        bm25_scores: dict[str, float],
        by_id: dict[str, Note],
    ) -> list[tuple[Note, float]]:
        """Połącz ranking BM25 i semantyczny przez RRF (ADR 0039, Faza B).

        RRF jest ranga-zależne, więc łączy niekompatybilne skale (BM25 vs cosinus) bez
        normalizacji. Dense szereguje WSZYSTKICH kandydatów, więc fuzja może wypłynąć notatkę
        bez pokrycia słów (parafrazę), której BM25 nie trafił. Wynik dostaje syntetyczny, ściśle
        malejący wynik = odległość od końca fuzji, żeby sort po ``(score, date)`` zachował
        kolejność RRF (unikalne wyniki → data nie rozstrzyga).
        """
        # BM25 wnosi tylko notatki z trafieniem (> 0), uszeregowane po trafności (remis: data).
        bm25_ids = [
            note.id
            for note in sorted(
                candidates,
                key=lambda n: (bm25_scores.get(n.id, 0.0), n.metadata.date),
                reverse=True,
            )
            if bm25_scores.get(note.id, 0.0) > 0.0
        ]
        dense_ids = self._semantic.rank(query, candidates) if self._semantic else []
        if self._dense_top_n:
            dense_ids = dense_ids[: self._dense_top_n]
        fused = reciprocal_rank_fusion(bm25_ids, dense_ids, k=self._rrf_k)
        total = len(fused)
        return [(by_id[nid], float(total - rank)) for rank, nid in enumerate(fused) if nid in by_id]

    def _doc_lemmas(self, note: Note) -> list[str]:
        """Worek lematów notatki: wszystkie pola, z tytułem POWTÓRZONYM (waga tytułu ×3).

        Powtórzenie podbija ``tf`` termów tytułu, ale i ``dl`` (długość dok.), więc normalizacja
        długości BM25 częściowo je znosi — to INNA semantyka niż dawny addytywny bonus pola w
        ``_score`` (wyniki między gałęziami nie są wprost porównywalne). Przy krótkich tytułach
        spotkań efekt jest pomijalny; prawdziwe per-polowe BM25F to ewentualna przyszła rewizja.
        """
        assert self._lemmatizer is not None  # wołane tylko z gałęzi z lematyzatorem
        lemmatize = self._lemmatizer.lemmatize
        meta = note.metadata
        bag = lemmatize(meta.title) * _TITLE_WEIGHT
        bag += lemmatize(note.body)
        for field in (
            meta.decisions,
            meta.open_questions,
            meta.action_items,
            meta.tags,
            meta.participants,
        ):
            bag += lemmatize(" ".join(field))
        return bag

    def get_note(self, note_id: str) -> Note | None:
        """Zwróć pełną notatkę po id ``<firma>/<projekt>/<data>-<slug>`` albo ``None``."""
        return self._notes.get(note_id)

    def _filtered(self, *, project: str | None, participant: str | None) -> list[Note]:
        notes = self._notes.all()
        if project:
            notes = notes_of_project(notes, project)
        if participant:
            needle = participant.lower()
            notes = [n for n in notes if any(needle in p.lower() for p in n.metadata.participants)]
        return notes


class ProjectsService:
    """Przypadki użycia dla projektów: lista i status.

    ``get_project_status`` syntetyzuje zadeklarowany status (z rejestru) z faktami
    wyliczonymi z notatek — dlatego serwis potrzebuje obu repozytoriów.
    """

    def __init__(
        self,
        projects: ProjectsRepository,
        notes: NotesRepository,
        events: EventService | None = None,
    ) -> None:
        self._projects = projects
        self._notes = notes
        self._events = events

    def list_projects(self) -> list[Project]:
        """Zwróć projekty pionu, posortowane po kluczu."""
        return sorted(self._projects.all(), key=lambda p: p.key)

    def project_for_repo(self, repo: str) -> str | None:
        """Klucz projektu, do którego zmapowano repozytorium GitHub (``owner/repo``), albo ``None``.

        Odwrotny indeks rejestru (ADR 0028) — pozwala drzwiom GitHub przypisać zdarzenie do
        projektu. Dopasowanie bez rozróżniania wielkości liter; pierwszy pasujący projekt.
        """
        target = repo.strip().lower()
        if not target:
            return None
        for project in self._projects.all():
            if any(r.strip().lower() == target for r in project.github_repos):
                return project.key
        return None

    def get_project_status(self, key: str) -> ProjectStatus | None:
        """Zwróć status projektu albo ``None``, gdy projekt nie istnieje."""
        project = self._projects.get(key)
        record = self._projects.status_record(key)
        if project is None or record is None:
            return None

        project_notes = notes_of_project(self._notes.all(), key)
        note_dates = [n.metadata.date for n in project_notes]
        open_action_items = sum(len(n.metadata.action_items) for n in project_notes)
        activity_count, latest_activity_at, failing_ci_count = self._activity_facts(key)

        return ProjectStatus(
            key=record.key,
            company=project.company,
            name=project.name,
            status=record.status,
            health=record.health,
            phase=record.phase,
            summary=record.summary,
            last_updated=record.last_updated,
            notes_count=len(project_notes),
            latest_note_date=max(note_dates) if note_dates else None,
            open_action_items=open_action_items,
            recent_activity_count=activity_count,
            latest_activity_at=latest_activity_at,
            failing_ci_count=failing_ci_count,
        )

    def _activity_facts(self, key: str) -> tuple[int, datetime | None, int]:
        """Synteza aktywności GitHub projektu ze zdarzeń (ADR 0029): (liczba, ostatnia, porażki CI).

        Bez ``EventService`` → ``(0, None, 0)`` — status jak dawniej (drzwi bez mostu zdarzeń).
        """
        if self._events is None:
            return 0, None, 0
        # OKNO PO CZASIE, nie po kolejności przyjęcia (amendment ADR 0071, 2026-09-07). To jest
        # miejsce, w którym backfill odtworzyłby incydent w INNYM narzędziu: ``max(occurred_at)``
        # niżej porządkuje wnętrze okna, ale samo okno wybierane po ``id`` wpuściłoby lipcowe
        # wiersze backfillu (najwyższe ``id`` w bazie) i wypchnęłoby z niego naprawdę świeże
        # zdarzenia — a ``Project(status)`` zaczął(by) raportować „ostatnią aktywność" z lipca.
        items = self._events.recent_by_time(project=key, limit=100)
        failing = sum(1 for e in items if e.kind == "ci_failure")
        # MAKSIMUM, nie pierwszy element — mimo sortu po czasie. Powód jest inny niż kolejność:
        # dwa zdarzenia mogą mieć ten sam ``occurred_at``, a ``max`` nie zależy od tego, które
        # z nich baza zwróci pierwsze. Zdanie „ostatnio nic się nie działo" model buduje właśnie
        # na tym polu. ``change_digest._group_by_project`` liczy to tak od początku.
        latest = max((e.occurred_at for e in items), default=None)
        return len(items), latest, failing


class NotesWriteService:
    """Przypadek użycia zapisu notatki (Bramka 2, ADR 0006).

    Wylicza miejsce zapisu z metadanych (firma z rejestru + projekt + data +
    slug tytułu) i zapisuje przez port ``NotesWriter``. Nigdy nie nadpisuje
    istniejącej notatki — przy kolizji dokłada sufiks (``-2``, ``-3``, …).
    Zależy wyłącznie od portów, więc reguła zależności rdzeń↛adaptery zostaje
    zachowana.
    """

    def __init__(self, writer: NotesWriter, projects: ProjectsRepository) -> None:
        self._writer = writer
        self._projects = projects

    def save_note(self, metadata: NoteMetadata, body: str) -> Note:
        """Zapisz nową notatkę i zwróć ją z nadanym identyfikatorem."""
        # Strażnik wstrzyknięć (obrona w głąb): odrzuć NUL/znaki sterujące w polach
        # tekstowych, zanim cokolwiek trafi do pliku bazy.
        reject_dangerous_content(
            metadata.title,
            body,
            *metadata.participants,
            *metadata.decisions,
            *metadata.action_items,
            *metadata.open_questions,
            *metadata.tags,
        )
        project = self._projects.get(metadata.project)
        if project is None:
            raise WriteError(f"projekt nie istnieje w rejestrze: {metadata.project!r}")
        try:
            base_id = build_note_id(project.company, project.key, metadata.date, metadata.title)
        except ValueError as exc:
            raise WriteError(str(exc)) from exc

        note = Note(id=self._unique_id(base_id), metadata=metadata, body=body.strip())
        self._writer.write(note)
        return note

    def _unique_id(self, base_id: str) -> str:
        """Zwróć ``base_id`` lub, jeśli zajęty, z najniższym wolnym sufiksem."""
        if not self._writer.exists(base_id):
            return base_id
        suffix = 2
        while self._writer.exists(f"{base_id}-{suffix}"):
            suffix += 1
        return f"{base_id}-{suffix}"

    def require_project(self, project: str) -> None:
        """Rzuć ``WriteError``, gdy projekt nie istnieje w rejestrze — tani strażnik przed I/O.

        Pozwala ścieżce notatki ze spotkania odrzucić literówkę w projekcie ZANIM zapłaci za pobór
        transkryptu i wywołanie Claude (w trybie async koszt idzie po cichu w tle, ADR 0043).
        """
        if self._projects.get(project) is None:
            raise WriteError(f"projekt nie istnieje w rejestrze: {project!r}")

    def meeting_note_id(self, meeting_ref: str, *, project: str, date: date) -> str | None:
        """Deterministyczny id notatki tego spotkania, jeśli JUŻ istnieje; inaczej ``None``.

        Pre-check idempotencji (ADR 0043): pozwala przypadkowi użycia SPOTKANIA pominąć pobór
        transkryptu i wywołanie Claude, gdy notatka już jest. ``None`` też przy nieznanym projekcie
        — właściwy ``WriteError`` podniesie dopiero ``save_meeting_note`` (jedno miejsce błędu).
        """
        proj = self._projects.get(project)
        if proj is None:
            return None
        note_id = build_meeting_note_id(proj.company, proj.key, date, meeting_ref)
        return note_id if self._writer.exists(note_id) else None

    def save_meeting_note(self, metadata: NoteMetadata, body: str, *, meeting_ref: str) -> Note:
        """Zapisz notatkę ze spotkania z id DETERMINISTYCZNYM z ``meeting_ref`` (ADR 0043).

        Jak ``save_note`` (sanityzacja, rejestr projektu), ale id nie wywodzi się z tytułu Claude,
        lecz ze stałego ``meeting_ref`` — create-only na TYM id: ponowienie tego samego spotkania
        rzuca ``WriteError('już istnieje')`` zamiast dokładać duplikat ``-2``. Wołający robi
        wcześniej ``meeting_note_id`` (tania idempotencja bez kosztu Claude); ten zapis domyka
        wyścig (dwa równoległe przebiegi jednego spotkania → drugi dostanie kolizję create-only).
        """
        reject_dangerous_content(
            metadata.title,
            body,
            *metadata.participants,
            *metadata.decisions,
            *metadata.action_items,
            *metadata.open_questions,
            *metadata.tags,
        )
        project = self._projects.get(metadata.project)
        if project is None:
            raise WriteError(f"projekt nie istnieje w rejestrze: {metadata.project!r}")
        try:
            note_id = build_meeting_note_id(
                project.company, project.key, metadata.date, meeting_ref
            )
        except ValueError as exc:
            raise WriteError(str(exc)) from exc
        note = Note(id=note_id, metadata=metadata, body=body.strip())
        self._writer.write(note)  # create-only; kolizja → WriteError (idempotencja)
        return note

    def thread_note_id(self, source_message_id: str, *, project: str, date: date) -> str | None:
        """Deterministyczny id notatki tego wątku, jeśli JUŻ istnieje; inaczej ``None`` (ADR 0048).

        Pre-check idempotencji (klon 0043): pozwala przypadkowi użycia „zapisz to" pominąć pobór
        wątku i wywołanie Claude, gdy notatka wzmianki już jest. ``None`` też przy nieznanym
        projekcie — właściwy ``WriteError`` podniesie dopiero ``save_thread_note``.
        """
        proj = self._projects.get(project)
        if proj is None:
            return None
        note_id = build_thread_note_id(proj.company, proj.key, date, source_message_id)
        return note_id if self._writer.exists(note_id) else None

    def save_thread_note(
        self, metadata: NoteMetadata, body: str, *, source_message_id: str
    ) -> Note:
        """Zapisz notatkę z wątku z id DETERMINISTYCZNYM z ``source_message_id`` (ADR 0048 §5).

        Lustro ``save_meeting_note`` dla przechwycenia „zapisz to": id nie wywodzi się z tytułu
        Claude, lecz z ID WIADOMOŚCI-WZMIANKI — create-only na tym id, więc ponowienie tej samej
        wzmianki rzuca kolizję zamiast dokładać duplikat ``-2``. Wołający robi wcześniej tani
        ``thread_note_id`` (idempotencja bez kosztu Claude); ten zapis domyka wyścig.
        """
        reject_dangerous_content(
            metadata.title,
            body,
            *metadata.participants,
            *metadata.decisions,
            *metadata.action_items,
            *metadata.open_questions,
            *metadata.tags,
        )
        project = self._projects.get(metadata.project)
        if project is None:
            raise WriteError(f"projekt nie istnieje w rejestrze: {metadata.project!r}")
        try:
            note_id = build_thread_note_id(
                project.company, project.key, metadata.date, source_message_id
            )
        except ValueError as exc:
            raise WriteError(str(exc)) from exc
        note = Note(id=note_id, metadata=metadata, body=body.strip())
        self._writer.write(note)  # create-only; kolizja → NoteExistsError (idempotencja)
        return note


def _score(note: Note, terms: list[str]) -> int:
    """Trafność = pokrycie (różne słowa zapytania) × waga + ważenie pól.

    Pokrycie dominuje (``_COVERAGE_WEIGHT``): notatka trafiająca więcej różnych słów
    zapytania jest wyżej niż taka, która wielokrotnie trafia jedno słowo w tytule.
    """
    meta = note.metadata
    weighted_fields = [
        (meta.title.lower(), _TITLE_WEIGHT),
        (note.body.lower(), 1),
        (" ".join(meta.decisions).lower(), 1),
        (" ".join(meta.open_questions).lower(), 1),
        (" ".join(meta.action_items).lower(), 1),
        (" ".join(meta.tags).lower(), 1),
        (" ".join(meta.participants).lower(), 1),
    ]
    matched: set[str] = set()
    field_bonus = 0
    for term in terms:
        for text, weight in weighted_fields:
            if term in text:
                matched.add(term)
                field_bonus += weight
    return len(matched) * _COVERAGE_WEIGHT + field_bonus


def _summarize(note: Note, terms: list[str], score: float) -> NoteSummary:
    """Zbuduj lekki wynik wyszukiwania z fragmentem wokół dopasowania."""
    return NoteSummary(
        id=note.id,
        title=note.metadata.title,
        project=note.metadata.project,
        date=note.metadata.date,
        participants=note.metadata.participants,
        snippet=_make_snippet(note.body, terms),
        score=score,
    )


def _make_snippet(body: str, terms: list[str]) -> str:
    """Wytnij fragment treści wokół pierwszego trafionego słowa (albo początek)."""
    text = " ".join(body.split())
    if not text:
        return ""
    low = text.lower()
    position = -1
    for term in terms:
        found = low.find(term)
        if found != -1:
            position = found
            break
    if position == -1:
        snippet = text[:_SNIPPET_LENGTH]
        return snippet + ("…" if len(text) > _SNIPPET_LENGTH else "")

    start = max(0, position - _SNIPPET_LENGTH // 3)
    end = min(len(text), start + _SNIPPET_LENGTH)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"
