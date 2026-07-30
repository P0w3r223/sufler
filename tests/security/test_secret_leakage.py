"""Bezpieczeństwo: sekrety nie wyciekają przez ``repr`` obiektów ustawień.

Klucze/tokeny to dane poufne (klucz Claude API, hasło bota Teams).
Przypadkowe zalogowanie obiektu ustawień albo traceback nie może ich ujawnić —
dlatego pola sekretne mają ``field(repr=False)``. Testy to pilnują dla obu
nośników sekretów.
"""

from __future__ import annotations

from workmate.config import AgentSettings, TeamsSettings


def test_agent_api_key_absent_from_repr():
    assert "sekret-klucz-abc" not in repr(AgentSettings(api_key="sekret-klucz-abc"))


def test_teams_password_absent_from_repr():
    assert "tajne-haslo-xyz" not in repr(TeamsSettings(app_password="tajne-haslo-xyz"))
