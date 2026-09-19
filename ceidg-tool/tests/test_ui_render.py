"""Ścieżka ekranowa wobec wrogich danych z rejestru (uzupelnienie-01.md §B).

`rich` traktuje nawiasy kwadratowe jak znaczniki, więc nazwa firmy wpisana do rejestru
jest wejściem sterującym: `[/b]` wywraca program wyjątkiem spoza taksonomii `CeidgError`
(czyli bez czytelnego komunikatu i bez powrotu do menu), `[link=…]` zamienia rekord
w klikalny odnośnik na obcy adres, a sekwencja ESC potrafi wyczyścić ekran i podmienić
to, co operator widzi. Te testy pilnują, że dane przechodzą jako zwykły tekst.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from ceidg_tool.console import ConsoleEvents
from ceidg_tool.ui.render import ConsoleView
from ceidg_tool.ui.texts import Block

HOSTILE = "PIEKARNIA [/b] [red]X[/red] [link=http://zly.example]klik[/link]"


def view_and_output() -> tuple[ConsoleView, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, width=200, no_color=True, highlight=False, soft_wrap=True)
    return ConsoleView(console), buffer


@pytest.mark.parametrize(
    "block",
    [
        Block(title="Karta", rows=(("nazwa", HOSTILE),)),
        Block(title="Karta", headers=("pole", "wartość"), rows=(("nazwa", HOSTILE),)),
        Block(title=HOSTILE),
        Block(title="Karta", notes=(HOSTILE,)),
    ],
    ids=["klucz-wartość", "z nagłówkami", "w tytule", "w uwadze"],
)
def test_a_hostile_name_never_reaches_rich_as_markup(block: Block) -> None:
    """Bez tego `[/b]` w nazwie kończy `sprawdz-nip` śladem stosu, bo `MarkupError`
    nie jest `CeidgError` i nie łapie go ani CLI, ani pętla menu kreatora."""
    view, buffer = view_and_output()

    view.block(block)  # nie może rzucić

    printed = buffer.getvalue()
    assert "[/b]" in printed  # znacznik widoczny dosłownie, a nie zjedzony
    assert "[red]" in printed and "[link=" in printed


def test_hostile_text_in_messages_and_errors_does_not_crash() -> None:
    """Ścieżka błędu musi znieść to samo wejście co ścieżka sukcesu — inaczej program
    nie potrafi nawet zgłosić własnego błędu."""
    view, buffer = view_and_output()

    view.message(HOSTILE)
    view.warning(HOSTILE)
    view.error(HOSTILE)

    printed = buffer.getvalue()
    assert printed.count("[/b]") == 3
    assert "Uwaga:" in printed and "Błąd:" in printed


def test_control_sequences_are_stripped_before_printing() -> None:
    """Sekwencja ESC z rejestru nie ma prawa sterować terminalem operatora."""
    view, buffer = view_and_output()

    view.block(Block(title="Karta", rows=(("nazwa", "FIRMA \x1b[2J\x07 SP."),)))

    printed = buffer.getvalue()
    assert "\x1b[2J" not in printed and "\x07" not in printed
    assert "FIRMA" in printed and "SP." in printed


def test_the_token_is_masked_on_the_screen_path() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlX3Rlc3Rvd2E"
    view, buffer = view_and_output()

    view.message(f"nagłówek Bearer {jwt}")

    assert jwt not in buffer.getvalue()


def test_progress_messages_take_the_same_hostile_input() -> None:
    """`ConsoleEvents.on_message` niesie nazwę raportu z API — tą samą drogą co reszta."""
    buffer = io.StringIO()
    events = ConsoleEvents(Console(file=buffer, width=200, no_color=True), quiet=True)

    events.on_message(f"Raport pobrany: {HOSTILE}\x1b[2J")

    printed = buffer.getvalue()
    assert "[/b]" in printed and "\x1b[2J" not in printed
