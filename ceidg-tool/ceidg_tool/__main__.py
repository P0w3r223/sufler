"""`python -m ceidg_tool …` oraz punkt wejścia skryptu `ceidg-tool`."""

from .cli import app, run

__all__ = ["app", "run"]

if __name__ == "__main__":
    run()
