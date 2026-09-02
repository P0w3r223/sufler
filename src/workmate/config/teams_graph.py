"""Ustawienia drzwi Teams Graph — token, poller, załączniki, obrazy, dostawa plikiem."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from workmate.config._env import (
    _bool_from_env,
    _int_from_env,
    _list_from_env,
    _path_from_env,
)

# Domyślne ścieżki drzwi Teams w trybie delegowanym (ADR 0015): POZA repo i data/.
# Cache tokenu MSAL to SEKRET; plik stanu (watermark wątków) — dane operacyjne, nie sekret.
_DEFAULT_TEAMS_GRAPH_CACHE = Path.home() / ".workmate" / "teams_token_cache.bin"
_DEFAULT_TEAMS_GRAPH_STATE = Path.home() / ".workmate" / "teams_graph_state.json"
# Zakresy delegowane, których potrzebuje poller kanału (wymagają zgody admina). NIE wpisuj
# offline_access/openid/profile — MSAL dokłada je sam (inaczej błąd "reserved scope").
_DEFAULT_TEAMS_GRAPH_SCOPES = (
    "ChannelMessage.Read.All",
    "ChannelMessage.Send",
    "Team.ReadBasic.All",
    "Channel.ReadBasic.All",
    "User.Read",
    # Pobieranie plików-załączników (PDF/Word/obrazy) leżących w SharePoint (ADR 0016).
    # Obrazy wklejane inline (hostedContents) NIE wymagają tych zakresów.
    "Files.Read.All",
    "Sites.Read.All",
)
# Sufit rozmiaru (RAW) załącznika. Request API to max 32 MB, ale base64 puchnie ~1.33×,
# więc 24 MB surowych ≈ 32 MB zakodowane — twardy limit, by pojedynczy blok nie przekroczył
# żądania. Domyślne wartości (8/20 MB) zostawiają zapas na historię.
_MAX_ATTACHMENT_MB_CEILING = 24
# Górny cap liczby załączników na wiadomość (chroni przed absurdalną wartością operatora).
_MAX_ATTACHMENTS_PER_MESSAGE_CEILING = 20
# Sufit dłuższej krawędzi obrazu (px). 2576 to maksymalna użyteczna rozdzielczość modeli
# high-res (Sonnet 5) — powyżej model i tak skaluje po stronie serwera, więc nie ma sensu
# wysyłać więcej. Domyślne 2048 zostawia zapas na koszt tokenów (bloki wracają co turę).
_MAX_IMAGE_EDGE_PX_CEILING = 2576
_MIN_IMAGE_EDGE_PX = 256
# Sufit rozmiaru pliku EKSTRAHOWANEGO do tekstu (docx/xlsx/pptx/txt). Nie wysyłamy z nich
# bajtów do API (tylko tekst), więc sufit 32 MB base64 ich nie dotyczy — limit jest tylko
# barierą na ekstrakcję/pamięć. Prezentacje z obrazkami rutynowo mają 10–50 MB.
_MAX_EXTRACT_MB_CEILING = 100
# Zakres zapisu Graph wymagany do wgrania pliku na dysk kanału (ADR 0026, bramka enable_file_reply).
# Nadany 2026-07-27 (zgoda admina). Włączona bramka BEZ tego zakresu = martwa (403 przy uploadzie),
# więc walidacja żąda go WPROST — fail-fast zamiast cichej bramki „włączonej, ale niedziałającej".
_FILE_REPLY_WRITE_SCOPE = "Files.ReadWrite.All"
# Sufit rozmiaru ZRENDEROWANEGO pliku odpowiedzi (KB). Treść pochodzi od modelu (ograniczona
# max_tokens), ale twardy limit domyka powierzchnię eksfiltracji przy wstrzyknięciu (ADR 0026).
_MAX_FILE_REPLY_KB_CEILING = 4096
# Zakresy czatu wymagane do wysłania OBRAZU 1:1 do usera (ADR 0027, bramka enable_user_file_push).
# Obraz idzie INLINE (hostedContents) — bez zakresu Files.* — ale dostawa na czat 1:1 wymaga tych
# zakresów. Są JUŻ skonsentowane przez admina (używa ich Powiadomienia_teams/TeamsPush) i dzielą
# cache MSAL z tymi drzwiami; brakuje ich tylko na TOKENIE pollera kanału, więc włączenie bramki =
# dodanie ich do WORKMATE_TEAMS_GRAPH_SCOPES + ponowna zgoda device-code (bez nowej zgody admina).
# Walidacja żąda ich WPROST — fail-fast zamiast cichej bramki „włączonej, ale niedziałającej" (403).
_USER_PUSH_CHAT_SCOPES = ("Chat.Create", "ChatMessage.Send")
# Sufit rozmiaru obrazu push-owanego do usera (KB). Bajty pochodzą od modelu; twardy limit domyka
# powierzchnię eksfiltracji i mieści się w limicie żądania Graph (~32 MB po base64) (ADR 0027).
_MAX_USER_IMAGE_KB_CEILING = 4096
# Zapis wymagany do wysłania DOKUMENTU 1:1 (ADR 0027, wariant PLIKOWY, bramka enable_user_doc_push).
# Wariant plikowy wgrywa TYLKO na WŁASNY OneDrive bota (``PUT /me/drive/root``) i udostępnia WŁASNY
# item (``invite``) — do tego wystarcza WĘŻSZY ``Files.ReadWrite`` (pełny dostęp do plików
# ZALOGOWANEGO usera). ``Files.ReadWrite.All`` (też cudze pliki i witryny SharePoint) jest SZERSZY
# niż tu trzeba, ale AKCEPTOWANY, bo ADR 0026 (upload na dysk KANAŁU) i tak go konsentuje — operator
# chcący TYLKO push dokumentu może nadać węższy (least-privilege). Wymagamy co najmniej JEDNEGO z
# pary; zakresy CZATU (dostawa 1:1) osobno w ``_USER_PUSH_CHAT_SCOPES``. (Że ``invite`` działa pod
# węższym ``Files.ReadWrite`` — do potwierdzenia na live-smoke; patrz research-doc.)
_USER_DOC_PUSH_WRITE_SCOPES = ("Files.ReadWrite", _FILE_REPLY_WRITE_SCOPE)
# Sufit rozmiaru ZRENDEROWANEGO dokumentu push-owanego do usera (KB). Treść od modelu; twardy limit
# domyka powierzchnię eksfiltracji przy wstrzyknięciu (jak odpowiedź plikiem, ADR 0026/0027).
_MAX_USER_DOC_KB_CEILING = 4096
# Zakresy delegowane wymagane do produkcyjnego M3 (ADR 0009, B1, bramka enable_meeting_transcript):
# odczyt TREŚCI transkryptu (``OnlineMeetingTranscript.Read.All``) oraz rozwiązanie spotkania po
# ``joinWebUrl`` (``OnlineMeetings.Read``). Oba wymagają ZGODY ADMINA (nowe zakresy, jeszcze nie
# skonsentowane — w przeciwieństwie do zakresów czatu/plików). Włączona bramka BEZ nich = martwa
# (403 przy pobraniu), więc walidacja żąda ich WPROST — fail-fast zamiast cichej, martwej bramki.
_MEETING_TRANSCRIPT_SCOPES = ("OnlineMeetingTranscript.Read.All", "OnlineMeetings.Read")


def _parse_watch_pairs(value: str) -> tuple[tuple[str, str], ...]:
    """Sparsuj ``team:channel,team:channel`` na krotki par (pomija niepełne wpisy)."""
    pairs: list[tuple[str, str]] = []
    for part in value.split(","):
        team, _, channel = part.strip().partition(":")
        if team.strip() and channel.strip():
            pairs.append((team.strip(), channel.strip()))
    return tuple(pairs)


@dataclass(frozen=True)
class TeamsGraphSettings:
    """Konfiguracja drzwi Teams w trybie DELEGOWANYM (ADR 0015) — polling kanału przez Graph.

    Bot działa jako ZALOGOWANY UŻYTKOWNIK (device-code MSAL), bez publicznego endpointu i
    bez rejestracji bota. ``client_id``/``tenant_id`` (ze strony aplikacji w Entra) są
    wymagane — ``validate`` odrzuca brak (koniec z placeholderem w kodzie). Sam obiekt nie
    trzyma sekretu (sekretem jest CACHE tokenu na dysku), więc bez pola ``repr=False``.

    ``watch`` (pary ``team_id:channel_id``) wyznacza nasłuchiwane kanały; pusty → tryb
    odkrywania (wypisz zespoły/kanały i zakończ). ``top_roots``/``top_replies`` ograniczają
    koszt API na rundę; wątek bez aktywności dłużej niż ``active_idle_hours`` przestaje być
    odpytywany o odpowiedzi (eksmisja).
    """

    client_id: str = ""
    tenant_id: str = ""
    scopes: tuple[str, ...] = _DEFAULT_TEAMS_GRAPH_SCOPES
    token_cache_path: Path = _DEFAULT_TEAMS_GRAPH_CACHE
    state_path: Path = _DEFAULT_TEAMS_GRAPH_STATE
    watch: tuple[tuple[str, str], ...] = ()
    poll_interval_s: int = 10
    top_roots: int = 20
    top_replies: int = 50
    active_idle_hours: int = 24
    # Limity załączników (ADR 0016): rozmiar pliku, liczba i ŁĄCZNY budżet na wiadomość.
    max_attachment_mb: int = 8
    max_attachments_per_message: int = 20  # równolegle wysłane pliki na wiadomość (= sufit MAX)
    max_total_attachment_mb: int = 20  # sumaryczny budżet base64 — chroni sufit 32 MB żądania API
    max_extract_mb: int = 50  # sufit pliku ekstrahowanego do tekstu (docx/xlsx/pptx/txt)
    max_image_edge_px: int = 2048  # dłuższa krawędź obrazu (px) — powyżej downscaling
    # Odpowiedź plikiem w wątku (ADR 0026, A′2) — OSOBNA bramka zapisu, domyślnie OFF (ADR 0006).
    enable_file_reply: bool = False
    max_file_reply_kb: int = 512  # sufit rozmiaru zrenderowanego pliku odpowiedzi
    # Skrzynka nadawcza rozmowy (ADR 0009 paczki). Oba limity WCHODZĄ w deklarowaną granicę
    # opóźnienia tury, więc muszą dać się nastroić razem z nią — inaczej dokument opisujący
    # sufit czasu rozjedzie się z kodem przy pierwszej zmianie.
    outbox_max_files_per_turn: int = 5
    outbox_max_seconds: float = 20.0
    # Push OBRAZU do rozmówcy 1:1 (ADR 0027, A′3) — OSOBNA bramka zapisu, domyślnie OFF (ADR 0006).
    enable_user_file_push: bool = False
    max_user_image_kb: int = 1024  # sufit rozmiaru obrazu push-owanego do usera
    # Push DOKUMENTU (md/txt/pdf/docx) do rozmówcy 1:1 (ADR 0027, plik) — OSOBNA bramka od
    # obrazowej, bo wymaga SZERSZEGO zakresu (Files.ReadWrite.All), domyślnie OFF (ADR 0006).
    enable_user_doc_push: bool = False
    max_user_doc_kb: int = 512  # sufit rozmiaru zrenderowanego dokumentu push-owanego do usera
    # Produkcyjne M3: pobranie transkryptu spotkania z Graph (ADR 0009, B1) — OSOBNA bramka,
    # domyślnie OFF, bo wymaga NOWYCH zakresów admina (patrz ``_MEETING_TRANSCRIPT_SCOPES``).
    enable_meeting_transcript: bool = False
    # Produkcyjne M3 — ZAPIS notatki komendą ``/notatka`` z drzwi Teams (ADR 0009 §4 / 0041). To
    # decyzja zaufania Gate-2 (zapis z mniej zaufanych drzwi), więc OSOBNA bramka, domyślnie OFF
    # (ADR 0006). Wymaga włączonego ``enable_meeting_transcript`` (skąd wziąć transkrypt).
    enable_meeting_note_write: bool = False
    # Autoryzacja nadawcy ``/notatka`` (B2 / ADR 0042): plik mapy tożsamości (może być TEN SAM
    # co worklogów). Rozwiązuje AAD id nadawcy na członka pionu; nieznany → odmowa (fail-closed).
    # Przy włączonej bramce zapisu WYMAGANY (walidacja) — bez niego „każdy pisze do wszystkiego".
    meeting_note_identities: Path = Path()
    # Async ``/notatka`` (B3 / ADR 0043): zamiast składać notatkę inline (blokuje poller na czas
    # transkrypt+Claude), router odsyła ACK natychmiast i liczy w tle, a wynik wrzuca do wątku.
    # OSOBNA bramka, domyślnie OFF (ADR 0006); wymaga włączonej bramki zapisu (to jej tryb).
    enable_meeting_note_async: bool = False
    # Górny limit RÓWNOLEGŁYCH notatek w tle (pula wątków) — sufit jednoczesnych wywołań Claude.
    meeting_note_async_workers: int = 2
    # Przechwycenie „zapisz to" z WĄTKU po @wzmiance bota (ADR 0048, F2). OSOBNA bramka zapisu,
    # domyślnie OFF (ADR 0006). Niezależna od transkryptu spotkania (materiałem jest treść wątku),
    # ale — jak /notatka — wymaga mapy tożsamości (autoryzacja B2 wbudowana w bramkę zapisu).
    # Tryb async współdzieli przełącznik ``enable_meeting_note_async`` (ta sama pula/poster).
    enable_thread_note_capture: bool = False
    # Autoryzacja ODCZYTU bazy wiedzy (ADR 0062): bramka członkostwa na TYPOWANYCH ścieżkach odczytu
    # (narzędzia agenta search_notes/get_note/list_projects wiązane per turę z nadawcą + komendy
    # /szukaj i /projekty). OSOBNY toggle, domyślnie OFF — inaczej niż zapis, gdzie autoryzacja jest
    # WBUDOWANA w bramkę zdolności (ADR 0042): odczyt nie ma bramki zdolności, na której mógłby
    # jechać (czytanie jest zachowaniem domyślnym), więc potrzebuje własnego przełącznika; domyślne
    # OFF to świadomy, bezpieczny rollout (luka otwarta do czasu uzupełnienia mapy tożsamości).
    # Włączony WYMAGA mapy tożsamości (ten sam plik co zapis, walidacja niżej). Powłoka
    # (cat/workmate-search po montażu ro) jest POZA zakresem — domknięcie wymaga montażu per-rozmowa
    # (infra ADR 0010); drzwi MCP też (pojedynczy zaufany operator, jak CLI w ADR 0042).
    enable_note_read_authz: bool = False
    # One-pager „ogarnij mnie na <projekt>" po @wzmiance bota (ADR 0051, F4). READ-ONLY (status +
    # notatki), więc NIE bramka zapisu — flaga staged rolloutu, domyślnie OFF. Bez wymogu mapy
    # tożsamości ani RW-montażu (nic nie zapisuje). Dostawa PDF ``| pdf`` reużywa kanału file-reply:
    # aktywna tylko przy dodatkowo włączonym ``enable_file_reply`` (jego zakres/sender); inaczej
    # ``| pdf`` degraduje do odpowiedzi tekstem.
    enable_project_brief: bool = False
    # Digest „co się zmieniło od <data>" po @wzmiance bota (ADR 0052, F5). READ-ONLY (fold zdarzeń
    # z warstwy spajającej), więc NIE bramka zapisu — flaga staged rolloutu, domyślnie OFF. Bez
    # wymogu tożsamości/RW-montażu. Dostawa PDF ``| pdf`` reużywa kanał file-reply (jak brief).
    enable_change_digest: bool = False
    # Narzędzie ``File(read)`` + odkładanie załączników użytkownika na dysk katalogu rozmowy
    # (ADR 0064). Domyślnie OFF jak KAŻDA bramka w tym projekcie (twarda reguła CLAUDE.md,
    # egzekwowana przez ``test_gates_closed_by_default``) — i zasłużenie, bo ta zdolność zapisuje
    # CUDZY plik na dysk floty i wstrzykuje jego treść do kontekstu modelu. Operator włącza ją
    # świadomie: jedna linia `.env` + recreate, tak samo jak powłokę. Wyłącznik ma też drugie
    # zastosowanie — ADR zapowiada ponowny pomiar użycia i USUNIĘCIE narzędzia, jeśli okaże się
    # martwe; bez flagi „wyłączenie" znaczyłoby wydanie nowego obrazu.
    enable_file_tool: bool = False
    # MUTACJA bazy wiedzy przez `File(edit)` (ADR 0065). Odwraca dotychczasową postawę
    # „create-only": do 0065 nie dało się zepsuć notatki, bo nie było czym. Domyślnie OFF.
    enable_note_mutation: bool = False
    # KASOWANIE notatek — osobno od edycji, bo ADR 0065 wiąże je z DZIAŁAJĄCĄ nocną kopią
    # wolumenu: migawka cofa jedną pomyłkę, przed złym dniem ratuje dopiero kopia poza
    # hostem. Włączenie bez sprawdzenia kopii jest tym, przed czym ta flaga ma chronić.
    enable_note_delete: bool = False
    # Strukturalne koperty T3 na treści OBCEJ (ADR 0066): plik, wynik narzędzia, tura
    # nadawcy spoza mapy. Domyślnie OFF jak każda bramka — włączona zmienia PROMPT każdej
    # tury (nagłówek tłumaczy znacznik) i kształt treści wysyłanej do modelu, więc operator
    # ma to włączyć świadomie i móc porównać zachowanie przed/po. Rozszczepienie nadawcy na
    # T1/T2 jedzie OSOBNO, za bramką odczytu notatek — zależy od kompletności identities.yaml.
    enable_trust_labels: bool = False
    # Polityka „czy w ogóle odpowiadać" (SZKIELET pod wielokanałowe wdrożenie WorkMate).
    # ``all`` (domyślnie) = zachowanie sprzed tej zmiany: odpowiedź na każdą wiadomość od
    # innego człowieka w kanałach z ``watch``. ``mention`` odpowiada tylko po @wzmiance bota
    # (lub gdy bot już jest aktywny w danym wątku — patrz ``selection.ReplyPolicy``), z
    # wyjątkiem kanałów z ``always_reply``, które zawsze zachowują się jak ``all``. Domyślne
    # ``all`` gwarantuje, że sam deploy tej zmiany NIC nie zmienia w produkcji.
    reply_policy: str = "all"
    # Kanały, które ZAWSZE odpowiadają (jak ``mode=all``), niezależnie od ``reply_policy`` —
    # ten sam format co ``watch`` (``team:channel,team:channel``); patrz ``_parse_watch_pairs``.
    always_reply: tuple[tuple[str, str], ...] = ()

    @property
    def authority(self) -> str:
        """URL authority MSAL dla aplikacji single-tenant (z ``tenant_id``)."""
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @classmethod
    def from_env(cls) -> TeamsGraphSettings:
        return cls(
            client_id=os.environ.get("WORKMATE_TEAMS_GRAPH_CLIENT_ID", ""),
            tenant_id=os.environ.get("WORKMATE_TEAMS_GRAPH_TENANT_ID", ""),
            scopes=_list_from_env("WORKMATE_TEAMS_GRAPH_SCOPES", _DEFAULT_TEAMS_GRAPH_SCOPES),
            token_cache_path=_path_from_env(
                "WORKMATE_TEAMS_GRAPH_TOKEN_CACHE", _DEFAULT_TEAMS_GRAPH_CACHE
            ),
            state_path=_path_from_env("WORKMATE_TEAMS_GRAPH_STATE", _DEFAULT_TEAMS_GRAPH_STATE),
            watch=_parse_watch_pairs(os.environ.get("WORKMATE_TEAMS_GRAPH_WATCH", "")),
            poll_interval_s=_int_from_env("WORKMATE_TEAMS_GRAPH_POLL_INTERVAL", 10),
            top_roots=_int_from_env("WORKMATE_TEAMS_GRAPH_TOP_ROOTS", 20),
            top_replies=_int_from_env("WORKMATE_TEAMS_GRAPH_TOP_REPLIES", 50),
            active_idle_hours=_int_from_env("WORKMATE_TEAMS_GRAPH_ACTIVE_IDLE_HOURS", 24),
            max_attachment_mb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENT_MB", 8),
            max_attachments_per_message=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENTS", 20),
            max_total_attachment_mb=_int_from_env(
                "WORKMATE_TEAMS_GRAPH_MAX_TOTAL_ATTACHMENT_MB", 20
            ),
            max_extract_mb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_EXTRACT_MB", 50),
            max_image_edge_px=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_IMAGE_EDGE", 2048),
            enable_file_reply=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY", default=False
            ),
            max_file_reply_kb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_FILE_REPLY_KB", 512),
            outbox_max_files_per_turn=_int_from_env("WORKMATE_TEAMS_GRAPH_OUTBOX_MAX_FILES", 5),
            outbox_max_seconds=float(_int_from_env("WORKMATE_TEAMS_GRAPH_OUTBOX_MAX_SECONDS", 20)),
            enable_user_file_push=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH", default=False
            ),
            max_user_image_kb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_USER_IMAGE_KB", 1024),
            enable_user_doc_push=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH", default=False
            ),
            max_user_doc_kb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_USER_DOC_KB", 512),
            enable_meeting_transcript=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT", default=False
            ),
            enable_meeting_note_write=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE", default=False
            ),
            meeting_note_identities=_path_from_env("WORKMATE_TEAMS_GRAPH_IDENTITIES", Path()),
            enable_meeting_note_async=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_ASYNC", default=False
            ),
            meeting_note_async_workers=_int_from_env(
                "WORKMATE_TEAMS_GRAPH_MEETING_NOTE_ASYNC_WORKERS", 2
            ),
            enable_thread_note_capture=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_THREAD_NOTE_CAPTURE", default=False
            ),
            enable_note_read_authz=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_READ_AUTHZ", default=False
            ),
            enable_file_tool=_bool_from_env("WORKMATE_TEAMS_GRAPH_ENABLE_FILE_TOOL", default=False),
            enable_note_mutation=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_MUTATION", default=False
            ),
            enable_note_delete=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_DELETE", default=False
            ),
            enable_trust_labels=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_TRUST_LABELS", default=False
            ),
            enable_project_brief=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_PROJECT_BRIEF", default=False
            ),
            enable_change_digest=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_CHANGE_DIGEST", default=False
            ),
            reply_policy=os.environ.get("WORKMATE_TEAMS_GRAPH_REPLY_POLICY", "all"),
            always_reply=_parse_watch_pairs(
                os.environ.get("WORKMATE_TEAMS_GRAPH_ALWAYS_REPLY", "")
            ),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy brak tożsamości aplikacji albo bezsensowne limity."""
        missing = [
            name
            for name, value in (
                ("WORKMATE_TEAMS_GRAPH_CLIENT_ID", self.client_id),
                ("WORKMATE_TEAMS_GRAPH_TENANT_ID", self.tenant_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Drzwi Teams (delegowane) wymagają tożsamości aplikacji Entra: brakuje "
                + ", ".join(missing)
                + " w środowisku/.env."
            )
        if not self.scopes:
            raise ValueError("WORKMATE_TEAMS_GRAPH_SCOPES nie może być puste.")
        if self.poll_interval_s < 1:
            raise ValueError(
                f"WORKMATE_TEAMS_GRAPH_POLL_INTERVAL musi być >= 1, jest: {self.poll_interval_s}."
            )
        if self.top_roots < 1:
            raise ValueError(
                f"WORKMATE_TEAMS_GRAPH_TOP_ROOTS musi być >= 1, jest: {self.top_roots}."
            )
        if self.top_replies < 1:
            raise ValueError(
                f"WORKMATE_TEAMS_GRAPH_TOP_REPLIES musi być >= 1, jest: {self.top_replies}."
            )
        # 0 eksmitowałoby każdy wątek natychmiast (koniec wielotury) — wymagamy >= 1.
        if self.active_idle_hours < 1:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ACTIVE_IDLE_HOURS musi być >= 1, jest: "
                f"{self.active_idle_hours}."
            )
        if not 1 <= self.max_attachment_mb <= _MAX_ATTACHMENT_MB_CEILING:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENT_MB musi być w zakresie "
                f"1..{_MAX_ATTACHMENT_MB_CEILING} (sufit API), jest: {self.max_attachment_mb}."
            )
        # Górny cap liczby chroni przed absurdalną wartością operatora (np. 1000).
        if not 1 <= self.max_attachments_per_message <= _MAX_ATTACHMENTS_PER_MESSAGE_CEILING:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENTS musi być w zakresie "
                f"1..{_MAX_ATTACHMENTS_PER_MESSAGE_CEILING}, jest: "
                f"{self.max_attachments_per_message}."
            )
        # Łączny budżet też w 1..32 (sufit żądania API). Nie wiążemy go z ``max_attachment_mb``:
        # plik większy niż budżet materializer i tak łagodnie zdegraduje do notki.
        if not 1 <= self.max_total_attachment_mb <= _MAX_ATTACHMENT_MB_CEILING:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MAX_TOTAL_ATTACHMENT_MB musi być w zakresie "
                f"1..{_MAX_ATTACHMENT_MB_CEILING}, jest: {self.max_total_attachment_mb}."
            )
        # Pliki ekstrahowane do tekstu nie zjadają budżetu base64, ale trzymamy górną barierę.
        if not 1 <= self.max_extract_mb <= _MAX_EXTRACT_MB_CEILING:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MAX_EXTRACT_MB musi być w zakresie "
                f"1..{_MAX_EXTRACT_MB_CEILING}, jest: {self.max_extract_mb}."
            )
        # Próg downscalingu obrazu: poniżej 256 px obraz byłby nieczytelny, powyżej 2576 px
        # model i tak skaluje po swojej stronie — trzymamy się użytecznego zakresu.
        if not _MIN_IMAGE_EDGE_PX <= self.max_image_edge_px <= _MAX_IMAGE_EDGE_PX_CEILING:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MAX_IMAGE_EDGE musi być w zakresie "
                f"{_MIN_IMAGE_EDGE_PX}..{_MAX_IMAGE_EDGE_PX_CEILING} (px), jest: "
                f"{self.max_image_edge_px}."
            )
        if self.enable_file_reply:
            # Bramka ON bez zakresu zapisu = 403 przy pierwszym uploadzie. Fail-fast na starcie
            # (jak przy Jira/GitHub write): włączona, ale martwa bramka byłaby footgunem (ADR 0026).
            if _FILE_REPLY_WRITE_SCOPE not in self.scopes:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY=true wymaga zakresu "
                    f"'{_FILE_REPLY_WRITE_SCOPE}' w WORKMATE_TEAMS_GRAPH_SCOPES (po nadaniu przez "
                    "admina usuń cache tokenu, by wymusić ponowną zgodę device-code)."
                )
            if not 1 <= self.max_file_reply_kb <= _MAX_FILE_REPLY_KB_CEILING:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_MAX_FILE_REPLY_KB musi być w zakresie "
                    f"1..{_MAX_FILE_REPLY_KB_CEILING}, jest: {self.max_file_reply_kb}."
                )
        if self.enable_user_file_push:
            # Bramka ON bez zakresów czatu = 403 przy pierwszym push-u. Fail-fast (ADR 0027):
            # zakresy są skonsentowane przez admina, ale muszą być na TOKENIE tych drzwi.
            missing = [s for s in _USER_PUSH_CHAT_SCOPES if s not in self.scopes]
            if missing:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH=true wymaga zakresów "
                    f"{', '.join(missing)} w WORKMATE_TEAMS_GRAPH_SCOPES (skonsentowane przez "
                    "admina; usuń cache tokenu, by wymusić ponowną zgodę device-code)."
                )
            if not 1 <= self.max_user_image_kb <= _MAX_USER_IMAGE_KB_CEILING:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_MAX_USER_IMAGE_KB musi być w zakresie "
                    f"1..{_MAX_USER_IMAGE_KB_CEILING}, jest: {self.max_user_image_kb}."
                )
        if self.enable_user_doc_push:
            # Bramka ON bez zakresów = 403 przy uploadzie/wysyłce. Fail-fast (ADR 0027, wariant
            # plikowy): zakresy CZATU (dostawa 1:1) ORAZ co najmniej JEDEN zapis do plików (upload
            # na własny OneDrive) — węższy Files.ReadWrite lub szerszy Files.ReadWrite.All.
            missing = [s for s in _USER_PUSH_CHAT_SCOPES if s not in self.scopes]
            if not any(s in self.scopes for s in _USER_DOC_PUSH_WRITE_SCOPES):
                missing.append("Files.ReadWrite (lub Files.ReadWrite.All)")
            if missing:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH=true wymaga zakresów "
                    f"{', '.join(missing)} w WORKMATE_TEAMS_GRAPH_SCOPES (skonsentowane przez "
                    "admina; usuń cache tokenu, by wymusić ponowną zgodę device-code)."
                )
            if not 1 <= self.max_user_doc_kb <= _MAX_USER_DOC_KB_CEILING:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_MAX_USER_DOC_KB musi być w zakresie "
                    f"1..{_MAX_USER_DOC_KB_CEILING}, jest: {self.max_user_doc_kb}."
                )
        if self.enable_meeting_transcript:
            # Bramka ON bez zakresów transkryptu = 403 przy pobraniu. Fail-fast (ADR 0009, B1):
            # to NOWE zakresy admina — dopóki nie nadane i nie ma ich na TOKENIE tych drzwi, bramka
            # byłaby martwa. Po nadaniu przez admina usuń cache tokenu, by wymusić ponowną zgodę.
            missing = [s for s in _MEETING_TRANSCRIPT_SCOPES if s not in self.scopes]
            if missing:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true wymaga zakresów "
                    f"{', '.join(missing)} w WORKMATE_TEAMS_GRAPH_SCOPES (nadaje ADMIN w Entra; "
                    "po nadaniu usuń cache tokenu, by wymusić ponowną zgodę device-code)."
                )
        if self.enable_meeting_note_write and not self.enable_meeting_transcript:
            # Zapis notatki komendą /notatka bez źródła transkryptu = martwa bramka. Fail-fast
            # (ADR 0009/0041): najpierw włącz transkrypt (i jego zakresy), potem zapis z drzwi.
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true wymaga też "
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true (skąd wziąć transkrypt)."
            )
        if self.enable_meeting_note_async and not self.enable_meeting_note_write:
            # Async to TRYB ścieżki zapisu /notatka, nie samodzielna zdolność — bez włączonego
            # zapisu nie ma czego wykonywać w tle. Fail-fast (ADR 0043).
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_ASYNC=true wymaga też "
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true (async = tryb zapisu)."
            )
        if self.enable_meeting_note_async and self.meeting_note_async_workers < 1:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MEETING_NOTE_ASYNC_WORKERS musi być ≥ 1, jest: "
                f"{self.meeting_note_async_workers}."
            )
        if self.enable_meeting_note_write and not self.meeting_note_identities.is_file():
            # Autoryzacja jest WBUDOWANA w bramkę zapisu (B2 / ADR 0042): bez mapy tożsamości
            # każdy nadawca pisałby do dowolnego projektu (ryzyko KRYTYCZNE). Fail-fast — nie
            # pozwalamy włączyć zapisu bez źródła autoryzacji (nie osobny toggle, bo domyślne
            # „OFF autoryzacji" = „każdy pisze"). Może wskazywać TEN SAM plik co worklogi.
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true wymaga "
                "WORKMATE_TEAMS_GRAPH_IDENTITIES = ścieżka do mapy tożsamości (członkostwo "
                f"autoryzuje zapis, ADR 0042); brak pliku: {self.meeting_note_identities}."
            )
        if self.enable_thread_note_capture and not self.meeting_note_identities.is_file():
            # „Zapisz to" pisze notatkę z drzwi Teams (ADR 0048) → autoryzacja WBUDOWANA w bramkę
            # (B2 / ADR 0042), jak /notatka: bez mapy tożsamości każdy nadawca zapisałby wątek do
            # dowolnego projektu. Fail-fast (ten sam plik co /notatka i worklogi). Transkryptu NIE
            # wymaga — materiałem jest treść wątku, nie WebVTT spotkania.
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_THREAD_NOTE_CAPTURE=true wymaga "
                "WORKMATE_TEAMS_GRAPH_IDENTITIES = ścieżka do mapy tożsamości (członkostwo "
                f"autoryzuje zapis, ADR 0042/0048); brak pliku: {self.meeting_note_identities}."
            )
        if self.enable_note_read_authz and not self.meeting_note_identities.is_file():
            # Odczyt bazy wiedzy bramkowany członkostwem (ADR 0062): bez mapy tożsamości nie ma po
            # czym rozpoznać nadawcy, więc bramka nie miałaby jak działać. Fail-fast — nie pozwalamy
            # włączyć autoryzacji odczytu bez źródła tożsamości (ten sam plik co zapis i worklogi).
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_READ_AUTHZ=true wymaga "
                "WORKMATE_TEAMS_GRAPH_IDENTITIES = ścieżka do mapy tożsamości (członkostwo "
                f"autoryzuje odczyt, ADR 0062); brak pliku: {self.meeting_note_identities}."
            )
        if self.enable_note_mutation and not self.meeting_note_identities.is_file():
            # MUTACJA bazy wiedzy (ADR 0065) autoryzowana tak samo jak zapis (B2 / ADR 0042):
            # bez mapy tożsamości nie ma komu przypisać zmiany ani kogo zapytać o potwierdzenie.
            # Do tej pory jedynym sygnałem był ``logger.error`` w wiringu, a narzędzie po prostu
            # nie powstawało — bramka wyglądała na włączoną i nic nie robiła.
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_MUTATION=true wymaga "
                "WORKMATE_TEAMS_GRAPH_IDENTITIES = ścieżka do mapy tożsamości (członkostwo "
                f"autoryzuje mutację, ADR 0042/0065); brak pliku: {self.meeting_note_identities}."
            )
        if self.enable_note_delete and not self.enable_note_mutation:
            # Kasowanie ma WŁASNĄ bramkę, ale jedzie tą samą ścieżką (``NoteMutationService``,
            # parametr ``allow_delete``) — bez mutacji jest flagą bez efektu. Fail-fast zamiast
            # cichej, sprzecznej konfiguracji (jak ``ENABLE_CHANNEL_THREADING`` bez kanału).
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_DELETE=true wymaga też "
                "WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_MUTATION=true (kasowanie idzie tą samą ścieżką)."
            )
        if self.enable_note_mutation and not self.enable_file_tool:
            # ``File(edit)`` to AKCJA narzędzia plikowego (ADR 0064/0065) — bez ``ENABLE_FILE_TOOL``
            # narzędzie nie wchodzi na listę modelu wcale, więc mutacja jest martwa. Fail-fast.
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_MUTATION=true wymaga też "
                "WORKMATE_TEAMS_GRAPH_ENABLE_FILE_TOOL=true (mutacja to akcja narzędzia File)."
            )
        if self.reply_policy not in ("all", "mention"):
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_REPLY_POLICY musi być 'all' albo 'mention', jest: "
                f"{self.reply_policy!r}."
            )
        # ``always_reply`` ma sens tylko dla kanałów faktycznie nasłuchiwanych — para spoza
        # ``watch`` to najczęściej literówka (fail-fast zamiast cichej, martwej konfiguracji).
        stray = [pair for pair in self.always_reply if pair not in self.watch]
        if stray:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ALWAYS_REPLY zawiera pary spoza WORKMATE_TEAMS_GRAPH_WATCH: "
                + ", ".join(f"{team}:{channel}" for team, channel in stray)
                + "."
            )
