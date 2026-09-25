"""Wiring autoryzacji per nadawca: odczyt notatek, powłoka, tożsamości mutacji."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sufler.config import (
    ShellSettings,
    TeamsGraphSettings,
)

if TYPE_CHECKING:
    from sufler.core.application.note_read_authz import NoteReadAuthorizer
    from sufler.core.application.shell_authz import ShellAuthorizer

logger = logging.getLogger(__name__)


def _build_note_read_authorizer(settings: TeamsGraphSettings) -> NoteReadAuthorizer | None:
    """Bramka członkostwa ODCZYTU bazy wiedzy (ADR 0062) albo ``None``.

    ``None``, gdy ``enable_note_read_authz`` wyłączona (domyślnie) — odczyt zachowuje się jak przed
    ADR 0062. Włączona: config wymusił istnienie mapy tożsamości (ten sam plik co zapis, ADR
    0042/0062), więc składamy authorizer nad ``YamlIdentityDirectory`` (fail-closed). Wpinany w
    ``_build_responder``: bramkuje per-turową powierzchnię odczytu agenta — **``Project`` oraz**
    ``SearchNotes``/``GetNote``/``ListProjects`` — ORAZ komendy ``/szukaj``/``/projekty``.
    ``Project`` jest tu wymieniony PIERWSZY nie dla porządku: jego akcja ``status`` serwuje treść
    notatek, ADR 0062 wciągnął go za bramkę jako „jedyną drogę odczytu, która przeżyła jej
    wpięcie", a przy włączonej powłoce jest JEDYNYM narzędziem odczytu, jakie ta bramka wystawia
    (trójka jest wtedy zbędna). Poprzednia redakcja tego opisu go pomijała.
    Powłoka i drzwi MCP są POZA zakresem — nie niosą tożsamości nadawcy (ADR 0062 §Decision 4
    i addendum).
    """
    if not settings.enable_note_read_authz:
        return None
    from sufler.adapters.outbound.graph_identity_directory import YamlIdentityDirectory
    from sufler.core.application.note_read_authz import NoteReadAuthorizer

    logger.info(
        "Autoryzacja ODCZYTU bazy wiedzy WŁĄCZONA (ADR 0062) — narzędzia agenta oraz komendy "
        "/szukaj i /projekty wymagają rozpoznanego członka pionu przez mapę tożsamości %s "
        "(fail-closed). Powłoka (cat/sufler-search po montażu ro) i drzwi MCP są POZA zakresem.",
        settings.meeting_note_identities,
    )
    return NoteReadAuthorizer(YamlIdentityDirectory(settings.meeting_note_identities))


def _build_shell_authorizer(
    settings: TeamsGraphSettings, shell_settings: ShellSettings
) -> ShellAuthorizer | None:
    """Bramka członkostwa POWŁOKI (ADR 0063) albo ``None``.

    ``None``, gdy powłoka wyłączona (``SUFLER_ENABLE_SHELL`` domyślnie OFF) — powłoki wtedy nie
    ma, nie ma czego bramkować. Włączona: WYMAGA mapy tożsamości (fail-fast tutaj, jak zapis ADR
    0042 / odczyt ADR 0062), bo bez niej bramka nie miałaby po czym rozpoznać nadawcy — i wtedy
    każdy dostałby powłokę, czyli dokładnie luka, którą ADR 0063 zamyka. Bramka jest WBUDOWANA we
    flagę powłoki (nie osobny toggle): powłoka jest już opt-in OFF, więc „shell ON" znaczy „ON i
    bramkowany", bez okna otwartego. Składa authorizer nad ``YamlIdentityDirectory`` (fail-closed).
    Wpinany w ``_build_responder``; egzekwuje per-turową fabrykę powłoki po nadawcy. Drzwi MCP i CLI
    (jeden zaufany operator, brak ``sender_id``) są POZA zakresem, jak w ADR 0042/0062.

    **Nowy wiersz w mapie tożsamości = powłoka dla tej osoby** (ADR 0070 §3). Mapa jest rosterem
    członkostwa pionu, nie katalogiem kont Jiry, a przy ``ENABLE_SHELL=true`` członkostwo niesie
    uruchamianie kodu w wykonawcy. Bramka nie czeka na żadną flagę odczytu — czyta mapę wprost.
    Rozdzielenie „kto jest członkiem" od „kto ma powłokę" zostało w ADR 0070 §3 rozważone
    i ODRZUCONE na koszt; odwrócenie tego wymaga własnego ADR-a o modelu uprawnień.
    """
    if not shell_settings.enabled:
        return None
    if not settings.meeting_note_identities.is_file():
        # Powłoka bramkowana członkostwem (ADR 0063): bez mapy tożsamości nie ma po czym rozpoznać
        # nadawcy, więc bramka nie miałaby jak działać — a powłoka sięga ścieżką bezwzględną poza
        # scope rozmowy. Fail-fast (ten sam plik co zapis/odczyt i worklogi).
        raise SystemExit(
            "SUFLER_ENABLE_SHELL=true na drzwiach Teams wymaga SUFLER_TEAMS_GRAPH_IDENTITIES "
            "= ścieżka do mapy tożsamości (członkostwo bramkuje powłokę, ADR 0063); brak pliku: "
            f"{settings.meeting_note_identities}."
        )
    from sufler.adapters.outbound.graph_identity_directory import YamlIdentityDirectory
    from sufler.core.application.shell_authz import ShellAuthorizer

    logger.info(
        "Bramka członkostwa POWŁOKI WŁĄCZONA (ADR 0063) — narzędzie Bash tylko dla rozpoznanego "
        "członka pionu przez mapę tożsamości %s (fail-closed). Cross-read MIĘDZY członkami domyka "
        "montaż per-rozmowa: wykonawca stoi na każdą rozmowę osobno i widzi wyłącznie jej "
        "podkatalog brudnopisu (infra ADR 0012).",
        settings.meeting_note_identities,
    )
    return ShellAuthorizer(YamlIdentityDirectory(settings.meeting_note_identities))


def _build_mutation_identities(settings: TeamsGraphSettings) -> object | None:
    """Mapa tożsamości dla MUTACJI bazy wiedzy (ADR 0065) albo ``None`` — fail-closed.

    Osobno od ``_build_note_read_authorizer``, bo to inna bramka i inny plik konfiguracji mógłby
    ją włączyć. Wspólny jest za to warunek konieczny: bez mapy nie ma komu przypisać zmiany
    ani kogo zapytać o potwierdzenie, więc brak pliku ZAMYKA mutacje zamiast je przepuścić.
    """
    if not settings.enable_note_mutation:
        return None
    if not settings.meeting_note_identities.is_file():
        logger.error(
            "SUFLER_TEAMS_GRAPH_ENABLE_NOTE_MUTATION=true, ale mapy tożsamości %s nie ma — "
            "mutacje bazy wiedzy POZOSTAJĄ WYŁĄCZONE (fail-closed, ADR 0065).",
            settings.meeting_note_identities,
        )
        return None
    from sufler.adapters.outbound.graph_identity_directory import YamlIdentityDirectory

    logger.warning(
        "MUTACJA bazy wiedzy WŁĄCZONA (ADR 0065): agent może zmieniać notatki przez File(edit)"
        "%s. Każda zmiana idzie przez migawkę i niezależnego sędziego; nadawca musi być "
        "rozpoznany przez mapę %s.",
        " ORAZ JE USUWAĆ (File(delete))" if settings.enable_note_delete else "",
        settings.meeting_note_identities,
    )
    return YamlIdentityDirectory(settings.meeting_note_identities)
