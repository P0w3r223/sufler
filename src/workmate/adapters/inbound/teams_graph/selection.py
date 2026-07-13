"""Czysta logika wyboru wiadomości i watermarku pollera Teams (delegowany Graph).

Bez I/O i bez SDK — tu żyje poprawka WIELOTURY: pobieranie odpowiedzi NIE zależy od
``root.lastModifiedDateTime`` (Graph nie aktualizuje go przy dodaniu nowej odpowiedzi do
wątku, przez co spike milkł po pierwszej odpowiedzi), tylko od watermarku PER WĄTEK i
zbioru aktywnych wątków. Dzięki temu w jednym wątku można pisać wielokrotnie, a bot dalej
czyta i odpowiada. Wszystkie decyzje są czystymi funkcjami — testowalne bez sieci.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from workmate.core.ports.llm import Attachment

# Watermark „od zawsze" dla wątku bez zapisanej pozycji — starszy niż jakikolwiek Graph.
_EPOCH_ISO = datetime.min.replace(tzinfo=timezone.utc).isoformat()

# Inline obrazy w HTML wiadomości: <img src=".../hostedContents/{id}/$value">.
_HOSTED_RE = re.compile(r"hostedContents/([^/\"'\s]+)/\$value")
# src dowolnego <img> — do rozgałęzienia po hoście (hostedContents Graph vs publiczny URL).
_IMG_SRC_RE = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# Publiczne hosty obrazów Teams (GIF/Giphy, emoji, naklejki) — pobieralne zwykłym HTTP, bez
# tokenu. WĄSKA allowlista jest barierą SSRF: nie pobieramy dowolnego URL z treści wiadomości.
_PUBLIC_IMAGE_HOSTS = (".giphy.com", ".teams.cdn.office.net")


@dataclass(frozen=True)
class AttachmentRef:
    """Lekka REFERENCJA do załącznika (bez bajtów) — materializuje ją adapter I/O.

    ``file`` → plik w SharePoint (``url`` = ``contentUrl``); ``hosted`` → obraz wklejony
    inline (``hosted_id`` w ``/messages/{id}/hostedContents/{hosted_id}/$value``); ``url`` →
    publiczny obraz (GIF/emoji) pobierany zwykłym HTTP z ``url``.
    """

    kind: Literal["file", "hosted", "url"]
    name: str
    url: str = ""  # dla file: contentUrl (SharePoint); dla url: publiczny URL obrazu
    hosted_id: str = ""  # dla hosted: id hostedContent


@dataclass(frozen=True)
class ChannelMessage:
    """Znormalizowana wiadomość kanału — niezależna od kształtu payloadu Graph."""

    id: str
    # Korzeń wątku: ``replyToId`` (dla odpowiedzi) albo własne ``id`` (dla posta root).
    # Klucz PAMIĘCI rozmowy i cel odpowiedzi (odpisujemy w tym samym wątku).
    thread_root_id: str
    created: str
    sender_id: str  # ``from.user.id``; puste dla botów/aplikacji/zdarzeń systemowych
    sender_name: str
    text: str  # treść po rozebraniu HTML
    # REFERENCJE do załączników (sparsowane z payloadu Graph, bez I/O) — poller je materializuje.
    attachment_refs: tuple[AttachmentRef, ...] = ()
    # MATERIALIZOWANE załączniki (base64/tekst) — dokłada je poller przez adapter I/O.
    attachments: tuple[Attachment, ...] = ()


def parse_iso(value: str) -> datetime:
    """ISO-8601 z Graph → aware UTC; puste/niepoprawne → epoka.

    Parsujemy zamiast porównywać stringi, bo Graph zwraca różną precyzję ułamków sekund
    (``...:00Z`` vs ``...:00.123Z``) — porównanie tekstowe myli wtedy 'Z' z cyframi.
    """
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def iso_gt(a: str, b: str) -> bool:
    """Czy znacznik ``a`` jest późniejszy niż ``b`` — po sparsowaniu, nie po stringu."""
    return parse_iso(a) > parse_iso(b)


def _strip_html(raw: str | None) -> str:
    """Usuń znaczniki HTML i odkoduj encje — treść wiadomości kanału bywa w HTML."""
    return html.unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()


def normalize(raw: dict[str, Any]) -> ChannelMessage | None:
    """Zmapuj surową wiadomość Graph; ``None`` gdy to nie treść do odpowiedzi.

    Odrzucamy zdarzenia systemowe (``messageType != 'message'``), skasowane i puste —
    nie ma na co odpowiadać. „Puste" to brak tekstu ORAZ brak załączników: wiadomość
    z samym plikiem/obrazem (bez podpisu) NADAL wymaga odpowiedzi.
    """
    if raw.get("messageType") != "message" or raw.get("deletedDateTime"):
        return None
    body_html = (raw.get("body") or {}).get("content")
    text = _strip_html(body_html)
    refs = _parse_refs(raw, body_html)
    if not text and not refs:
        return None
    user = (raw.get("from") or {}).get("user") or {}
    msg_id = raw.get("id") or ""
    return ChannelMessage(
        id=msg_id,
        thread_root_id=raw.get("replyToId") or msg_id,
        created=raw.get("createdDateTime") or "",
        sender_id=user.get("id") or "",
        sender_name=user.get("displayName") or "?",
        text=text,
        attachment_refs=refs,
    )


def _parse_refs(raw: dict[str, Any], body_html: str | None) -> tuple[AttachmentRef, ...]:
    """Wyłuskaj referencje załączników: pliki (``attachments`` typu reference) + obrazy
    inline (``hostedContents`` z HTML). Sama referencja, bez bajtów — I/O robi materializer."""
    refs: list[AttachmentRef] = []
    for att in raw.get("attachments") or []:
        if att.get("contentType") == "reference" and att.get("contentUrl"):
            refs.append(
                AttachmentRef(
                    kind="file", name=att.get("name") or "plik", url=att["contentUrl"]
                )
            )
    seen: set[str] = set()
    for hosted_id in _HOSTED_RE.findall(body_html or ""):
        if hosted_id not in seen:
            seen.add(hosted_id)
            refs.append(AttachmentRef(kind="hosted", name="obraz", hosted_id=hosted_id))
    # Publiczne obrazy (GIF/Giphy, emoji) — src spoza hostedContents, na allowliście hostów.
    seen_url: set[str] = set()
    for src in _IMG_SRC_RE.findall(body_html or ""):
        url = _public_image_url(src)
        if url and url not in seen_url:
            seen_url.add(url)
            refs.append(AttachmentRef(kind="url", name="obraz", url=url))
    return tuple(refs)


def _public_image_url(src: str) -> str | None:
    """Zwróć URL obrazu, jeśli host jest publiczny i na allowliście (GIF/emoji); inaczej ``None``.

    Wymóg ``https`` + wąska allowlista hostów to bariera SSRF — nie pobieramy dowolnego URL z
    treści wiadomości (np. adresu wewnętrznej usługi). hostedContents (Graph) łapie inna ścieżka.
    """
    url = html.unescape(src)
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not url.lower().startswith("https://") or not host:
        return None
    if any(host == h.lstrip(".") or host.endswith(h) for h in _PUBLIC_IMAGE_HOSTS):
        return url
    return None


def _from_other_human(msg: ChannelMessage, me_id: str) -> bool:
    """Czy wiadomość jest od INNEGO człowieka — pomija boty/aplikacje (brak id) oraz nas.

    Skip po ``me_id`` jest nośny w trybie delegowanym: bot JEST użytkownikiem, więc bez
    tego odpowiadałby na własne odpowiedzi (pętla).
    """
    return bool(msg.sender_id) and msg.sender_id != me_id


def roots_to_poll(roots: list[dict[str, Any]], channel_state: dict[str, Any]) -> list[str]:
    """Które wątki odpytać o odpowiedzi: aktywne (śledzone) + świeżo utworzone.

    Zwraca posortowaną listę id (determinizm w testach). Świadomie NIE bramkujemy po
    ``lastModifiedDateTime`` — aktywny wątek odpytujemy o odpowiedzi w każdej rundzie,
    dopóki nie zostanie eksmitowany (patrz ``plan_channel``).
    """
    since_roots = channel_state.get("since_roots") or ""
    ids = set(channel_state.get("threads", {}))
    for raw in roots:
        root_id = raw.get("id")
        if root_id and iso_gt(raw.get("createdDateTime") or "", since_roots):
            ids.add(root_id)
    return sorted(ids)


def _collect_new(
    raws: list[dict[str, Any]], watermark: str
) -> tuple[list[dict[str, Any]], str]:
    """Zwróć surowe wiadomości utworzone po ``watermark`` + nowy watermark.

    Watermark przesuwamy do NAJNOWSZEJ realnie zobaczonej wiadomości (a nie do „teraz"),
    żeby nie zgubić wpisów utworzonych między rundami.
    """
    newest = watermark
    fresh: list[dict[str, Any]] = []
    for raw in raws:
        created = raw.get("createdDateTime") or ""
        if iso_gt(created, newest):
            newest = created
        if iso_gt(created, watermark):
            fresh.append(raw)
    return fresh, newest


def plan_channel(
    roots: list[dict[str, Any]],
    replies_by_root: dict[str, list[dict[str, Any]]],
    channel_state: dict[str, Any],
    *,
    me_id: str,
    replied: set[str],
    now: datetime,
    active_idle: timedelta,
) -> tuple[list[ChannelMessage], dict[str, Any]]:
    """Wybierz wiadomości do obsługi i policz nowy stan kanału (watermark + aktywne wątki).

    Nowe wątki najwyższego poziomu wykrywamy po ``since_roots``; nowe odpowiedzi — po
    watermarku PER WĄTEK (niezależnie od ``root.lastModifiedDateTime``). Wątki bez
    aktywności dłużej niż ``active_idle`` eksmitujemy, żeby nie odpytywać ich w
    nieskończoność. ``replied`` filtruje już odpisane (dedup), ale watermark i tak
    przesuwamy nad nimi. Wiadomości wracają posortowane chronologicznie.
    """
    since_roots = channel_state.get("since_roots") or _EPOCH_ISO
    threads: dict[str, dict[str, str]] = {
        rid: dict(info) for rid, info in channel_state.get("threads", {}).items()
    }
    messages: list[ChannelMessage] = []

    # 1) Nowe wątki najwyższego poziomu (posty root utworzone po since_roots).
    fresh_roots, since_roots = _collect_new(roots, since_roots)
    for raw in fresh_roots:
        root_id = raw.get("id") or ""
        created = raw.get("createdDateTime") or ""
        if root_id and root_id not in threads:
            threads[root_id] = {"watermark": created, "last_seen": created}
        _append_actionable(messages, raw, me_id, replied)

    # 2) Nowe odpowiedzi w śledzonych wątkach (watermark per wątek — sedno wielotury).
    for root_id, replies in replies_by_root.items():
        info = threads.get(root_id, {"watermark": _EPOCH_ISO, "last_seen": _EPOCH_ISO})
        fresh, watermark = _collect_new(replies, info["watermark"])
        last_seen = info["last_seen"]
        for raw in fresh:
            created = raw.get("createdDateTime") or ""
            if iso_gt(created, last_seen):
                last_seen = created
            _append_actionable(messages, raw, me_id, replied)
        threads[root_id] = {"watermark": watermark, "last_seen": last_seen}

    # 3) Eksmisja martwych wątków — bez aktywności dłużej niż active_idle.
    cutoff = now - active_idle
    threads = {
        rid: info
        for rid, info in threads.items()
        if parse_iso(info["last_seen"]) >= cutoff
    }

    messages = _dedup_by_id(messages)
    messages.sort(key=lambda m: parse_iso(m.created))
    return messages, {"since_roots": since_roots, "threads": threads}


def _append_actionable(
    messages: list[ChannelMessage],
    raw: dict[str, Any],
    me_id: str,
    replied: set[str],
) -> None:
    """Dołóż wiadomość do obsługi, jeśli to treść od innego człowieka i jeszcze nie odpisana."""
    msg = normalize(raw)
    if msg and _from_other_human(msg, me_id) and msg.id not in replied:
        messages.append(msg)


def _dedup_by_id(messages: list[ChannelMessage]) -> list[ChannelMessage]:
    """Usuń duplikaty po id, zachowując kolejność pierwszego wystąpienia."""
    seen: set[str] = set()
    unique: list[ChannelMessage] = []
    for msg in messages:
        if msg.id and msg.id not in seen:
            seen.add(msg.id)
            unique.append(msg)
    return unique
