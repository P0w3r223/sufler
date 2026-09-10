"""ceidg_tool — pobieranie danych JDG z API v3 Hurtowni Danych CEIDG do Excela."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ceidg-tool")
except PackageNotFoundError:  # uruchomienie z katalogu bez instalacji
    __version__ = "0.0.0+dev"

__all__ = ["__version__"]
