"""Testy czystej logiki wyboru wiadomości pollera Teams (delegowany Graph).

Tu żyje sedno drzwi — WIELOTURA w wątku (watermark PER WĄTEK, nie po
``root.lastModifiedDateTime``), self-skip, dedup, watermark startowy i odporność
znaczników czasu na różną precyzję ułamków sekund. Wszystko czyste → testujemy bez sieci
i bez SDK, atrapując tylko surowe słowniki payloadu Graph.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from workmate.adapters.inbound.teams_graph.selection import (
    AttachmentRef,
    ChannelMessage,
    _parse_mention_ids,
    _parse_refs,
    iso_gt,
    normalize,
    parse_iso,
    plan_channel,
    roots_to_poll,
)

_ME = "me-bot-user"
_ACTIVE_IDLE = timedelta(hours=24)
# Chwila „teraz" pollingu — dobrana tak, by świeże znaczniki poniżej nie były eksmitowane.
_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _raw_message(
    *,
    msg_id: str,
    created: str,
    text: str = "hej",
    reply_to: str | None = None,
    sender_id: str | None = "u-anna",
    sender_name: str = "Anna",
    message_type: str = "message",
    deleted: str | None = None,
) -> dict[str, Any]:
    """Surowy słownik wiadomości Graph — z domyślnym human-senderem i treścią HTML."""
    raw: dict[str, Any] = {
        "id": msg_id,
        "messageType": message_type,
        "createdDateTime": created,
        "body": {"contentType": "html", "content": f"<p>{text}</p>"},
    }
    if reply_to is not None:
        raw["replyToId"] = reply_to
    if deleted is not None:
        raw["deletedDateTime"] = deleted
    if sender_id is not None:
        raw["from"] = {"user": {"id": sender_id, "displayName": sender_name}}
    else:
        # Bot/aplikacja: brak ``from.user`` (albo ``from`` bez usera).
        raw["from"] = {"application": {"id": "app-x"}}
    return raw


# --- parse_iso / iso_gt: odporność na precyzję ułamków sekund ----------------


def test_parse_iso_handles_zulu_suffix():
    assert parse_iso("2024-01-01T10:00:00Z") == datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)


def test_parse_iso_empty_and_invalid_map_to_epoch():
    epoch = datetime.min.replace(tzinfo=UTC)
    assert parse_iso("") == epoch
    assert parse_iso("not-a-date") == epoch  # nie rzuca — degraduje do epoki


def test_iso_gt_compares_by_instant_not_by_string_across_fraction_precision():
    """Pułapka: '...00Z' vs '...00.5Z' — porównanie stringów myli 'Z' z cyfrą po kropce.

    Bez kropki znak po sekundach to 'Z' (0x5A); z ułamkiem to '.' (0x2E) < 'Z', więc
    string uznałby '...00Z' za większy. Po sparsowaniu .5 s jest jednak PÓŹNIEJSZE.
    """
    coarse = "2024-01-01T00:00:00Z"
    fine_later = "2024-01-01T00:00:00.500Z"

    # Dowód pułapki: naiwny string uznaje PÓŹNIEJSZY znacznik za wcześniejszy ('.' < 'Z').
    assert fine_later < coarse
    assert iso_gt(fine_later, coarse) is True
    assert iso_gt(coarse, fine_later) is False


def test_iso_gt_treats_equal_instants_with_different_precision_as_not_greater():
    assert iso_gt("2024-01-01T00:00:00Z", "2024-01-01T00:00:00.000Z") is False
    assert iso_gt("2024-01-01T00:00:00.000Z", "2024-01-01T00:00:00Z") is False


# --- normalize --------------------------------------------------------------


def test_normalize_maps_root_post_thread_root_to_own_id():
    raw = _raw_message(msg_id="root-1", created="2024-01-01T10:00:00Z", text="ustalenia")

    msg = normalize(raw)

    assert msg == ChannelMessage(
        id="root-1",
        thread_root_id="root-1",  # post root: wątek = własne id
        created="2024-01-01T10:00:00Z",
        sender_id="u-anna",
        sender_name="Anna",
        text="ustalenia",
    )


def test_normalize_reply_keeps_reply_to_id_as_thread_root():
    """Klucz PAMIĘCI wątku = ``replyToId`` — odpowiedź należy do wątku roota."""
    raw = _raw_message(
        msg_id="r-2", created="2024-01-01T10:05:00Z", reply_to="root-1", text="i jeszcze"
    )

    msg = normalize(raw)

    assert msg is not None
    assert msg.thread_root_id == "root-1"


def test_normalize_strips_html_and_unescapes_entities():
    raw = _raw_message(msg_id="m", created="t")
    raw["body"]["content"] = "<div>Ala &amp; Ola <b>2&gt;1</b></div>"

    msg = normalize(raw)

    assert msg is not None
    assert msg.text == "Ala & Ola 2>1"


@pytest.mark.parametrize(
    "raw",
    [
        _raw_message(msg_id="s", created="t", message_type="systemEventMessage"),
        _raw_message(msg_id="d", created="t", deleted="2024-01-01T00:00:00Z"),
    ],
    ids=["system_event", "deleted"],
)
def test_normalize_rejects_non_content_events(raw):
    assert normalize(raw) is None


def test_normalize_rejects_empty_text_after_stripping_html():
    raw = _raw_message(msg_id="m", created="t")
    raw["body"]["content"] = "<p></p>"  # same znaczniki → pusto po strip

    assert normalize(raw) is None


def test_normalize_bot_message_has_empty_sender_id():
    """Bot/aplikacja nie ma ``from.user.id`` — sender_id puste (potem odfiltrowane)."""
    raw = _raw_message(msg_id="b", created="t", sender_id=None)

    msg = normalize(raw)

    assert msg is not None
    assert msg.sender_id == ""


# --- normalize.mentions_bot + _parse_mention_ids (wyzwalacz „zapisz to", ADR 0048) ---


def test_normalize_sets_mentions_bot_when_me_id_is_mentioned():
    # ``me_id`` (AAD id bota) wśród @wzmiankowanych → sygnał wyzwalacza „zapisz to".
    raw = _raw_message(msg_id="m", created="2024-01-01T10:00:00Z", text="@WorkMate zapisz to")
    raw["mentions"] = [{"mentioned": {"user": {"id": _ME}}}]

    msg = normalize(raw, _ME)

    assert msg is not None
    assert msg.mentions_bot is True


def test_normalize_mentions_bot_false_without_me_id():
    # Drzwi/testy bez ``me_id`` (pusty) → mentions_bot domyślnie False, nawet gdy wzmianka jest.
    raw = _raw_message(msg_id="m", created="2024-01-01T10:00:00Z")
    raw["mentions"] = [{"mentioned": {"user": {"id": _ME}}}]

    msg = normalize(raw)  # brak me_id

    assert msg is not None
    assert msg.mentions_bot is False


def test_normalize_mentions_bot_false_when_someone_else_mentioned():
    # Wzmianka innego użytkownika (nie bota) nie jest wyzwalaczem.
    raw = _raw_message(msg_id="m", created="2024-01-01T10:00:00Z")
    raw["mentions"] = [{"mentioned": {"user": {"id": "u-inny"}}}]

    msg = normalize(raw, _ME)

    assert msg is not None
    assert msg.mentions_bot is False


def test_normalize_mentions_bot_false_when_no_mentions():
    raw = _raw_message(msg_id="m", created="2024-01-01T10:00:00Z")  # brak klucza mentions

    msg = normalize(raw, _ME)

    assert msg is not None
    assert msg.mentions_bot is False


def test_parse_mention_ids_extracts_user_ids_and_skips_non_user_mentions():
    # Wzmianki kanału/zespołu nie mają ``user`` — wyłuskujemy TYLKO id użytkowników.
    raw = {
        "mentions": [
            {"mentioned": {"user": {"id": "u1"}}},
            {"mentioned": {"conversation": {"id": "channel-x"}}},  # wzmianka kanału (bez user)
            {"mentioned": {"user": {"id": "u2"}}},
        ]
    }

    assert _parse_mention_ids(raw) == frozenset({"u1", "u2"})


def test_parse_mention_ids_empty_without_mentions():
    assert _parse_mention_ids({}) == frozenset()


# --- _parse_refs / normalize: załączniki (ADR 0016) -------------------------


def _hosted_html(hosted_id: str, *, caption: str = "") -> str:
    """HTML wiadomości z obrazem inline (``hostedContents/{id}/$value``) i opcjonalnym podpisem."""
    img = f'<img src="https://graph/…/hostedContents/{hosted_id}/$value">'
    return f"<p>{caption}</p>{img}" if caption else img


def test_parse_refs_maps_file_attachment_of_type_reference():
    """Plik w SharePoint: ``attachments`` z ``contentType='reference'`` + ``contentUrl``."""
    raw = {
        "attachments": [
            {
                "contentType": "reference",
                "name": "umowa.pdf",
                "contentUrl": "https://sharepoint/…/umowa.pdf",
            }
        ]
    }

    refs = _parse_refs(raw, body_html=None)

    assert refs == (
        AttachmentRef(kind="file", name="umowa.pdf", url="https://sharepoint/…/umowa.pdf"),
    )


def test_parse_refs_ignores_non_reference_or_urlless_attachments():
    """Załącznik bez ``contentUrl`` albo innego typu niż reference nie daje referencji pliku."""
    raw = {
        "attachments": [
            {"contentType": "reference", "name": "brak-url.pdf"},  # bez contentUrl
            {"contentType": "messageReference", "contentUrl": "x"},  # inny typ
        ]
    }

    assert _parse_refs(raw, body_html=None) == ()


def test_parse_refs_extracts_inline_hosted_image_from_body_html():
    refs = _parse_refs({}, body_html=_hosted_html("hosted-abc"))

    assert refs == (AttachmentRef(kind="hosted", name="obraz", hosted_id="hosted-abc"),)


def test_parse_refs_dedups_repeated_hosted_id():
    """Ten sam ``hosted_id`` powtórzony w HTML → jedna referencja (dedup)."""
    html = _hosted_html("dup") + _hosted_html("dup")

    refs = _parse_refs({}, body_html=html)

    assert refs == (AttachmentRef(kind="hosted", name="obraz", hosted_id="dup"),)


def test_parse_refs_extracts_public_giphy_image_as_url():
    """GIF z Giphy (host allowlistowany) → referencja ``url`` (pobierana zwykłym HTTP)."""
    body = '<img src="https://media.giphy.com/media/abc123/giphy.gif" alt="gif">'

    refs = _parse_refs({}, body_html=body)

    assert refs == (
        AttachmentRef(
            kind="url", name="obraz", url="https://media.giphy.com/media/abc123/giphy.gif"
        ),
    )


def test_parse_refs_extracts_teams_cdn_emoji_as_url():
    """Emoji/naklejka z teams.cdn.office.net (host allowlistowany) → referencja ``url``."""
    body = "<img src='https://statics.teams.cdn.office.net/evergreen-assets/emoji/x.png'>"

    refs = _parse_refs({}, body_html=body)

    assert refs == (
        AttachmentRef(
            kind="url",
            name="obraz",
            url="https://statics.teams.cdn.office.net/evergreen-assets/emoji/x.png",
        ),
    )


def test_parse_refs_ignores_non_allowlisted_image_host_ssrf_guard():
    """Obraz spoza allowlisty (dowolny host) NIE jest pobierany — bariera SSRF."""
    body = '<img src="https://internal.service.local/secret.png">'

    assert _parse_refs({}, body_html=body) == ()


def test_parse_refs_ignores_non_https_public_image():
    """Nawet host z allowlisty, ale po ``http`` (nie https) → pomijamy."""
    body = '<img src="http://media.giphy.com/media/abc/giphy.gif">'

    assert _parse_refs({}, body_html=body) == ()


def test_parse_refs_asyncgw_direct_src_is_not_fetched():
    """Bezpośredni obraz AMS/asyncgw (nie hostedContents, nie allowlista) → brak referencji."""
    body = (
        '<img itemtype="http://schema.skype.com/AMSImage" '
        'src="https://pl-prod.asyncgw.teams.microsoft.com/v1/objects/0-plc/views/imgo">'
    )

    assert _parse_refs({}, body_html=body) == ()


def test_normalize_keeps_message_with_only_file_and_no_text():
    """REGRESJA: wiadomość z SAMYM plikiem (bez podpisu) NIE jest odrzucana (dziś byłaby)."""
    raw = _raw_message(msg_id="m", created="2024-01-01T10:00:00Z", text="")
    raw["body"]["content"] = "<p></p>"  # pusto po strip HTML
    raw["attachments"] = [
        {"contentType": "reference", "name": "plik.pdf", "contentUrl": "u://plik"}
    ]

    msg = normalize(raw)

    assert msg is not None
    assert msg.text == ""
    assert msg.attachment_refs == (AttachmentRef(kind="file", name="plik.pdf", url="u://plik"),)


def test_normalize_keeps_message_with_only_inline_image():
    raw = _raw_message(msg_id="m", created="2024-01-01T10:00:00Z")
    raw["body"]["content"] = _hosted_html("h1")  # sam obraz, bez tekstu

    msg = normalize(raw)

    assert msg is not None
    assert msg.text == ""
    assert msg.attachment_refs == (AttachmentRef(kind="hosted", name="obraz", hosted_id="h1"),)


def test_normalize_keeps_caption_alongside_inline_image():
    """Podpis (tekst po rozebraniu HTML) zachowany OBOK referencji obrazu inline."""
    raw = _raw_message(msg_id="m", created="2024-01-01T10:00:00Z")
    raw["body"]["content"] = _hosted_html("h1", caption="Rzuć okiem na to")

    msg = normalize(raw)

    assert msg is not None
    assert msg.text == "Rzuć okiem na to"
    assert msg.attachment_refs == (AttachmentRef(kind="hosted", name="obraz", hosted_id="h1"),)


def test_normalize_still_rejects_empty_message_without_text_or_attachments():
    """Pusto = brak tekstu ORAZ brak załączników — nadal odrzucone (nie ma na co odpowiadać)."""
    raw = _raw_message(msg_id="m", created="t")
    raw["body"]["content"] = "<p></p>"

    assert normalize(raw) is None


# --- roots_to_poll ----------------------------------------------------------


def test_roots_to_poll_always_includes_tracked_threads():
    """Sedno wielotury: aktywny (śledzony) wątek odpytujemy o odpowiedzi KAŻDĄ rundą,

    nawet gdy w tej rundzie nie ma świeżych postów root (``roots`` pusty).
    """
    channel_state = {
        "since_roots": "2024-01-01T00:00:00Z",
        "threads": {"root-1": {"watermark": "t", "last_seen": "t"}},
    }

    assert roots_to_poll([], channel_state) == ["root-1"]


def test_roots_to_poll_adds_fresh_roots_and_skips_old_backlog():
    channel_state = {"since_roots": "2024-01-01T10:00:00Z", "threads": {}}
    roots = [
        {"id": "old", "createdDateTime": "2024-01-01T09:00:00Z"},  # sprzed since
        {"id": "new", "createdDateTime": "2024-01-01T11:00:00Z"},  # po since
    ]

    assert roots_to_poll(roots, channel_state) == ["new"]


def test_roots_to_poll_returns_sorted_ids_for_determinism():
    channel_state = {
        "since_roots": "2024-01-01T00:00:00Z",
        "threads": {"root-b": {}, "root-a": {}},
    }

    assert roots_to_poll([], channel_state) == ["root-a", "root-b"]


# --- plan_channel: nowe wątki root + watermark startowy ---------------------


def _fresh_channel_state(since: str = "2024-01-01T11:00:00Z") -> dict[str, Any]:
    return {"since_roots": since, "threads": {}}


def test_plan_channel_returns_new_root_and_tracks_it():
    root = _raw_message(msg_id="root-1", created="2024-01-01T11:30:00Z", text="start")

    messages, state = plan_channel(
        [root],
        {},
        _fresh_channel_state(),
        me_id=_ME,
        replied=set(),
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert [m.id for m in messages] == ["root-1"]
    assert "root-1" in state["threads"]
    assert state["since_roots"] == "2024-01-01T11:30:00Z"  # watermark root przesunięty


def test_plan_channel_ignores_backlog_before_startup_watermark():
    """Watermark startowy (``since_roots``) tnie backlog sprzed uruchomienia bota."""
    old_root = _raw_message(msg_id="old", created="2024-01-01T09:00:00Z")

    messages, state = plan_channel(
        [old_root],
        {},
        _fresh_channel_state("2024-01-01T11:00:00Z"),
        me_id=_ME,
        replied=set(),
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert messages == []
    assert state["threads"] == {}  # backlogowy wątek nie jest nawet śledzony


# --- plan_channel: WIELOTURA (watermark per wątek) --------------------------


def _tracked_state(root_id: str, watermark: str) -> dict[str, Any]:
    """Kanał ze śledzonym wątkiem na danym watermarku (ostatnio widziana odpowiedź)."""
    return {
        "since_roots": "2024-01-01T11:00:00Z",
        "threads": {root_id: {"watermark": watermark, "last_seen": watermark}},
    }


def test_plan_channel_returns_new_reply_in_tracked_thread_past_per_thread_watermark():
    """REGRESJA: kolejna odpowiedź w wątku wraca mimo niezmienionego ``lastModifiedDateTime``.

    Bramkujemy WYŁĄCZNIE po watermarku per wątek — dlatego bot nie milknie po pierwszej
    odpowiedzi (spike milkł, bo patrzył na root.lastModifiedDateTime).
    """
    reply = _raw_message(msg_id="r-2", created="2024-01-01T11:40:00Z", reply_to="root-1")

    messages, state = plan_channel(
        [],
        {"root-1": [reply]},
        _tracked_state("root-1", watermark="2024-01-01T11:30:00Z"),
        me_id=_ME,
        replied=set(),
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert [m.id for m in messages] == ["r-2"]
    assert messages[0].thread_root_id == "root-1"
    assert state["threads"]["root-1"]["watermark"] == "2024-01-01T11:40:00Z"


def test_plan_channel_skips_replies_at_or_before_per_thread_watermark():
    """Odpowiedź nie nowsza niż watermark wątku to już-widziana — nie zwracamy jej."""
    stale = _raw_message(msg_id="r-old", created="2024-01-01T11:30:00Z", reply_to="root-1")

    messages, _ = plan_channel(
        [],
        {"root-1": [stale]},
        _tracked_state("root-1", watermark="2024-01-01T11:30:00Z"),
        me_id=_ME,
        replied=set(),
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert messages == []


# --- plan_channel: self-skip, boty, dedup -----------------------------------


def test_plan_channel_skips_own_message_but_advances_watermark():
    """Tryb delegowany: bot JEST userem. Własna odpowiedź nie wraca (brak pętli),

    ale watermark i tak przesuwamy, żeby nie przetwarzać jej w kółko.
    """
    own = _raw_message(
        msg_id="mine", created="2024-01-01T11:40:00Z", reply_to="root-1", sender_id=_ME
    )

    messages, state = plan_channel(
        [],
        {"root-1": [own]},
        _tracked_state("root-1", watermark="2024-01-01T11:30:00Z"),
        me_id=_ME,
        replied=set(),
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert messages == []
    assert state["threads"]["root-1"]["watermark"] == "2024-01-01T11:40:00Z"


def test_plan_channel_skips_bot_and_app_messages_without_user_id():
    """Wiadomości bez ``from.user.id`` (boty/aplikacje) nie są treścią do odpowiedzi."""
    bot = _raw_message(
        msg_id="botmsg",
        created="2024-01-01T11:40:00Z",
        reply_to="root-1",
        sender_id=None,
    )

    messages, _ = plan_channel(
        [],
        {"root-1": [bot]},
        _tracked_state("root-1", watermark="2024-01-01T11:30:00Z"),
        me_id=_ME,
        replied=set(),
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert messages == []


def test_plan_channel_dedup_skips_already_replied_message():
    """``replied`` (dedup) blokuje drugą odpowiedź na tę samą wiadomość."""
    reply = _raw_message(msg_id="r-2", created="2024-01-01T11:40:00Z", reply_to="root-1")

    messages, state = plan_channel(
        [],
        {"root-1": [reply]},
        _tracked_state("root-1", watermark="2024-01-01T11:30:00Z"),
        me_id=_ME,
        replied={"r-2"},
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert messages == []
    # Watermark i tak nad odpisaną — nie utknie na niej.
    assert state["threads"]["root-1"]["watermark"] == "2024-01-01T11:40:00Z"


# --- plan_channel: sort + eksmisja ------------------------------------------


def test_plan_channel_sorts_returned_messages_chronologically():
    later = _raw_message(msg_id="late", created="2024-01-01T11:50:00Z", reply_to="root-1")
    earlier = _raw_message(msg_id="early", created="2024-01-01T11:40:00Z", reply_to="root-1")

    messages, _ = plan_channel(
        [],
        {"root-1": [later, earlier]},  # celowo poza kolejnością
        _tracked_state("root-1", watermark="2024-01-01T11:30:00Z"),
        me_id=_ME,
        replied=set(),
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert [m.id for m in messages] == ["early", "late"]


def test_plan_channel_evicts_thread_idle_longer_than_active_idle():
    """Wątek bez aktywności dłużej niż ``active_idle`` znika ze śledzonych (koniec odpytywania)."""
    stale_seen = (_NOW - timedelta(hours=48)).isoformat()
    channel_state = {
        "since_roots": "2024-01-01T00:00:00Z",
        "threads": {"dead": {"watermark": stale_seen, "last_seen": stale_seen}},
    }

    _, state = plan_channel(
        [],
        {},
        channel_state,
        me_id=_ME,
        replied=set(),
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert state["threads"] == {}


def test_plan_channel_keeps_thread_active_within_idle_window():
    recent_seen = (_NOW - timedelta(hours=1)).isoformat()
    channel_state = {
        "since_roots": "2024-01-01T00:00:00Z",
        "threads": {"live": {"watermark": recent_seen, "last_seen": recent_seen}},
    }

    _, state = plan_channel(
        [],
        {},
        channel_state,
        me_id=_ME,
        replied=set(),
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert "live" in state["threads"]


def test_plan_channel_does_not_mutate_input_channel_state():
    """Czystość: wejściowy stan kanału zostaje nietknięty (nowy stan zwracany)."""
    channel_state = _tracked_state("root-1", watermark="2024-01-01T11:30:00Z")
    original_watermark = channel_state["threads"]["root-1"]["watermark"]
    reply = _raw_message(msg_id="r-2", created="2024-01-01T11:40:00Z", reply_to="root-1")

    plan_channel(
        [],
        {"root-1": [reply]},
        channel_state,
        me_id=_ME,
        replied=set(),
        now=_NOW,
        active_idle=_ACTIVE_IDLE,
    )

    assert channel_state["threads"]["root-1"]["watermark"] == original_watermark


def test_normalize_carries_mention_texts_for_the_save_trigger():
    """``mentionText`` to nazwa, którą Graph podmienia znacznik ``<at>``.

    ``_strip_html`` spłaszcza wzmiankę do gołej nazwy, więc bez tego pola wyzwalacz „zapisz to"
    nie odróżnia adresata od argumentu — a nazwa bota niesie klucz rejestru (`workmate`).
    """
    raw = _raw_message(
        msg_id="m", created="2024-01-01T10:00:00Z", text="Zapisz to, Virtual WorkMate"
    )
    raw["mentions"] = [
        {"mentionText": "Virtual WorkMate", "mentioned": {"user": {"id": _ME}}},
        {"mentionText": "Kanał", "mentioned": {}},
    ]

    msg = normalize(raw, _ME)

    assert msg is not None
    assert msg.mention_texts == ("Virtual WorkMate", "Kanał")


def test_normalize_without_mentions_leaves_mention_texts_empty():
    raw = _raw_message(msg_id="m", created="2024-01-01T10:00:00Z", text="zwykła wiadomość")

    msg = normalize(raw, _ME)

    assert msg is not None
    assert msg.mention_texts == ()
