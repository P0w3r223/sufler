"""WorkMate — wewnętrzny serwer MCP pionu Inteligentnych Technologii.

Faza 1 (MVP): wąskie, typowane narzędzia *tylko do odczytu*, które dają
Claude Code każdego developera dostęp do wspólnej bazy wiedzy — notatek ze
spotkań i statusu projektów.

Architektura: "jeden rdzeń, wiele drzwi". Cała logika mieszka w ``core`` i jest
niezależna od interfejsu; ``adapters`` to tanie "drzwi" (MCP teraz, Teams/GitHub
później). Szczegóły: ``docs/explanation/architecture.md``.
"""

__version__ = "1.7.0"
