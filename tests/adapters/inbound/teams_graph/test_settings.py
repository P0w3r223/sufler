"""Testy TeamsGraphSettings — bramka tożsamości aplikacji Entra i sensowność limitów.

``validate`` to granica startu drzwi delegowanych (ADR 0015): bez ``client_id``/
``tenant_id`` proces nie ma jak się zalogować (device-code), więc lepiej nie ruszyć niż
wystartować z placeholderem. Ustawienia są czyste, więc testujemy je bez MSAL i bez sieci.
"""

from __future__ import annotations

import pytest

from workmate.config import TeamsGraphSettings

_TEAMS_GRAPH_VARS = (
    "WORKMATE_TEAMS_GRAPH_CLIENT_ID",
    "WORKMATE_TEAMS_GRAPH_TENANT_ID",
    "WORKMATE_TEAMS_GRAPH_SCOPES",
    "WORKMATE_TEAMS_GRAPH_TOKEN_CACHE",
    "WORKMATE_TEAMS_GRAPH_STATE",
    "WORKMATE_TEAMS_GRAPH_WATCH",
    "WORKMATE_TEAMS_GRAPH_POLL_INTERVAL",
    "WORKMATE_TEAMS_GRAPH_TOP_ROOTS",
    "WORKMATE_TEAMS_GRAPH_TOP_REPLIES",
    "WORKMATE_TEAMS_GRAPH_ACTIVE_IDLE_HOURS",
    "WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENT_MB",
    "WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENTS",
    "WORKMATE_TEAMS_GRAPH_MAX_TOTAL_ATTACHMENT_MB",
)


def _valid(**overrides: object) -> TeamsGraphSettings:
    """Config przechodzący ``validate`` — testy nadpisują tylko badane pole."""
    base: dict[str, object] = {"client_id": "app-1", "tenant_id": "tenant-1"}
    base.update(overrides)
    return TeamsGraphSettings(**base)  # type: ignore[arg-type]


# --- validate: tożsamość aplikacji -----------------------------------------


def test_validate_passes_with_full_identity():
    _valid().validate()  # nie rzuca


def test_validate_rejects_missing_both_identity_fields():
    with pytest.raises(ValueError) as exc:
        TeamsGraphSettings().validate()

    msg = str(exc.value)
    assert "WORKMATE_TEAMS_GRAPH_CLIENT_ID" in msg
    assert "WORKMATE_TEAMS_GRAPH_TENANT_ID" in msg


def test_validate_names_only_the_missing_client_id():
    """Komunikat wskazuje DOKŁADNIE brakujące pole — nie myli o czym mowa."""
    with pytest.raises(ValueError) as exc:
        TeamsGraphSettings(tenant_id="tenant-1").validate()

    msg = str(exc.value)
    assert "WORKMATE_TEAMS_GRAPH_CLIENT_ID" in msg
    assert "WORKMATE_TEAMS_GRAPH_TENANT_ID" not in msg


def test_validate_names_only_the_missing_tenant_id():
    with pytest.raises(ValueError) as exc:
        TeamsGraphSettings(client_id="app-1").validate()

    msg = str(exc.value)
    assert "WORKMATE_TEAMS_GRAPH_TENANT_ID" in msg
    assert "WORKMATE_TEAMS_GRAPH_CLIENT_ID" not in msg


# --- validate: sensowność limitów ------------------------------------------


def test_validate_rejects_empty_scopes():
    with pytest.raises(ValueError, match="SCOPES"):
        _valid(scopes=()).validate()


@pytest.mark.parametrize(
    ("field", "var_fragment"),
    [
        ("poll_interval_s", "POLL_INTERVAL"),
        ("top_roots", "TOP_ROOTS"),
        ("top_replies", "TOP_REPLIES"),
        ("active_idle_hours", "ACTIVE_IDLE_HOURS"),
    ],
)
def test_validate_rejects_below_one_limits(field, var_fragment):
    """Każdy limit < 1 jest odrzucany — 0 kanałów/rund/godzin nie ma sensu."""
    with pytest.raises(ValueError, match=var_fragment):
        _valid(**{field: 0}).validate()


