"""Powiadomienia_teams — cotygodniowy asystent uzupełniania zmian w Microsoft Shifts.

Raz w tygodniu (domyślnie **piątek 16:00**, `Europe/Warsaw` — patrz `config.Settings.run_weekday`)
usługa wykrywa osoby bez uzupełnionego grafiku, pisze do nich 1:1 na Teams, interpretuje odpowiedź
w języku naturalnym i po jawnym „tak" zapisuje zmiany do Shifts. Układ warstw opisuje README.

Numer wersji mieszka w PIĘCIU miejscach — tutaj, w `pyproject.toml`, w `Dockerfile` (`ARG WERSJA`),
w `deploy/docker-compose.yml` i w `scripts/build-image.sh`. Nic ich automatycznie nie wywodzi jedne
z drugich, więc zgodności pilnuje bramka: `tests/test_wersje.py`. Obowiązuje **tag obrazu Dockera**;
te napisy mają się z nim zgadzać, a nie go zastępować.

Do 2026-09-07 stało tu zdanie, że `pyproject.toml` czyta wersję stąd przez `[tool.hatch.version]`
i że pilnuje tego skrypt check_versions — nieprawdziwe podwójnie i przez to rozjazd żył latami.
"""

__version__ = "0.2.24"
