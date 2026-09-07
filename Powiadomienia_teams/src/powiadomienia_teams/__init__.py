"""Powiadomienia_teams — cotygodniowy asystent uzupełniania zmian w Microsoft Shifts.

Raz w tygodniu (domyślnie **piątek 16:00**, `Europe/Warsaw` — patrz `config.Settings.run_weekday`)
usługa wykrywa osoby bez uzupełnionego grafiku, pisze do nich 1:1 na Teams, interpretuje odpowiedź
w języku naturalnym i po jawnym „tak" zapisuje zmiany do Shifts. Układ warstw opisuje README.

``__version__`` NIE jest dziś jedynym źródłem numeru wersji i nic tego nie pilnuje. Do 2026-09-07
stało tu zdanie twierdzące odwrotnie i było nieprawdziwe podwójnie: `pyproject.toml` ma wersję
wpisaną statycznie (nie czyta jej stąd przez `[tool.hatch.version]`), a `tools/check_versions.py`
nigdy nie istniał. Numer mieszka w pięciu miejscach — tutaj, w `pyproject.toml`, w `Dockerfile`
(`ARG WERSJA`), w `deploy/docker-compose.yml` i w `scripts/build-image.sh` — i bywają rozjechane.
Obowiązuje **tag obrazu Dockera**, nie ten napis. Wyrównanie i strażnik należą do kroku WYDANIA,
bo dopiero wtedy wiadomo, jaką liczbę wpisać (patrz `docs/plan-rozwoju.md`, „Pozycje otwarte").
"""

__version__ = "0.2.19"
