"""Testy adaptera wyjściowego załącznika Graph (HttpxGraphFileSender, ADR 0026).

Sedno: upload = ``GET …/filesFolder`` → ``PUT …/content`` (surowe bajty); załącznik w wątku =
POST z tablicą attachments (typ reference) związaną z treścią przez ``<attachment id="GUID">``,
gdzie GUID pochodzi z ``eTag`` wgranego pliku. Ponawianie: 429 zawsze; wysłanie odpowiedzi z plikiem
NIGDY (duplikat załącznika), odczyt folderu i upload — tak (bezpieczne do powtórzenia).
"""

from __future__ import annotations

import json

import httpx
import pytest

from workmate.adapters.outbound.graph_file_sender import HttpxGraphFileSender
from workmate.core.errors import ThreadRootGone
from workmate.core.ports.file_output import TeamsFileSender, UploadedFile

_ETAG = '"{2318B4D5-1111-2222-3333-444455556666},1"'
_GUID = "2318B4D5-1111-2222-3333-444455556666"


def _sender(handler, *, token: str = "access-token") -> HttpxGraphFileSender:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    # sleep=no-op: backoff jest realny (sekundy), w testach nie odczekujemy.
    return HttpxGraphFileSender(client, lambda: token, sleep=lambda _s: None)


def _attachment() -> UploadedFile:
    return UploadedFile(
        item_id="item-7", name="raport.pdf", web_url="https://sp/raport.pdf", attachment_id=_GUID
    )


# --- upload_channel_file --------------------------------------------------------


def test_upload_gets_files_folder_then_puts_content():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/filesFolder"):
            return httpx.Response(
                200, json={"id": "folder-1", "parentReference": {"driveId": "drive-9"}}
            )
        return httpx.Response(
            201,
            json={
                "id": "item-7",
                "name": "raport.pdf",
                "webUrl": "https://sp/raport.pdf",
                "eTag": _ETAG,
            },
        )

    result = _sender(handler).upload_channel_file(
        "team-1", "chan-1", "raport.pdf", b"PDFDATA", "application/pdf"
    )

    # Sekwencja jest sednem metody: NAJPIERW odczyt folderu, POTEM upload — i dokładnie dwa żądania.
    assert calls == [
        ("GET", "/v1.0/teams/team-1/channels/chan-1/filesFolder"),
        ("PUT", "/v1.0/drives/drive-9/items/folder-1:/raport.pdf:/content"),
    ]
    assert result == UploadedFile(
        item_id="item-7", name="raport.pdf", web_url="https://sp/raport.pdf", attachment_id=_GUID
    )


def test_upload_sends_raw_bytes_content_type_and_auth_header():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/filesFolder"):
            return httpx.Response(200, json={"id": "f", "parentReference": {"driveId": "d"}})
        seen["body"] = request.read()
        seen["ctype"] = request.headers.get("content-type")
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(201, json={"id": "i", "webUrl": "u", "eTag": _ETAG})

    _sender(handler).upload_channel_file(
        "t", "c", "f.bin", b"\x00\x01raw", "application/octet-stream"
    )

    assert seen["body"] == b"\x00\x01raw"
    assert seen["ctype"] == "application/octet-stream"
    assert seen["auth"] == "Bearer access-token"


def test_token_is_carried_by_the_request_not_stored_on_the_shared_client():
    """Token doklejamy PER ŻĄDANIE (wzorzec ``graph_thread_source``), nie do ``client.headers``.

    Sonda wyżej (``…auth_header``) tego NIE rozstrzyga: ``httpx`` scala nagłówki klienta
    z nagłówkami żądania, więc ``request.headers["Authorization"]`` wygląda tak samo przy obu
    implementacjach — asercja przechodziła również przed naprawą. Rozstrzyga dopiero token
    ZMIENNY: przy tokenie wstrzykniętym raz w obiekt klienta drugie żądanie tej samej wysyłki
    jedzie jeszcze pierwszym, a nagłówek zostaje na dzielonym kliencie i wycieka do cudzych
    żądań z puli wątków. Bliźniaczy ``graph_user_push`` ma tę sondę; ten adapter jej nie miał.
    """
    tokeny = iter(["token-1", "token-2", "token-3"])
    autoryzacje: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        autoryzacje.append(request.headers.get("Authorization"))
        if request.url.path.endswith("/filesFolder"):
            return httpx.Response(200, json={"id": "f", "parentReference": {"driveId": "d"}})
        return httpx.Response(201, json={"id": "i", "webUrl": "u", "eTag": _ETAG})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sender = HttpxGraphFileSender(client, lambda: next(tokeny), sleep=lambda _s: None)

    sender.upload_channel_file("t", "c", "raport.pdf", b"x", "application/pdf")

    assert autoryzacje == ["Bearer token-1", "Bearer token-2"]
    assert "Authorization" not in client.headers  # nic nie zostaje na dzielonym kliencie