def test_validate_rejects_zero_active_idle_because_it_kills_multiturn():
    """0 h eksmitowałoby każdy wątek natychmiast po rundzie — koniec wielotury."""
    with pytest.raises(ValueError, match="ACTIVE_IDLE_HOURS"):
        _valid(active_idle_hours=0).validate()


# --- validate: limity załączników (ADR 0016) --------------------------------


@pytest.mark.parametrize("mb", [0, 25, -1], ids=["zero", "above_ceiling", "negative"])
def test_validate_rejects_attachment_mb_out_of_range(mb):
    """Rozmiar pojedynczego pliku musi mieścić się w 1..24 MB (base64 ≈ 32 MB request)."""
    with pytest.raises(ValueError, match="MAX_ATTACHMENT_MB"):
        _valid(max_attachment_mb=mb).validate()


@pytest.mark.parametrize("mb", [1, 8, 24], ids=["min", "default", "ceiling"])
def test_validate_accepts_attachment_mb_within_range(mb):
    _valid(max_attachment_mb=mb).validate()  # nie rzuca


def test_validate_rejects_zero_attachments_per_message():
    with pytest.raises(ValueError, match="MAX_ATTACHMENTS"):
        _valid(max_attachments_per_message=0).validate()


def test_validate_accepts_one_attachment_per_message():
    _valid(max_attachments_per_message=1).validate()  # nie rzuca


def test_validate_rejects_attachments_count_above_ceiling():
    """Górny cap chroni przed absurdalną wartością operatora (np. 1000)."""
    with pytest.raises(ValueError, match="MAX_ATTACHMENTS"):
        _valid(max_attachments_per_message=21).validate()


@pytest.mark.parametrize("mb", [0, 25, -1], ids=["zero", "above_ceiling", "negative"])
def test_validate_rejects_total_attachment_mb_out_of_range(mb):
    """Łączny budżet też musi mieścić się w 1..24 MB (base64 ≈ 32 MB request)."""
    with pytest.raises(ValueError, match="MAX_TOTAL_ATTACHMENT_MB"):
        _valid(max_total_attachment_mb=mb).validate()


def test_validate_accepts_total_attachment_mb_within_range():
    _valid(max_total_attachment_mb=24).validate()  # nie rzuca


# --- domyślne zakresy: pobieranie plików z SharePoint (ADR 0016) ------------


def test_default_scopes_include_file_and_site_read():
    """Pobranie plików-załączników w SharePoint wymaga ``Files.Read.All``/``Sites.Read.All``."""
    scopes = TeamsGraphSettings().scopes

    assert "Files.Read.All" in scopes
    assert "Sites.Read.All" in scopes


def test_from_env_defaults_attachment_limits(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)

    settings = TeamsGraphSettings.from_env()

    assert settings.max_attachment_mb == 8
    assert settings.max_attachments_per_message == 20
    assert settings.max_total_attachment_mb == 20


def test_from_env_reads_attachment_limits(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENT_MB", "16")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENTS", "3")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_MAX_TOTAL_ATTACHMENT_MB", "24")

    settings = TeamsGraphSettings.from_env()

    assert settings.max_attachment_mb == 16
    assert settings.max_attachments_per_message == 3
    assert settings.max_total_attachment_mb == 24


# --- validate: bramka odpowiedzi plikiem (ADR 0026, A′2) --------------------

_WRITE_SCOPE = "Files.ReadWrite.All"


def _with_write_scope(**overrides: object) -> TeamsGraphSettings:
    scopes = (*TeamsGraphSettings().scopes, _WRITE_SCOPE)
    return _valid(scopes=scopes, enable_file_reply=True, **overrides)


def test_validate_file_reply_off_needs_no_write_scope():
    """Domyślnie OFF: brak ``Files.ReadWrite.All`` w zakresach jest w porządku (ADR 0006)."""
    _valid().validate()  # nie rzuca, choć domyślne zakresy nie mają zapisu


def test_validate_file_reply_on_requires_write_scope():
    """Bramka ON bez zakresu zapisu = 403 przy uploadzie → fail-fast (nie cicha martwa bramka)."""
    with pytest.raises(ValueError, match="Files.ReadWrite.All"):
        _valid(enable_file_reply=True).validate()


