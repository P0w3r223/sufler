"""Deterministyczne parsowanie mówców z transkryptu (M3 / ADR 0047).

Rdzeń domenowy — bez I/O i bez SDK (jak ``paths.py``/``sanitize.py``): z surowego
transkryptu wydobywa ETYKIETY MÓWCÓW jako JEDYNE dozwolone źródło nazwisk uczestników.
To wprost realizuje zasadę anty-halucynacji ADR 0047: model NIE zgaduje tożsamości —
lista uczestników notatki pochodzi z tego deterministycznego parsera, nie z LLM, a
prozie wolno używać wyłącznie etykiet z ``allowed_names()``.

Formaty obsługiwane świadomie (nie pod jeden plik):
- ``Nazwa: treść`` — wynik ``vtt_to_text`` (produkcyjna ścieżka Graph, WebVTT ``<v Nazwa>``);
  najpewniejszy sygnał diaryzacji.
- ``Nazwa   M:SS`` / ``Nazwa   H:MM:SS`` — surowy eksport Teams (znacznik tury).

Gdy diaryzacja jest ZDEGENEROWANA (≤1 etykieta na całe spotkanie albo etykiety zbyt rzadkie
wobec objętości tekstu — realny przypadek surowego wklejenia, gdzie wszystkie tury są
sklejone w jeden akapit), parser NIE zmyśla liczby ani ról: zwraca znane etykiety + JEDEN
uczciwy generyk „pozostali nierozpoznani". Nigdy nie emituje nazwiska spoza transkryptu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Człon nazwy: wielka litera (w tym PL) + dalsze znaki słowa/łącznik/kropka inicjału.
_NAME_WORD = r"[A-ZŁŚŻŹĆŃÓĄĘ][\wąćęłńóśżźĄĆĘŁŃÓŚŻŹ.'-]*"
# Etykieta = 1–4 takich członów. Nazwiska mówców z Teams to zwykle „Imię Nazwisko".
_NAME = rf"{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}}"
# ``Nazwa: treść`` — wymagamy treści po dwukropku (goły „Nazwa:" to nie tura mówcy).
_COLON_LABEL = re.compile(rf"^\s*({_NAME})\s*:\s+\S")
# ``Nazwa   M:SS`` / ``Nazwa   H:MM:SS`` — znacznik tury w surowym eksporcie Teams. Po czasie
# lookahead „nie cyfra" (nie ``\b``): w realnym eksporcie timestamp bywa SKLEJONY z tekstem
# („0:03Firmach…"), a ``\b`` by tego nie złapał; ``(?!\d)`` odsiewa tylko dłuższe liczby.
_NAME_TIME_LABEL = re.compile(rf"^\s*({_NAME})\s+\d{{1,2}}:\d{{2}}(?::\d{{2}})?(?!\d)")

# Próg „gęstości" diaryzacji: średnio powyżej tylu znaków na turę = etykiety zbyt rzadkie,
# by uznać diaryzację za kompletną (klasyczny sklejony wklej, gdzie tur faktycznie nie widać).
_MAX_CHARS_PER_TURN = 2000

# Uczciwe generyki dla mówców, których transkrypt NIE nazywa — nigdy nazwisko z domysłu.
_UNRECOGNIZED = "Pozostali uczestnicy nierozpoznani (brak etykiet mówców w transkrypcie)"
_NONE = "Uczestnicy nierozpoznani (transkrypt bez etykiet mówców)"


@dataclass(frozen=True)
class SpeakerRoster:
    """Zbiór mówców wyprowadzony deterministycznie z transkryptu.

    ``speakers`` — rozpoznane etykiety w kolejności pierwszego wystąpienia (jedyne nazwiska
    obecne w transkrypcie). ``diarized`` — czy diaryzacja wygląda kompletnie (≥2 etykiety i
    dostatecznie gęste tury); gdy ``False``, ``participants()`` dokłada JEDEN uczciwy generyk.
    """

    speakers: tuple[str, ...]
    diarized: bool

    def allowed_names(self) -> tuple[str, ...]:
        """Nazwiska, których model MOŻE użyć w prozie — dokładnie te z transkryptu (allowlist)."""
        return self.speakers

    def participants(self) -> list[str]:
        """Deterministyczna lista uczestników do ``NoteMetadata`` (nie z LLM).

        Diaryzacja kompletna → same rozpoznane etykiety. Niekompletna → etykiety + jeden
        uczciwy generyk (albo sam generyk, gdy nikt nie jest nazwany). Zero zgadywania liczby.
        """
        result = list(self.speakers)
        if not self.diarized:
            result.append(_UNRECOGNIZED if self.speakers else _NONE)
        return result


def _match_label(line: str) -> str | None:
    """Wydobądź etykietę mówcy z początku linii (format dwukropka albo Nazwa+czas)."""
    match = _COLON_LABEL.match(line) or _NAME_TIME_LABEL.match(line)
    if match is None:
        return None
    return " ".join(match.group(1).split())  # normalizacja białych znaków w nazwie


def parse_speaker_roster(transcript: str) -> SpeakerRoster:
    """Zbuduj ``SpeakerRoster`` z transkryptu — jedyne źródło nazwisk uczestników (ADR 0047).

    Zlicza tury per etykieta; jako mówców zatrzymuje etykiety WIELOCZŁONOWE (Imię Nazwisko)
    albo powtórzone ≥2×, co odsiewa pojedyncze artefakty „Słowo:" z treści. Diaryzację uznaje
    za kompletną tylko przy ≥2 mówcach i gęstych turach — inaczej ``participants()`` dokłada
    uczciwy generyk zamiast zmyślać nierozpoznanych rozmówców.
    """
    counts: dict[str, int] = {}
    order: list[str] = []
    turns = 0
    for raw in transcript.splitlines():
        label = _match_label(raw)
        if label is None:
            continue
        turns += 1
        if label not in counts:
            counts[label] = 0
            order.append(label)
        counts[label] += 1
    speakers = tuple(name for name in order if " " in name or counts[name] >= 2)
    text_len = len(transcript.strip())
    dense = turns >= 2 and text_len / turns <= _MAX_CHARS_PER_TURN
    diarized = len(speakers) >= 2 and dense
    return SpeakerRoster(speakers=speakers, diarized=diarized)