def test_upload_url_encodes_filename():
    raw_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw_paths.append(request.url.raw_path.decode())
        if request.url.path.endswith("/filesFolder"):
            return httpx.Response(200, json={"id": "f", "parentReference": {"driveId": "d"}})
        return httpx.Response(201, json={"id": "i", "webUrl": "u", "eTag": _ETAG})

    _sender(handler).upload_channel_file("t", "c", "moj raport.pdf", b"x", "application/pdf")

    # Spacja w nazwie MUSI wyjść jako %20 w ścieżce — inaczej Graph rozbije segment ścieżki.
    assert any(":/moj%20raport.pdf:/content" in rp for rp in raw_paths)


def test_upload_raises_when_files_folder_lacks_drive():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "folder-1"})  # brak parentReference.driveId

    with pytest.raises(RuntimeError, match="driveId"):
        _sender(handler).upload_channel_file("t", "c", "f", b"x", "text/plain")


def test_upload_raises_when_files_folder_lacks_id():
    def handler(request: httpx.Request) -> httpx.Response:
        # driveId jest, ale brak id folderu — druga gałąź _require (kanał bez dysku plików).
        return httpx.Response(200, json={"parentReference": {"driveId": "d"}})

    with pytest.raises(RuntimeError, match="filesFolder.id"):
        _sender(handler).upload_channel_file("t", "c", "f", b"x", "text/plain")


def test_upload_raises_when_put_response_lacks_web_url():
    """web_url jest load-bearing (→ contentUrl) — niepełna odpowiedź PUT musi paść."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/filesFolder"):
            return httpx.Response(200, json={"id": "f", "parentReference": {"driveId": "d"}})
        return httpx.Response(201, json={"id": "i", "eTag": _ETAG})  # brak webUrl

    with pytest.raises(RuntimeError, match="webUrl"):
        _sender(handler).upload_channel_file("t", "c", "f", b"x", "text/plain")


def test_upload_raises_when_etag_has_no_guid():
    """Brak GUID w eTag = pliku nie da się załączyć → fail loudly przy wgraniu."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/filesFolder"):
            return httpx.Response(200, json={"id": "f", "parentReference": {"driveId": "d"}})
        return httpx.Response(201, json={"id": "i", "webUrl": "u", "eTag": "brak-guida"})

    with pytest.raises(RuntimeError, match="GUID"):
        _sender(handler).upload_channel_file("t", "c", "f", b"x", "text/plain")


# --- post_reply_with_attachment -------------------------------------------------


def test_reply_posts_reference_attachment_to_replies_endpoint():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={"id": "reply-1"})

    _sender(handler).post_reply_with_attachment(
        "team-1", "chan-1", "root-9", "<p>oto raport</p>", _attachment()
    )

    assert seen["path"] == "/v1.0/teams/team-1/channels/chan-1/messages/root-9/replies"
    assert seen["auth"] == "Bearer access-token"  # _refresh_auth ustawia token też na ścieżce reply
    payload = json.loads(seen["body"])
    assert payload["body"]["contentType"] == "html"
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "reference"
    assert attachment["contentUrl"] == "https://sp/raport.pdf"
    assert attachment["name"] == "raport.pdf"
    assert "<p>oto raport</p>" in payload["body"]["content"]


def test_attachment_id_binds_body_marker_to_attachments_array():
    """GUID z eTag musi być TEN SAM w ``<attachment id>`` i w ``attachments[].id`` — inaczej Teams
    nie zrenderuje pliku."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={})

    _sender(handler).post_reply_with_attachment("t", "c", "root", "", _attachment())

    payload = json.loads(seen["body"])
    assert payload["attachments"][0]["id"] == _GUID
    assert f'<attachment id="{_GUID}">' in payload["body"]["content"]


def test_reply_raises_thread_root_gone_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "NotFound"}})

    with pytest.raises(ThreadRootGone):
        _sender(handler).post_reply_with_attachment("t", "c", "gone", "", _attachment())


def test_reply_reraises_non_404_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "Forbidden"}})

    with pytest.raises(httpx.HTTPStatusError):
        _sender(handler).post_reply_with_attachment("t", "c", "root", "", _attachment())


# --- ponawianie (ADR 0035, ta sama reguła co graph_teams_notifier) --------------


def test_throttling_is_retried_on_folder_read():
    """429 = żądanie odrzucone przed przetworzeniem → wolno ponowić także odczyt folderu."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/filesFolder"):
            attempts.append("folder")
            if len(attempts) < 3:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"id": "f", "parentReference": {"driveId": "d"}})
        return httpx.Response(201, json={"id": "i", "webUrl": "u", "eTag": _ETAG})

    _sender(handler).upload_channel_file("t", "c", "f", b"x", "text/plain")

    assert len(attempts) == 3