def test_validate_file_reply_on_with_write_scope_passes():
    _with_write_scope().validate()  # nie rzuca


@pytest.mark.parametrize("kb", [0, 5000, -1], ids=["zero", "above_ceiling", "negative"])
def test_validate_rejects_file_reply_kb_out_of_range(kb):
    with pytest.raises(ValueError, match="MAX_FILE_REPLY_KB"):
        _with_write_scope(max_file_reply_kb=kb).validate()


def test_from_env_defaults_file_reply_off(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY", raising=False)

    settings = TeamsGraphSettings.from_env()

    assert settings.enable_file_reply is False
    assert settings.max_file_reply_kb == 512


# --- validate: bramka push-u obrazu 1:1 (ADR 0027, A′3) --------------------

_CHAT_SCOPES = ("Chat.Create", "ChatMessage.Send")


def _with_chat_scopes(**overrides: object) -> TeamsGraphSettings:
    scopes = (*TeamsGraphSettings().scopes, *_CHAT_SCOPES)
    return _valid(scopes=scopes, enable_user_file_push=True, **overrides)


def test_validate_user_push_off_needs_no_chat_scopes():
    """Domyślnie OFF: brak zakresów czatu w scopes jest w porządku (ADR 0006)."""
    _valid().validate()  # nie rzuca, choć domyślne zakresy nie mają Chat.Create/ChatMessage.Send


def test_validate_user_push_on_requires_both_chat_scopes():
    """Bramka ON bez zakresów czatu = 403 przy push-u → fail-fast (nie cicha martwa bramka)."""
    with pytest.raises(ValueError) as exc:
        _valid(enable_user_file_push=True).validate()

    msg = str(exc.value)
    assert "Chat.Create" in msg
    assert "ChatMessage.Send" in msg


def test_validate_user_push_on_names_only_the_missing_scope():
    """Komunikat wskazuje DOKŁADNIE brakujący zakres — obecny nie jest wymieniany."""
    scopes = (*TeamsGraphSettings().scopes, "Chat.Create")  # brakuje tylko ChatMessage.Send
    with pytest.raises(ValueError) as exc:
        _valid(scopes=scopes, enable_user_file_push=True).validate()

    msg = str(exc.value)
    assert "ChatMessage.Send" in msg
    assert "Chat.Create" not in msg


def test_validate_user_push_on_with_chat_scopes_passes():
    _with_chat_scopes().validate()  # nie rzuca


@pytest.mark.parametrize("kb", [0, 5000, -1], ids=["zero", "above_ceiling", "negative"])
def test_validate_rejects_user_image_kb_out_of_range(kb):
    with pytest.raises(ValueError, match="MAX_USER_IMAGE_KB"):
        _with_chat_scopes(max_user_image_kb=kb).validate()


@pytest.mark.parametrize("kb", [1, 1024, 4096], ids=["min", "default", "ceiling"])
def test_validate_accepts_user_image_kb_within_range(kb):
    _with_chat_scopes(max_user_image_kb=kb).validate()  # nie rzuca


def test_validate_file_reply_and_user_push_gates_are_independent():
    """Push obrazu (zakresy czatu) NIE wymaga zakresu zapisu plików — osobne bramki (ADR 0027)."""
    scopes = (*TeamsGraphSettings().scopes, *_CHAT_SCOPES)  # bez Files.ReadWrite.All
    _valid(scopes=scopes, enable_user_file_push=True).validate()  # nie rzuca


def test_from_env_defaults_user_push_off(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH", raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_MAX_USER_IMAGE_KB", raising=False)

    settings = TeamsGraphSettings.from_env()

    assert settings.enable_user_file_push is False
    assert settings.max_user_image_kb == 1024


def test_from_env_reads_user_push_gate(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH", "true")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_MAX_USER_IMAGE_KB", "2048")

    settings = TeamsGraphSettings.from_env()

    assert settings.enable_user_file_push is True
    assert settings.max_user_image_kb == 2048


# --- validate: bramka push-u DOKUMENTU 1:1 (ADR 0027, wariant plikowy) --------------------


def _with_doc_scopes(**overrides: object) -> TeamsGraphSettings:
    # Dokument wymaga SZERZEJ: zakresy czatu (dostawa 1:1) ORAZ zapis (upload na OneDrive).
    scopes = (*TeamsGraphSettings().scopes, *_CHAT_SCOPES, _WRITE_SCOPE)
    return _valid(scopes=scopes, enable_user_doc_push=True, **overrides)


def test_validate_doc_push_off_needs_no_scopes():
    """Domyślnie OFF: brak zakresów czatu/zapisu jest w porządku (ADR 0006)."""
    _valid().validate()  # nie rzuca


def test_validate_doc_push_on_requires_chat_and_write_scopes():
    """Bramka ON bez zakresów = 403 przy uploadzie/wysyłce → fail-fast (nie cicha martwa bramka)."""
    with pytest.raises(ValueError) as exc:
        _valid(enable_user_doc_push=True).validate()

    msg = str(exc.value)
    assert "Chat.Create" in msg
    assert "ChatMessage.Send" in msg
    assert "Files.ReadWrite.All" in msg


def test_validate_doc_push_on_with_chat_but_missing_write_scope_names_write():
    """SZERZEJ niż obraz: same zakresy czatu nie wystarczą — brak zapisu jest wskazany."""
    scopes = (*TeamsGraphSettings().scopes, *_CHAT_SCOPES)  # brak Files.ReadWrite.All
    with pytest.raises(ValueError) as exc:
        _valid(scopes=scopes, enable_user_doc_push=True).validate()

    msg = str(exc.value)
    assert "Files.ReadWrite.All" in msg
    assert "Chat.Create" not in msg


def test_validate_doc_push_on_with_all_scopes_passes():
    _with_doc_scopes().validate()  # nie rzuca (szerszy Files.ReadWrite.All zaakceptowany)


def test_validate_doc_push_on_accepts_narrower_files_readwrite():
    """Least-privilege: upload idzie na WŁASNY OneDrive → wystarcza węższy ``Files.ReadWrite``."""
    scopes = (*TeamsGraphSettings().scopes, *_CHAT_SCOPES, "Files.ReadWrite")
    _valid(scopes=scopes, enable_user_doc_push=True).validate()  # nie rzuca


@pytest.mark.parametrize("kb", [0, 5000, -1], ids=["zero", "above_ceiling", "negative"])
def test_validate_rejects_user_doc_kb_out_of_range(kb):
    with pytest.raises(ValueError, match="MAX_USER_DOC_KB"):
        _with_doc_scopes(max_user_doc_kb=kb).validate()


@pytest.mark.parametrize("kb", [1, 512, 4096], ids=["min", "default", "ceiling"])
def test_validate_accepts_user_doc_kb_within_range(kb):
    _with_doc_scopes(max_user_doc_kb=kb).validate()  # nie rzuca


def test_validate_doc_push_and_image_push_gates_are_independent():
    """Obraz idzie inline (bez zapisu), dokument wymaga zapisu — osobne bramki (ADR 0027)."""
    # Obraz ON z samymi zakresami czatu przechodzi, choć bramka dokumentu wymagałaby zapisu.
    scopes = (*TeamsGraphSettings().scopes, *_CHAT_SCOPES)
    _valid(scopes=scopes, enable_user_file_push=True).validate()  # nie rzuca


def test_from_env_defaults_doc_push_off(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH", raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_MAX_USER_DOC_KB", raising=False)

    settings = TeamsGraphSettings.from_env()

    assert settings.enable_user_doc_push is False
    assert settings.max_user_doc_kb == 512


def test_from_env_reads_doc_push_gate(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH", "true")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_MAX_USER_DOC_KB", "256")

    settings = TeamsGraphSettings.from_env()

    assert settings.enable_user_doc_push is True
    assert settings.max_user_doc_kb == 256


# --- validate: bramka transkryptu spotkania (M3 produkcyjnie, ADR 0009, B1) --

_MEETING_SCOPES = ("OnlineMeetingTranscript.Read.All", "OnlineMeetings.Read")


def test_validate_meeting_transcript_off_needs_no_scopes():
    """Bramka OFF nie żąda zakresów M3 — domyślny config przechodzi."""
    _valid(enable_meeting_transcript=False).validate()  # nie rzuca


def test_validate_meeting_transcript_on_requires_transcript_scopes():
    with pytest.raises(ValueError) as exc:
        _valid(enable_meeting_transcript=True).validate()

    msg = str(exc.value)
    assert "OnlineMeetingTranscript.Read.All" in msg
    assert "OnlineMeetings.Read" in msg


def test_validate_meeting_transcript_on_names_only_the_missing_scope():
    scopes = (*TeamsGraphSettings().scopes, "OnlineMeetings.Read")  # brakuje tylko treści
    with pytest.raises(ValueError) as exc:
        _valid(scopes=scopes, enable_meeting_transcript=True).validate()

    msg = str(exc.value)
    assert "OnlineMeetingTranscript.Read.All" in msg
    assert "OnlineMeetings.Read" not in msg


def test_validate_meeting_transcript_on_with_scopes_passes():
    scopes = (*TeamsGraphSettings().scopes, *_MEETING_SCOPES)
    _valid(scopes=scopes, enable_meeting_transcript=True).validate()  # nie rzuca


def test_from_env_defaults_meeting_transcript_off(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT", raising=False)

    assert TeamsGraphSettings.from_env().enable_meeting_transcript is False


def test_from_env_reads_meeting_transcript_gate(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT", "true")

    assert TeamsGraphSettings.from_env().enable_meeting_transcript is True


def _with_meeting_scopes(**overrides: object) -> TeamsGraphSettings:
    scopes = (*TeamsGraphSettings().scopes, *_MEETING_SCOPES)
    return _valid(scopes=scopes, enable_meeting_transcript=True, **overrides)


def test_validate_note_write_off_needs_no_transcript():
    """Bramka zapisu OFF nie wymaga transkryptu — domyślny config przechodzi."""
    _valid(enable_meeting_note_write=False).validate()  # nie rzuca


def test_validate_note_write_on_requires_transcript_gate():
    # Zapis bez źródła transkryptu = martwa bramka → fail-fast (ADR 0009/0041).
    with pytest.raises(ValueError, match="ENABLE_MEETING_TRANSCRIPT=true"):
        _valid(enable_meeting_note_write=True, enable_meeting_transcript=False).validate()


def test_validate_note_write_on_with_transcript_and_scopes_passes(tmp_path):
    # Zapis wymaga też mapy tożsamości (autoryzacja członkostwa, B2 / ADR 0042).
    identities = tmp_path / "identities.yaml"
    identities.write_text("", encoding="utf-8")
    _with_meeting_scopes(
        enable_meeting_note_write=True, meeting_note_identities=identities
    ).validate()  # nie rzuca


def test_validate_note_write_on_without_identities_fails():
    # Bramka zapisu ON bez mapy tożsamości = „każdy pisze do wszystkiego" → fail-fast (ADR 0042).
    with pytest.raises(ValueError, match="WORKMATE_TEAMS_GRAPH_IDENTITIES"):
        _with_meeting_scopes(enable_meeting_note_write=True).validate()


def test_validate_note_read_authz_off_passes():
    """Bramka odczytu OFF (domyślnie) nie wymaga mapy tożsamości — config przechodzi."""
    _valid(enable_note_read_authz=False).validate()  # nie rzuca


def test_validate_note_read_authz_on_without_identities_fails():
    # Bramka ODCZYTU ON bez mapy tożsamości = nie ma po czym rozpoznać nadawcy → fail-fast (0062).
    # Nie wymaga transkryptu (to odczyt) — sam brak identities wystarcza do błędu.
    with pytest.raises(ValueError, match="ENABLE_NOTE_READ_AUTHZ"):
        _valid(enable_note_read_authz=True).validate()


def test_validate_note_read_authz_on_with_identities_passes(tmp_path):
    # Z mapą tożsamości bramka odczytu przechodzi (ten sam plik co zapis).
    identities = tmp_path / "identities.yaml"
    identities.write_text("", encoding="utf-8")
    _valid(enable_note_read_authz=True, meeting_note_identities=identities).validate()  # nie rzuca


def test_from_env_reads_note_read_authz_gate(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_READ_AUTHZ", "true")

    assert TeamsGraphSettings.from_env().enable_note_read_authz is True


def test_from_env_reads_identities_path(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_IDENTITIES", "/etc/workmate/identities.yaml")

    assert str(TeamsGraphSettings.from_env().meeting_note_identities).endswith("identities.yaml")


def test_from_env_defaults_note_write_off(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE", raising=False)

    assert TeamsGraphSettings.from_env().enable_meeting_note_write is False


def test_from_env_reads_note_write_gate(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE", "true")

    assert TeamsGraphSettings.from_env().enable_meeting_note_write is True


# --- async /notatka (B3 / ADR 0043) -----------------------------------------


def _note_write_on(tmp_path, **overrides):
    """Config z włączoną bramką zapisu (scopes + transkrypt + mapa tożsamości) — baza pod async."""
    identities = tmp_path / "identities.yaml"
    identities.write_text("", encoding="utf-8")
    return _with_meeting_scopes(
        enable_meeting_note_write=True, meeting_note_identities=identities, **overrides
    )


def test_validate_async_without_write_fails(tmp_path):
    # Async to TRYB zapisu — bez włączonego zapisu nie ma czego wykonywać w tle (fail-fast).
    with pytest.raises(ValueError, match="ENABLE_MEETING_NOTE_ASYNC"):
        _with_meeting_scopes(
            enable_meeting_note_write=False, enable_meeting_note_async=True
        ).validate()


def test_validate_async_on_with_write_passes(tmp_path):
    _note_write_on(tmp_path, enable_meeting_note_async=True).validate()  # nie rzuca


def test_validate_async_workers_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="ASYNC_WORKERS"):
        _note_write_on(
            tmp_path, enable_meeting_note_async=True, meeting_note_async_workers=0
        ).validate()


def test_from_env_defaults_async_off_and_workers(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_ASYNC", raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_MEETING_NOTE_ASYNC_WORKERS", raising=False)

    settings = TeamsGraphSettings.from_env()
    assert settings.enable_meeting_note_async is False
    assert settings.meeting_note_async_workers == 2


def test_from_env_reads_async_gate_and_workers(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_ASYNC", "true")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_MEETING_NOTE_ASYNC_WORKERS", "5")

    settings = TeamsGraphSettings.from_env()
    assert settings.enable_meeting_note_async is True
    assert settings.meeting_note_async_workers == 5


# --- „zapisz to": przechwycenie wątku (F2 / ADR 0048) ----------------------


def test_validate_thread_note_capture_off_needs_no_identities():
    """Bramka OFF nie żąda mapy tożsamości — domyślny config przechodzi."""
    _valid(enable_thread_note_capture=False).validate()  # nie rzuca


def test_validate_thread_note_capture_on_without_identities_fails():
    # Zapis z wątku ma WBUDOWANĄ autoryzację (B2 / ADR 0042): bez mapy tożsamości każdy nadawca
    # zapisałby wątek do dowolnego projektu → fail-fast (jak /notatka).
    with pytest.raises(ValueError, match="ENABLE_THREAD_NOTE_CAPTURE"):
        _valid(enable_thread_note_capture=True).validate()


def test_validate_thread_note_capture_on_with_identities_passes_without_transcript(tmp_path):
    # ON wymaga TYLKO mapy tożsamości — materiałem jest treść WĄTKU, NIE transkrypt WebVTT
    # (inaczej niż /notatka). Bramka transkryptu może zostać OFF.
    identities = tmp_path / "identities.yaml"
    identities.write_text("", encoding="utf-8")
    _valid(
        enable_thread_note_capture=True,
        enable_meeting_transcript=False,
        meeting_note_identities=identities,
    ).validate()  # nie rzuca


def test_from_env_defaults_thread_note_capture_off(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_THREAD_NOTE_CAPTURE", raising=False)

    assert TeamsGraphSettings.from_env().enable_thread_note_capture is False


def test_from_env_reads_thread_note_capture_gate(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_THREAD_NOTE_CAPTURE", "true")

    assert TeamsGraphSettings.from_env().enable_thread_note_capture is True


# --- one-pager „ogarnij mnie na <projekt>" (F4 / ADR 0051) ------------------


def test_validate_project_brief_on_needs_no_identities_or_scopes():
    # One-pager jest READ-ONLY (status + notatki): włączenie NIE wymaga mapy tożsamości, zakresu
    # zapisu ani transkryptu — inaczej niż bramki zapisu. Domyślny config z samą flagą przechodzi.
    _valid(enable_project_brief=True).validate()  # nie rzuca


def test_from_env_defaults_project_brief_off(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_PROJECT_BRIEF", raising=False)

    assert TeamsGraphSettings.from_env().enable_project_brief is False


def test_from_env_reads_project_brief_gate(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_PROJECT_BRIEF", "true")

    assert TeamsGraphSettings.from_env().enable_project_brief is True


# --- digest „co się zmieniło od <data>" (F5 / ADR 0052) ---------------------


def test_validate_change_digest_on_needs_no_identities_or_scopes():
    # Digest jest READ-ONLY (fold zdarzeń): włączenie NIE wymaga tożsamości/zakresu/transkryptu.
    _valid(enable_change_digest=True).validate()  # nie rzuca


def test_from_env_defaults_change_digest_off(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("WORKMATE_TEAMS_GRAPH_ENABLE_CHANGE_DIGEST", raising=False)

    assert TeamsGraphSettings.from_env().enable_change_digest is False


def test_from_env_reads_change_digest_gate(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ENABLE_CHANGE_DIGEST", "true")

    assert TeamsGraphSettings.from_env().enable_change_digest is True


# --- from_env ---------------------------------------------------------------


def test_from_env_defaults_when_unset(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)

    settings = TeamsGraphSettings.from_env()

    assert (settings.client_id, settings.tenant_id) == ("", "")
    assert settings.watch == ()  # brak WATCH → tryb odkrywania
    assert (settings.poll_interval_s, settings.top_roots, settings.top_replies) == (
        10,
        20,
        50,
    )
    assert settings.active_idle_hours == 24


def test_from_env_parses_watch_pairs(monkeypatch):
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_WATCH", "team-a:chan-1, team-b:chan-2")

    settings = TeamsGraphSettings.from_env()

    assert settings.watch == (("team-a", "chan-1"), ("team-b", "chan-2"))


def test_from_env_skips_incomplete_watch_entries(monkeypatch):
    """Wpis bez ``:channel`` jest pomijany — nie da się z niego zbudować pary."""
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_WATCH", "team-a:chan-1,broken-entry")

    settings = TeamsGraphSettings.from_env()

    assert settings.watch == (("team-a", "chan-1"),)


def test_from_env_reads_identity_and_limits(monkeypatch):
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_CLIENT_ID", "app-xyz")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_TENANT_ID", "tenant-xyz")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_POLL_INTERVAL", "5")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_TOP_ROOTS", "3")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_TOP_REPLIES", "7")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_ACTIVE_IDLE_HOURS", "48")

    settings = TeamsGraphSettings.from_env()

    assert settings.client_id == "app-xyz"
    assert settings.tenant_id == "tenant-xyz"
    assert settings.poll_interval_s == 5
    assert settings.top_roots == 3
    assert settings.top_replies == 7
    assert settings.active_idle_hours == 48


def test_from_env_then_validate_accepts_full_identity(monkeypatch):
    """Ścieżka startowa app.py::main: env z tożsamością daje config, który przechodzi."""
    for var in _TEAMS_GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_CLIENT_ID", "app-1")
    monkeypatch.setenv("WORKMATE_TEAMS_GRAPH_TENANT_ID", "tenant-1")

    TeamsGraphSettings.from_env().validate()  # nie rzuca


def test_authority_url_is_single_tenant_from_tenant_id():
    settings = TeamsGraphSettings(client_id="app-1", tenant_id="tenant-1")

    assert settings.authority == "https://login.microsoftonline.com/tenant-1"
