"""Czysta konwersja tekst ↔ ADF (Atlassian Document Format) — most Jira Cloud (ADR 0033).

Jira Cloud REST v3 przyjmuje i zwraca ``description``/``comment.body`` jako dokument ADF (JSON),
nie goły string. Te dwie czyste funkcje domykają tłumaczenie NA GRANICY adaptera Cloud: klient
koduje tekst → ADF przy zapisie (``create_issue``/``add_comment``) i dekoduje ADF → tekst przy
odczycie (``search_issues``), dzięki czemu reszta pipeline'u (``selection``, poller, serwisy zapisu)
widzi te same stringi co dla Server/DC. Bez I/O, bez SDK — testowalne w izolacji.

Zakres świadomie minimalny: paragrafy + twarde złamania linii + wzmianki/emoji (``attrs.text``).
Bogatszy rendering (tabele, panele, listy z wcięciami) jest rozszerzalny tutaj, ale nie potrzebny
do wiernego przeniesienia zwykłego tekstu zgłoszeń i komentarzy.
"""

from __future__ import annotations

from typing import Any

# Kontenery INLINE sklejają dzieci bez separatora (``text``/``hardBreak``/wzmianki). Pozostałe
# kontenery (``doc``/listy/``listItem``/``blockquote``…) są BLOKOWE — dzieci łączymy ``\n``, więc
# akapity i pozycje list rozdzielają się jednym złamaniem (bez podwójnych z zagnieżdżeń).
_INLINE_CONTAINERS = frozenset({"paragraph", "heading", "codeBlock"})


def text_to_adf(text: str) -> dict[str, Any]:
    """Zakoduj zwykły tekst jako minimalny dokument ADF (``doc`` → ``paragraph`` → ``text``).

    Każda linia (podział po ``\\n``) staje się osobnym akapitem; pusta linia → pusty akapit, więc
    ``adf_to_text(text_to_adf(s)) == s`` dla ``s`` bez skrajnych złamań linii (te są przy odczycie
    przycinane). Pusty tekst daje dokument z jednym pustym akapitem (poprawny ADF,
    akceptowany przez Jira Cloud jako „brak treści").
    """
    content: list[dict[str, Any]] = []
    for line in (text or "").split("\n"):
        paragraph: dict[str, Any] = {"type": "paragraph", "content": []}
        if line:
            paragraph["content"] = [{"type": "text", "text": line}]
        content.append(paragraph)
    return {"type": "doc", "version": 1, "content": content}


def adf_to_text(node: dict[str, Any] | str | None) -> str:
    """Spłaszcz dokument ADF do zwykłego tekstu; odporny na ``None``/nie-dict/nieznane węzły.

    Jeśli ``node`` jest już stringiem (np. instancja zwróciła zwykły tekst), przepuszcza go bez
    zmian. Zbiera węzły ``text``, tłumaczy ``hardBreak`` na ``\\n``, łączy akapity/bloki złamaniem
    linii, a wzmianki/emoji renderuje po ``attrs.text``. Skrajne złamania linii są przycinane
    (czysty, czytelny wynik) — dlatego round-trip zachowuje tekst BEZ wiodących/kończących ``\\n``.
    """
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    return _render(node).strip("\n")


def _render(node: Any) -> str:
    """Zrenderuj węzeł do tekstu: kontenery inline sklejają dzieci, blokowe łączą je ``\\n``."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    node_type = node.get("type")
    if node_type == "text":
        return str(node.get("text", ""))
    if node_type == "hardBreak":
        return "\n"
    if node_type in ("mention", "emoji"):
        attrs = node.get("attrs")
        if isinstance(attrs, dict):
            return str(attrs.get("text") or attrs.get("shortName") or "")
        return ""
    children = node.get("content")
    if not isinstance(children, list):
        return ""
    separator = "" if node_type in _INLINE_CONTAINERS else "\n"
    return separator.join(_render(child) for child in children)
