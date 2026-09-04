"""Powiadomienia_teams — cotygodniowy asystent uzupełniania zmian w Microsoft Shifts.

Raz w tygodniu (domyślnie **piątek 16:00**, `Europe/Warsaw` — patrz `config.Settings.run_weekday`)
usługa wykrywa osoby bez uzupełnionego grafiku, pisze do nich 1:1 na Teams, interpretuje odpowiedź
w języku naturalnym i po jawnym „tak" zapisuje zmiany do Shifts. Układ warstw opisuje README.

``__version__`` jest JEDYNYM źródłem numeru wersji w kodzie: `pyproject.toml` czyta go stąd
(`[tool.hatch.version]`), a `tools/check_versions.py` pilnuje, żeby `Dockerfile` i
`docker-compose.yml` mówiły to samo. Wcześniej numer mieszkał w trzech plikach naraz i przy każdym
wydaniu podbijało się go ręcznie — a rozjazd oznacza, że `image:` w compose przestaje jednoznacznie
mówić, co działa na serwerze.
"""

__version__ = "0.2.19"