def test_upload_put_is_retried_after_5xx():
    """Upload po ŚCIEŻCE jest idempotentny (nadpisuje po nazwie) → 5xx wolno ponowić."""
    puts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/filesFolder"):
            return httpx.Response(200, json={"id": "f", "parentReference": {"driveId": "d"}})
        puts.append("put")
        if len(puts) < 2:
            return httpx.Response(503, json={"error": {"message": "boom"}})
        return httpx.Response(201, json={"id": "i", "webUrl": "u", "eTag": _ETAG})

    _sender(handler).upload_channel_file("t", "c", "f", b"x", "text/plain")

    assert len(puts) == 2


def test_upload_put_is_retried_after_timeout():
    """Timeout uploadu jest niejednoznaczny, ale PUT po ścieżce jest idempotentny → ponawiamy."""
    puts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/filesFolder"):
            return httpx.Response(200, json={"id": "f", "parentReference": {"driveId": "d"}})
        puts.append("put")
        if len(puts) < 2:
            raise httpx.ReadTimeout("zbyt wolno", request=request)
        return httpx.Response(201, json={"id": "i", "webUrl": "u", "eTag": _ETAG})

    _sender(handler).upload_channel_file("t", "c", "f", b"x", "text/plain")

    assert len(puts) == 2


def test_upload_transient_retries_give_up_instead_of_hanging():
    """Sufit prób jest twardy także tam, gdzie ponawiamy — nie wisimy na trwałym 5xx."""
    puts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/filesFolder"):
            return httpx.Response(200, json={"id": "f", "parentReference": {"driveId": "d"}})
        puts.append("put")
        return httpx.Response(503, json={"error": {"message": "boom"}})

    with pytest.raises(httpx.HTTPStatusError):
        _sender(handler).upload_channel_file("t", "c", "f", b"x", "text/plain")

    assert len(puts) == 4  # pierwsza próba + trzy ponowienia (_MAX_TRANSIENT_RETRIES)


def test_reply_send_is_not_retried_after_timeout():
    """SEDNO: timeout wysyłki jest niejednoznaczny (Graph mógł przyjąć) → powtórka = duplikat."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append("post")
        raise httpx.ReadTimeout("zbyt wolno", request=request)

    with pytest.raises(httpx.ReadTimeout):
        _sender(handler).post_reply_with_attachment("t", "c", "root", "", _attachment())

    assert len(attempts) == 1  # ani jednego powtórzenia — inaczej duplikat załącznika


def test_reply_send_is_not_retried_after_5xx():
    """SEDNO: powtórzona odpowiedź = DRUGI załącznik w wątku — 5xx wysyłki NIE ponawiamy."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append("post")
        return httpx.Response(503, json={"error": {"message": "boom"}})

    with pytest.raises(httpx.HTTPStatusError):
        _sender(handler).post_reply_with_attachment("t", "c", "root", "", _attachment())

    assert len(attempts) == 1  # ani jednego powtórzenia


def test_reply_throttling_is_still_retried():
    """429 na wysyłce jest bezpieczny (odrzucone przed przetworzeniem) — ponawiamy."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append("post")
        if len(attempts) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201, json={"id": "reply-1"})

    _sender(handler).post_reply_with_attachment("t", "c", "root", "", _attachment())

    assert len(attempts) == 3


# --- kontrakt portu (strukturalna atrapa w pamięci) -----------------------------


class _FakeFileSender:
    """Atrapa ``TeamsFileSender`` w pamięci — dowód, że port jest implementowalny bez Graph.

    Przyszli konsumenci (odpowiedź plikiem 0026, push 1:1 0027, worklog załącznikiem 0035/0038)
    będą testować swoją logikę właśnie na takiej atrapie, bez ``httpx``/MSAL.
    """

    def __init__(self) -> None:
        self.uploaded: list[tuple[str, str, str, bytes, str]] = []
        self.replies: list[tuple[str, str, str, str, UploadedFile]] = []

    def upload_channel_file(
        self, team_id: str, channel_id: str, filename: str, content: bytes, content_type: str
    ) -> UploadedFile:
        self.uploaded.append((team_id, channel_id, filename, content, content_type))
        return UploadedFile(
            item_id=f"item-{filename}",
            name=filename,
            web_url=f"https://x/{filename}",
            attachment_id=_GUID,
        )

    def post_reply_with_attachment(
        self, team_id: str, channel_id: str, root_id: str, html: str, attachment: UploadedFile
    ) -> None:
        self.replies.append((team_id, channel_id, root_id, html, attachment))


def test_in_memory_fake_satisfies_the_port():
    sender: TeamsFileSender = _FakeFileSender()  # mypy: atrapa MUSI pasować strukturalnie
    ref = sender.upload_channel_file("t", "c", "raport.pdf", b"data", "application/pdf")
    sender.post_reply_with_attachment("t", "c", "root-1", "<p>opis</p>", ref)

    assert isinstance(ref, UploadedFile)
    assert sender.uploaded[0][2] == "raport.pdf"
    assert sender.replies[0][4] is ref
