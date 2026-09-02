"""Katalog narzędzia ``Bash`` — jedyne wejście do wykonawcy rozmowy (ADR 0012)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from workmate.core.ports.command import CommandRunner

from workmate.core.application.tools.spec import ToolSpec, _envelope
from workmate.core.domain.workspace import WorkspaceScope
from workmate.core.errors import WorkMateError
from workmate.core.ports.document import FILE_REPLY_FORMATS

# Mapa montaży wyprowadziła się stąd do sekcji `ENVIRONMENT` promptu (etap 6 planu przebudowy).
# Tu zostaje to, co dotyczy URUCHAMIANIA polecenia: gdzie startuje, czym szukać w notatkach
# i jakie ma limity. Układ ścieżek jest własnością świata, a nie tej czynności — trzymany
# w obu miejscach dawałby dwa źródła do synchronizacji przy następnym montażu.
_SHELL_HEAD = """\
Uruchom polecenie powłoki (bash) w izolowanym kontenerze bez dostępu do sieci.

Startujesz w katalogu roboczym tej rozmowy (/home/scratchpad/…) — pliki tworzone tutaj
przeżywają do kolejnych tur."""

# Akapit o skrzynce doklejany WYŁĄCZNIE, gdy dostawa faktycznie działa (bramka ``enable_file_reply``
# na drzwiach). Bezwarunkowa obietnica dostawy przy wyłączonej bramce byłaby dokładnie tym
# defektem, który ta zdolność likwiduje: model dostaje kod 0 i ciszę, a pliki rosną na wolumenie.
_SHELL_OUTBOX = """
Podkatalog `outputs/` w katalogu roboczym jest skrzynką nadawczą: plik zapisany tam
wysyłam rozmówcy po zakończeniu tury i usuwam ze skrzynki, więc trzymaj tam wyłącznie
gotowe wyniki, a materiał roboczy piętro wyżej. Dozwolone rozszerzenia: {formats}.
Pliki `md`/`txt` zapisz wprost (np. `... > outputs/raport.md`); `pdf`/`docx` twórz
komendą `workmate-render --format pdf --output outputs/raport.pdf < tresc.md`.
Plik w budowie nazywaj `*.tmp` i zmieniaj nazwę, gdy jest gotowy — pozycje `.tmp`
pomijam przy wysyłce."""

_SHELL_TAIL = """
Do przeszukiwania notatek użyj `workmate-search "fraza"` — korpus jest polski
i odmieniony, więc dopasowanie wzorca (grep) gubi trafienia.
Pliku, który nie jest tekstem, nie czytaj `cat`-em — `workmate-extract plik.pdf`
wypisze tekst z pdf, docx, xlsx, pptx i html (długie wyjście filtruj, np. `| head`).
Wyjście jest przycinane do 64 KB (flaga `truncated`), a polecenie przerywane po
`timeout_s` sekund (domyślnie 60, maksymalnie 300; flaga `timed_out`).
Zapis ma sufit rozmiaru pojedynczego pliku: po przekroczeniu polecenie ginie,
a plik zostaje na dysku UCIĘTY — komunikat podaje limit i mówi, co z nim zrobić."""


def build_shell_catalog(
    scope: WorkspaceScope,
    runner: CommandRunner,
    *,
    workspace_root: str,
    default_timeout_s: int = 60,
    outbox_enabled: bool = False,
) -> list[ToolSpec]:
    """Zbuduj narzędzie POWŁOKI dla danej rozmowy (ADR 0057).

    Polecenie biegnie w OSOBNYM kontenerze bez sieci — ``runner`` to klient gniazda, nie
    lokalny ``subprocess``. Katalog roboczy jest DOMKNIĘTY w closurze (jak ``scope``
    w ``build_workspace_catalog``): model nie widzi go w schemacie, więc nie POPROSI o cudzą
    rozmowę, a polecenia startują tam, gdzie leżą jego własne pliki robocze. Nie jest to
    zamknięcie — wykonawca montuje cały wolumen brudnopisu i ustawia wyłącznie ``cwd``, więc
    powłoka sięgnie ścieżką bezwzględną wszędzie. Granicą jest brak sieci (ADR 0057), a nie
    domknięcie katalogu.

    Mapy montaży opis JUŻ NIE NIESIE — od etapu 6 mieszka w sekcji ``ENVIRONMENT`` promptu,
    w wariancie wybieranym tą samą flagą, która buduje to narzędzie (``shell_available``
    w ``build_agent_runtime``). Opis nosił ją zastępczo, dopóki prompt opisywał świat narzędzi.

    ``outbox_enabled`` steruje akapitem o ``outputs/``: dostawa ma WŁASNĄ bramkę po stronie
    drzwi, a opis obiecujący ją bezwarunkowo kłamałby przy konfiguracji „powłoka tak, załączniki
    nie". Warianty są dwa i stałe per proces, więc cache prefiksu ``tools+system`` dzieli się
    najwyżej na dwa — nie na jeden per rozmowa.
    """
    workdir = f"{workspace_root.rstrip('/')}/{scope.dirpath()}"
    outbox = _SHELL_OUTBOX.format(formats="/".join(FILE_REPLY_FORMATS)) if outbox_enabled else ""
    description = f"{_SHELL_HEAD}{outbox}{_SHELL_TAIL}"

    def run_command(command: str, timeout_s: int = 0) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            if timeout_s < 0:
                # ``timeout_s`` przychodzi OD MODELU i nie miał dolnej granicy: wartość ujemna
                # kończyła się natychmiastowym ``timed_out`` bez uruchomienia polecenia, więc
                # model widział „polecenie za wolne" tam, gdzie naprawdę podał złą liczbę,
                # i poprawiał nie ten parametr. Zero zostaje umowne — znaczy „użyj domyślnego".
                return {
                    "error": (
                        f"`timeout_s` nie może być ujemny (podano {timeout_s}). "
                        f"Podaj liczbę sekund albo 0, żeby użyć domyślnych {default_timeout_s} s."
                    )
                }
            result = runner.run(
                command, cwd=workdir, timeout_s=float(timeout_s or default_timeout_s)
            )
            wynik: dict[str, Any] = {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "truncated": result.truncated,
                "timed_out": result.timed_out,
            }
            # Cisza po udanym poleceniu czyta się jak awaria i zaprasza do powtórki — a to
            # NORMALNY wynik `mkdir`, `mv` czy przekierowania do pliku. Nazywamy ją wprost,
            # tym samym ruchem co ``count`` w ``search_notes``: pusty zbiór ma być widoczny
            # jako zbiór pusty, a nie jako brak odpowiedzi.
            if result.exit_code == 0 and not result.stdout and not result.stderr:
                wynik["note"] = "Polecenie zakończyło się powodzeniem i nic nie wypisało."
            return wynik

        return _envelope(build, errors=(WorkMateError,))

    return [ToolSpec("Bash", description, run_command)]
