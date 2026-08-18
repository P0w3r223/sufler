"""Redakcja treści wrażliwej i prywatnej z promptów oraz komunikatów commitów.

Trójstopniowa obrona (proste, deterministyczne blokady — bez LLM):

1. **Redakcja w miejscu** — wartości sekretów, kont/e-maili, IP, identyfikatorów (UUID/GUID)
   i nazw użytkownika w ścieżkach są zastępowane etykietami (``[SEKRET]``, ``[KONTO]``, ``[IP]``,
   ``[ID]``, ``[UŻYTKOWNIK]``). Sama WARTOŚĆ nigdy nie zostaje odczytana ani zapisana.
2. **Przycięcie wklejek** — prompt wyglądający na wklejony log/wyjście terminala redukujemy do
   wiodącej instrukcji człowieka (reszta pomijana ogólnym znacznikiem) — „opisz bardzo ogólnie".
3. **Pełne pominięcie** — jeśli po redakcji zostaje sam zrzut bez sensownej instrukcji, prompt jest
   ignorowany w całości.

Redakcja działa na GRANICY (``parse_prompt`` / ``parse_git_log``), więc surowa treść nie wchodzi
do modeli, renderu ani do zapytania LLM. Funkcje są czyste i w pełni testowalne.

To samo dotyczy METADANYCH raportu (poprawka ADR 0003 z 2026-08-17): ``repo`` idzie przez
``redact_text``, a osoba przez ``person_label`` — na zewnątrz nie wychodzi ani ścieżka z nazwą
użytkownika, ani adres e-mail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Ile znaków wiodącej instrukcji zachowujemy (reszta = potencjalna wklejka → przycięta).
_MAX_KEEP = 240

# Okno skanowania wzorców. Wynik ``sanitize_prompt`` i tak jest przycinany do ``_MAX_KEEP``
# ZNAKÓW PO redakcji, więc puszczanie wyrażeń po całej wklejce to koszt wyrzucony do kosza —
# a przy dziesiątkach kilobajtów jednego tokenu (hex, identyfikatory) skanowanie jest kwadratowe
# i potrafiło zamrozić CLI na dziesiątki sekund. Okno daje ~8× zapasu na etykiety dłuższe od
# zastępowanych wartości, a zdejmuje patologię.
_MAX_SCAN = 8 * _MAX_KEEP

# Ogon niedomkniętego tokenu na granicy okna. Wzorce sekretów dopasowują CAŁE tokeny, więc
# fragment urwany w połowie (``AKIAABCDEF``, JWT bez trzeciego segmentu) nie zostałby złapany,
# a redakcja wcześniejszych sekretów skraca tekst na tyle, że taki ogon potrafi wjechać
# w zachowywane ``_MAX_KEEP`` znaków. Tniemy go razem z oknem.
_TRAILING_TOKEN = re.compile(r"\S+\Z")

# --- Sekrety (najwyższe ryzyko) ---
_PRIVATE_KEY = re.compile(
    r"-----BEGIN[^-]*PRIVATE KEY-----.*?-----END[^-]*PRIVATE KEY-----", re.DOTALL
)
_SECRET_TOKEN = re.compile(
    r"\b(sk-ant-[\w\-]{8,}|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}|xox[baprs]-[\w\-]{8,}|eyJ[\w\-]{8,}\.[\w\-]{8,}\.[\w\-]{8,})\b"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[\w.\-]{8,}")
# Prefiks nazwy zmiennej ma TWARDY limit — nieograniczony ``[\w.\-]*`` dawał skanowanie
# kwadratowe na długich ciągach znaków słownych. Wartość bez cudzysłowów redagujemy DO KOŃCA
# LINII: hasło bywa wielowyrazowe („haslo: moje tajne haslo"), a nadmiarowa redakcja jest
# tańsza niż wyciek.
_SECRET_ASSIGN = re.compile(
    r"(?i)\b([\w.\-]{0,64}(?:password|passwd|hasło|haslo|secret|api[_-]?key|apikey"
    r"|access[_-]?key|token))(\s*[:=]\s*)(\"[^\"\n]*\"|'[^'\n]*'|[^\n]+)"
)

# --- Dane prywatne / identyfikatory ---
_ACCOUNT = re.compile(r"\b[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)*\b")  # e-mail lub user@host
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_H16 = r"[0-9A-Fa-f]{1,4}"
# IPv6: zapis pełny oraz skrócony ``::``. Wariant zaczynający się od ``::`` wymaga co najmniej
# dwóch grup — ``::1`` to loopback (nie adres serwera), a jednogrupowy wzorzec zjadałby wycinki
# w rodzaju ``x[::2]``.
_IPV6 = re.compile(
    r"(?<![\w.])(?:"
    rf"(?:{_H16}:){{7}}{_H16}"
    rf"|{_H16}(?::{_H16})*::(?:{_H16}(?::{_H16})*)?"
    rf"|::{_H16}(?::{_H16})+"
    r")(?!\w)"
)
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# Nazwa użytkownika to CAŁY segment ścieżki do następnego separatora — także ze spacją
# („Jan Kowalski") i z myślnikiem („jan-kowalski"). Segment ze spacjami przepuszczamy tylko
# wtedy, gdy domyka go separator; inaczej redagujemy sam pierwszy token, żeby nie zjeść prozy
# stojącej za ścieżką.
_USER_PATH = re.compile(
    r"(?i)\b(Users|home)([\\/])([^\\/\s]+(?:[ \t]+[^\\/\s]+)*(?=[\\/])|[^\\/\s]+)"
)
# Wariant nazwy folderu ``~/.claude/projects`` (``C--Users-Jan Kowalski-repo``): separatory
# ścieżki są zakodowane myślnikiem, więc TU myślnik kończy segment.
_USER_PATH_DASH = re.compile(
    r"(?i)\b(Users-|home-)([^\-\\/\s]+(?:[ \t]+[^\-\\/\s]+)*(?=-)|[^\-\\/\s]+)"
)

# --- Sygnały wklejonego logu / wyjścia terminala ---
_SHELL_PROMPT = re.compile(r"[\w.\-]+@[\w.\-]+:[~/][^\s]*[$#]")
_LOG_LEVEL = re.compile(r"\b(INFO|WARN|WARNING|ERROR|DEBUG|TRACE|CRITICAL)\b")

# Słowo naturalnego języka (min. 2 litery) — do oceny, czy zostało cokolwiek sensownego.
_WORD = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{2,}")

_PASTE_MARKER = " […wklejona treść pominięta]"


@dataclass(frozen=True)
class Sanitized:
    """Wynik oczyszczenia promptu: bezpieczny tekst, kategorie redakcji, decyzja o pominięciu."""

    text: str
    categories: frozenset[str]
    dropped: bool


def redact_inline(text: str) -> tuple[str, frozenset[str]]:
    """Zastąp wrażliwe/prywatne wartości etykietami; zwróć (tekst, zbiór usuniętych kategorii)."""
    categories: set[str] = set()

    def _apply(pattern: re.Pattern[str], repl: str, category: str, value: str) -> str:
        new_value, count = pattern.subn(repl, value)
        if count:
            categories.add(category)
        return new_value

    text = _apply(_PRIVATE_KEY, "[SEKRET]", "secret", text)
    text = _apply(_SECRET_TOKEN, "[SEKRET]", "secret", text)
    text = _apply(_BEARER, "Bearer [SEKRET]", "secret", text)
    text, assign_count = _SECRET_ASSIGN.subn(r"\1\2[SEKRET]", text)
    if assign_count:
        categories.add("secret")
    text = _apply(_ACCOUNT, "[KONTO]", "account", text)
    text = _apply(_IPV4, "[IP]", "ip", text)
    text = _apply(_IPV6, "[IP]", "ip", text)
    text = _apply(_UUID, "[ID]", "id", text)
    text, path_count = _USER_PATH.subn(r"\1\2[UŻYTKOWNIK]", text)
    text, dash_count = _USER_PATH_DASH.subn(r"\1[UŻYTKOWNIK]", text)
    if path_count or dash_count:
        categories.add("path")
    return text, frozenset(categories)


def redact_text(text: str) -> str:
    """Sama redakcja w miejscu (bez logiki wklejek) — do komunikatów commitów i etykiet projektu."""
    return redact_inline(text)[0]


def person_label(person: str) -> str:
    """Etykieta osoby do WYNIKU (JSON, Markdown, zapytanie LLM) — nigdy adres e-mail.

    ``--author`` / ``git config user.email`` zostają adresem, bo tym filtrujemy ``git log``;
    na zewnątrz idzie imię wyprowadzone z części lokalnej adresu (``jan.kowalski@firma.pl`` →
    ``Jan Kowalski``). Wejście bez ``@`` przepuszczamy przez zwykłą redakcję w miejscu.
    """
    text = person.strip()
    if not text:
        return ""
    local, at_sign, _domain = text.partition("@")
    if not at_sign:
        return redact_text(text)
    words = [word for word in re.split(r"[._\-]+", local.partition("+")[0]) if word]
    if not words:
        return "[KONTO]"
    return " ".join(word[:1].upper() + word[1:] for word in words)


def _scan_window(text: str) -> str:
    """Okno wejścia dla wzorców — ucięte na GRANICY TOKENU, nigdy w jego środku.

    Cięcie w połowie tokenu zostawiałoby fragment sekretu, którego żaden wzorzec nie dopasuje
    (dopasowania są całotokenowe), a który po skróceniu wcześniejszych sekretów mieści się
    w zachowywanym wyniku. Gdy okno kończy się w środku tokenu, cały ten token wypada.
    """
    if len(text) <= _MAX_SCAN:
        return text
    window = text[:_MAX_SCAN]
    if text[_MAX_SCAN].isspace():
        return window
    return _TRAILING_TOKEN.sub("", window)


def _looks_like_paste(text: str) -> bool:
    """Heurystyka: czy prompt zawiera wklejony log/zrzut (wielolinijkowy, długi, znaki powłoki)."""
    return (
        "\n" in text
        or len(text) > 400
        or _SHELL_PROMPT.search(text) is not None
        or _LOG_LEVEL.search(text) is not None
    )


def _leading_instruction(text: str) -> str:
    """Wiodąca instrukcja przed pierwszym sygnałem wklejki (newline / powłoka / poziom logu)."""
    bounds = [len(text)]
    newline = text.find("\n")
    if newline != -1:
        bounds.append(newline)
    for pattern in (_SHELL_PROMPT, _LOG_LEVEL):
        match = pattern.search(text)
        if match is not None:
            bounds.append(match.start())
    head = text[: min(bounds)].strip()
    return head.rstrip(" \"'[({:-").strip()  # utnij osieroconą interpunkcję otwierającą wklejkę


def _too_little(text: str) -> bool:
    """Czy po redakcji zostało za mało treści, by cokolwiek powiedzieć (sam zrzut → pomiń)."""
    return len(_WORD.findall(text)) < 2


def sanitize_prompt(raw: str) -> Sanitized:
    """Oczyść prompt: redakcja w miejscu + przycięcie wklejek + decyzja o pominięciu."""
    # Najpierw okno, potem wzorce: ogon i tak zostałby przycięty po redakcji, a skanowanie
    # dziesiątek kilobajtów jednego tokenu jest kwadratowe (patrz ``_MAX_SCAN``).
    stripped = raw.strip()
    text = _scan_window(stripped)
    # Decyzja o wklejce z PEŁNEJ długości: wejście dłuższe niż okno jest wklejką z definicji
    # (``_MAX_SCAN`` >> progu 400 znaków), więc pozostałe sygnały sprawdzamy tylko w oknie.
    if len(stripped) > _MAX_SCAN or _looks_like_paste(text):
        head = _leading_instruction(text)
        redacted, categories = redact_inline(head)
        redacted = redacted.strip()
        categories = categories | {"paste"}
        if _too_little(redacted):
            return Sanitized(text="", categories=frozenset(categories), dropped=True)
        if len(redacted) > _MAX_KEEP:
            redacted = redacted[:_MAX_KEEP].rstrip()
        return Sanitized(
            text=(redacted + _PASTE_MARKER).strip(),
            categories=frozenset(categories),
            dropped=False,
        )
    redacted, categories = redact_inline(text)
    redacted = redacted.strip()
    if not redacted:
        return Sanitized(text="", categories=categories, dropped=True)
    if len(redacted) > _MAX_KEEP:
        redacted = redacted[:_MAX_KEEP].rstrip() + " […]"
    return Sanitized(text=redacted, categories=categories, dropped=False)
