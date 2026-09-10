"""Rejestr sekretów i maskowanie — szew skopiowany z `ceidg-tool/ceidg_tool/config.py`
(linie ~103-148, kopia z 2026-09-10).

**Etap 1 nie ma żadnego sekretu.** Nie ma tokenu, nie ma klucza do modelu, nie ma
poświadczenia — bo nie ma z czym się łączyć (`docs/adr/0001`, decyzja 2). Rejestr jest więc
pusty i `mask_tokens` na realnym wejściu nic nie zmienia.

To zdanie stoi tutaj celowo, zamiast zostać przemilczane. Maskowanie, o którym się wierzy, że
coś chroni, a które nie chroni niczego, jest gorsze od jego braku: daje pewność bez pokrycia.
Szew istnieje, żeby późniejsze etapy miały gdzie wpiąć prawdziwy sekret, a nie żeby dziś coś
osłaniać.

Wzorzec JWT zostaje z jednego powodu: kosztuje zero, a odpis dostarczony przez operatora jest
plikiem z zewnątrz i nikt nie obiecywał, że nie znajdzie się w nim wklejony token.
"""

from __future__ import annotations

import re

# Kształt JWT — trzy człony base64url rozdzielone kropkami.
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (JWT_RE,)

# Wartości sekretów, które ten proces faktycznie trzyma. Wzorzec łapie **kształt**, ten zbiór
# łapie **wartość**. Dziś nikt do niego nic nie dopisuje — patrz docstring modułu.
_KNOWN_SECRETS: set[str] = set()

# Krótkiej wartości nie rejestrujemy: sekret długości 3 zamieniłby każdy tekst zawierający te
# trzy znaki w `<token>`, czyli maskowanie zjadłoby komunikaty zamiast chronić sekret.
MIN_REGISTERED_SECRET = 12


def register_secret(value: str | None) -> None:
    """Dopisuje konkretną wartość do maskowania."""
    if value and len(value) >= MIN_REGISTERED_SECRET:
        _KNOWN_SECRETS.add(value)


def forget_secrets() -> None:
    """Czyści rejestr — wyłącznie dla izolacji testów."""
    _KNOWN_SECRETS.clear()


def mask_tokens(text: str) -> str:
    """Zastępuje każdy sekret w tekście znacznikiem `<token>`.

    Dwa mechanizmy, w tej kolejności: znane **wartości**, potem znane **kształty**.
    """
    for secret in _KNOWN_SECRETS:
        text = text.replace(secret, "<token>")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("<token>", text)
    return text
