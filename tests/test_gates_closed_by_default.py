"""Golden-test bramek: KAŻDA zdolność mutująca i wychodząca jest domyślnie ZAMKNIĘTA.

Rozproszone `default=False` w ``config.py`` łatwo przestawić — przy dopisywaniu pola, przy
scalaniu, przy „tymczasowym" włączeniu do testów. Ten test zbiera je REFLEKSYJNIE, i to na DWÓCH
poziomach:

1. KLASY odkrywamy z modułu ``workmate.config`` (każda dataklasa ``*Settings``), a nie z ręcznej
   listy. Poprzednia wersja miała krotkę ośmiu klas wpisanych z palca — nowa klasa ustawień
   z własną bramką (``ExecManagerSettings`` i ``SkillsSettings`` weszły już po tamtym przeglądzie)
   wymykała się bramce w całości, bo refleksja po polach nigdy jej nie oglądała.
2. POLA odkrywamy po nazwie (``enabled``/``enable_*``), a TYP rozstrzygamy przez
   ``get_type_hints``. Filtr ``f.type == "bool"`` porównywał adnotację jako TEKST, więc bramka
   zapisana jako ``bool | None``, ``Optional[bool]`` czy alias przechodziła niezauważona — a pole
   nie-``bool`` o nazwie bramki (``ScheduleSettings.enabled: str``) znikało z widoku bez śladu.
   Dziś takie pole musi stać w jawnym rejestrze ``_BRAMKI_TROJSTANOWE`` i mieć własny test.

Inwariant (CLAUDE.md, ADR 0006/0021/0034/0065): odczyt jest domyślny, każdy zapis wchodzi przez
własną bramkę wyłączoną z fabryki, a operator włącza ją świadomie — BEZ WYJĄTKU (od 2026-07-31
obejmuje też ``save_note``/``WORKMATE_ENABLE_WRITE``, amendment ADR 0006; od ADR 0065 także
mutację i kasowanie notatek). Jeśli ten test padnie, NIE „naprawiaj" go zmianą oczekiwanej
wartości — to sygnał, że ktoś otworzył bramkę domyślnie.

Środowisko czyści globalny fixture z ``tests/conftest.py`` (zdejmuje ``WORKMATE_*``): sprawdzamy
domyślne wartości KODU, nie bieżącą konfigurację maszyny ani repozytoryjnego ``.env``.

Od przeglądu kompozycji 2026-08-17 mierzymy też TRZECIĄ drogę otwarcia bramki: SZABLONY
(``.env.example``, ``deploy/docker/env.example``). Oba pliki mają w nagłówku polecenie „skopiuj do
``.env``/``env``", więc aktywna (nieskomentowana) linia włączająca bramkę jest równoważna
domyślnemu ``True`` w kodzie — z tą różnicą, że dwie pierwsze drogi miały bramkę testową, a ta
nie miała żadnej. Zastane naruszenia: ``WORKMATE_ENABLE_WRITE=true`` w ``.env.example`` (trzy
linie pod komentarzem „odkomentuj, żeby…") i cztery bramki zapisu notatek w szablonie floty.
"""

from __future__ import annotations

import dataclasses
import inspect
import typing
from pathlib import Path

import pytest

from workmate import config
from workmate.config import ScheduleSettings, Settings

# Klasy ustawień znane z przeglądu 2026-08-17. Refleksja MUSI je wszystkie znaleźć — inaczej
# odkrywanie przestało działać (np. ktoś zmienił konwencję nazw) i cały test cichnie.
# Dopisanie klasy jest darmowe; USUNIĘCIE wymaga świadomego skreślenia wpisu tutaj.
_ZNANE_KLASY = {
    "Settings",
    "TeamsSettings",
    "AgentSettings",
    "ConversationSettings",
    "EventsSettings",
    "RetrievalSettings",
    "TeamsGraphSettings",
    "WorkspaceSettings",
    "ShellSettings",
    "ExecManagerSettings",
    "SkillsSettings",
    "GithubSettings",
    "JiraSettings",
    "TeamsPushSettings",
    "ScheduleSettings",
    "TeamsDigestSettings",
}

# Bramki, które NIE są ``bool`` — każda musi mieć własny test „domyślnie zamknięta" niżej,
# bo automat po wartości ``False`` ich nie obejmie. Rejestr jest jawny właśnie po to, żeby nowa
# bramka trójstanowa nie mogła się prześlizgnąć jako „nie-bool, więc nie moja sprawa".
_BRAMKI_TROJSTANOWE = {"ScheduleSettings.enabled"}


def _klasy_ustawien() -> dict[str, type]:
    """Wszystkie dataklasy ``*Settings`` zdefiniowane w ``workmate.config``."""
    return {
        name: obj
        for name, obj in vars(config).items()
        if inspect.isclass(obj)
        and dataclasses.is_dataclass(obj)
        and obj.__module__ == "workmate.config"
        and (name.endswith("Settings") or name == "Settings")
    }


def _pola_bramek(cls: type) -> list[dataclasses.Field]:
    """Pola nazwane jak bramka (``enabled``/``enable_*``) — NIEZALEŻNIE od typu."""
    return [
        f for f in dataclasses.fields(cls) if f.name == "enabled" or f.name.startswith("enable_")
    ]


def _typ_pola(cls: type, field_name: str) -> object:
    return typing.get_type_hints(cls)[field_name]


def _bramki_z_env() -> dict[str, bool]:
    """Stan WSZYSTKICH bramek ``bool``, zebrany refleksyjnie z ``from_env()`` każdej klasy."""
    gates: dict[str, bool] = {}
    for name, cls in _klasy_ustawien().items():
        instance = cls.from_env()
        for field in _pola_bramek(cls):
            if _typ_pola(cls, field.name) is bool:
                gates[f"{name}.{field.name}"] = getattr(instance, field.name)
    return gates


# --- odkrywanie: nic się nie wymyka -----------------------------------------


def test_refleksja_widzi_wszystkie_klasy_ustawien() -> None:
    """Bramka bez materiału przechodzi zawsze — to sprawdzenie samego odkrywania."""
    znalezione = set(_klasy_ustawien())
    assert znalezione >= _ZNANE_KLASY, (
        f"refleksja przestała widzieć klasy ustawień: {sorted(_ZNANE_KLASY - znalezione)}"
    )


def test_kazde_pole_o_nazwie_bramki_jest_bool_albo_jawnie_trojstanowe() -> None:
    """Pole nazwane jak bramka, a nie ``bool``, musi stać w rejestrze i mieć własny test.

    Bez tej sondy ``ScheduleSettings.enabled: str = "auto"`` był bramką, której automat nie
    widział — poprzedni filtr odrzucał ją po typie i nikt nie sprawdzał, czy „auto" znaczy
    zamknięte.
    """
    nietypowe = {
        f"{name}.{field.name}"
        for name, cls in _klasy_ustawien().items()
        for field in _pola_bramek(cls)
        if _typ_pola(cls, field.name) is not bool
    }
    assert nietypowe <= _BRAMKI_TROJSTANOWE, (
        f"bramka o nietypowym typie poza rejestrem: {sorted(nietypowe - _BRAMKI_TROJSTANOWE)}. "
        "Dopisz ją do _BRAMKI_TROJSTANOWE i dołóż test jej stanu domyślnego."
    )


def test_kazda_znana_bramka_jest_widziana() -> None:
    """Refleksja musi znaleźć co najmniej bramki znane z przeglądu 2026-08-17."""
    known = {
        "Settings.enable_write",
        "RetrievalSettings.enable_dense",
        "TeamsGraphSettings.enable_file_reply",
        "TeamsGraphSettings.enable_user_file_push",
        "TeamsGraphSettings.enable_user_doc_push",
        "TeamsGraphSettings.enable_meeting_transcript",
        "TeamsGraphSettings.enable_meeting_note_write",
        "TeamsGraphSettings.enable_meeting_note_async",
        "TeamsGraphSettings.enable_thread_note_capture",
        "TeamsGraphSettings.enable_note_read_authz",
        "TeamsGraphSettings.enable_project_brief",
        "TeamsGraphSettings.enable_change_digest",
        # ADR 0065: narzędzie plikowe, mutacja i kasowanie notatki — kasowanie ma bramkę OSOBNĄ
        # od mutacji, związaną z działającą kopią zapasową.
        "TeamsGraphSettings.enable_file_tool",
        "TeamsGraphSettings.enable_note_mutation",
        "TeamsGraphSettings.enable_note_delete",
        "TeamsGraphSettings.enable_trust_labels",
        "WorkspaceSettings.enabled",
        # Powłoka (ADR 0057) — bramka OSOBNA od workspace: tam model tworzy plik narzędziem
        # typowanym, tu uruchamia dowolny kod. Wspólna otwierałaby powłokę po cichu.
        "ShellSettings.enabled",
        "GithubSettings.enable_github_write",
        "GithubSettings.enable_ci_auto_comment",
        "TeamsPushSettings.enable_chat",
        "TeamsPushSettings.enable_channel",
        "TeamsPushSettings.enable_channel_threading",
        "TeamsDigestSettings.enabled",
    }
    assert known <= set(_bramki_z_env())


# --- inwariant: zamknięte z fabryki -----------------------------------------


def test_kazda_bramka_zamknieta_przy_czystym_srodowisku() -> None:
    gates = _bramki_z_env()
    open_gates = {name: value for name, value in gates.items() if value is not False}
    assert not open_gates, (
        f"BRAMKI OTWARTE DOMYŚLNIE: {sorted(open_gates)}. Każda zdolność mutująca i wychodząca "
        "musi być wyłączona z fabryki — operator włącza ją świadomie (CLAUDE.md, ADR 0006)."
    )


def test_kazda_bramka_zamknieta_takze_w_domyslnej_wartosci_pola() -> None:
    """Wiring składa ustawienia też WPROST (``TeamsGraphSettings(...)``), bez ``from_env``.

    ``from_env`` i domyślna wartość pola to dwa osobne miejsca zapisu tej samej decyzji — test
    wyżej pilnuje pierwszego, ten drugiego. ``Settings.enable_write`` nie ma domyślnej (jest
    argumentem wymaganym) i to jest w porządku: tam decyzję podejmuje każde wywołanie z osobna.
    """
    otwarte = {
        f"{name}.{field.name}"
        for name, cls in _klasy_ustawien().items()
        for field in _pola_bramek(cls)
        if _typ_pola(cls, field.name) is bool
        and field.default is not dataclasses.MISSING
        and field.default is not False
    }
    assert not otwarte, f"domyślna wartość POLA otwiera bramkę: {sorted(otwarte)}"


def test_grafik_w_trybie_auto_jest_zamkniety_bez_zamontowanego_cache(tmp_path) -> None:
    """``ScheduleSettings.enabled='auto'`` = bramka trójstanowa: „włącz, jeśli jest z czego".

    Zdolność wychodzi do Graph na POŻYCZONEJ tożsamości (cudzy cache MSAL), więc „auto" nie może
    znaczyć „włączone". Zamknięte jest domyślnie (brak konfiguracji), zamknięte zostaje przy samej
    konfiguracji aplikacji bez cache, i otwiera się dopiero, gdy plik cache naprawdę leży na dysku.
    """
    assert ScheduleSettings.from_env().is_enabled() is False  # czyste środowisko

    skonfigurowany = ScheduleSettings(
        client_id="c", tenant_id="t", token_cache_path=tmp_path / "nie-ma.json"
    )
    assert skonfigurowany.is_enabled() is False  # aplikacja jest, cache nie ma → zamknięte

    cache = tmp_path / "cache.json"
    cache.write_text("{}", encoding="utf-8")
    assert dataclasses.replace(skonfigurowany, token_cache_path=cache).is_enabled() is True


def test_grafik_wylaczony_jawnie_zostaje_wylaczony_mimo_kompletnej_konfiguracji(tmp_path) -> None:
    """``false`` musi wygrywać z „auto" — inaczej nie ma jak wyłączyć zamontowanego cache."""
    cache = tmp_path / "cache.json"
    cache.write_text("{}", encoding="utf-8")
    komplet = ScheduleSettings(client_id="c", tenant_id="t", token_cache_path=cache)

    assert dataclasses.replace(komplet, enabled="false").is_enabled() is False
    assert dataclasses.replace(komplet, enabled="true").is_enabled() is True


# --- inwariant: bramkę otwiera TYLKO świadoma wartość -----------------------


@pytest.mark.parametrize(
    "wartosc", ["false", "0", "no", "off", "", "   ", "tak", "yes-please", "2"]
)
def test_bramki_nie_otwiera_wartosc_inna_niz_zgoda(monkeypatch, wartosc: str) -> None:
    """Literówka i „prawie-prawda" w ``.env`` zostawiają bramkę ZAMKNIĘTĄ (fail-closed).

    ``tak`` i ``yes-please`` są tu celowo: parser przyjmuje angielskie ``yes``, więc polskie „tak"
    i sklejka z ``yes`` to najbliższe realne pomyłki operatora, jakie mogłyby przypadkiem otworzyć
    zapis do bazy wiedzy.
    """
    monkeypatch.setenv("WORKMATE_ENABLE_WRITE", wartosc)
    assert Settings.from_env().enable_write is False


@pytest.mark.parametrize("wartosc", ["true", "TRUE", "  True  ", "1", "yes", "on"])
def test_bramke_otwiera_jawna_zgoda_operatora(monkeypatch, wartosc: str) -> None:
    """Kontrast: bramka MUSI dać się otworzyć — inaczej byłaby martwa, a nie bezpieczna."""
    monkeypatch.setenv("WORKMATE_ENABLE_WRITE", wartosc)
    assert Settings.from_env().enable_write is True


# --- inwariant: szablon też nie otwiera bramki -------------------------------

_KORZEN = Path(__file__).resolve().parents[1]
# Oba pliki nagłówkiem każą się skopiować do działającej konfiguracji, więc aktywna linia
# w szablonie JEST wartością domyślną wdrożenia — mierzymy je tą samą miarą co kod.
_SZABLONY = (
    _KORZEN / ".env.example",
    _KORZEN / "deploy" / "docker" / "env.example",
)
# Wartości, które parser ``config._bool_from_env`` uznaje za zgodę operatora.
_ZGODA = ("1", "true", "yes", "on")


def _nazwa_wyglada_na_bramke(klucz: str) -> bool:
    """Heurystyka po NAZWIE — szablon to tekst, nie ma z czego wziąć typu pola.

    Konwencja projektu jest jednolita: bramka to ``*_ENABLE_*`` albo ``*_ENABLED``
    (``WORKMATE_ENABLE_WRITE``, ``WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE``,
    ``WORKMATE_TEAMS_DIGEST_ENABLED``). Fałszywe trafienie jest tanie (wystarczy zakomentować
    linię w szablonie), pominięcie byłoby dziurą — dlatego łapiemy szeroko.
    """
    return "_ENABLE" in klucz


def _aktywne_bramki_w_szablonie(sciezka: Path) -> dict[str, str]:
    """Klucz→wartość dla NIESKOMENTOWANYCH linii szablonu, które otwierają bramkę."""
    otwarte: dict[str, str] = {}
    for surowa in sciezka.read_text(encoding="utf-8").splitlines():
        wiersz = surowa.strip()
        if not wiersz or wiersz.startswith("#") or "=" not in wiersz:
            continue
        klucz, _, wartosc = wiersz.partition("=")
        klucz = klucz.strip()
        # Komentarz na końcu linii (szablon floty tak opisuje sekrety) nie jest wartością.
        wartosc = wartosc.split("#", 1)[0].strip().strip("\"'").lower()
        if _nazwa_wyglada_na_bramke(klucz) and wartosc in _ZGODA:
            otwarte[klucz] = wartosc
    return otwarte


@pytest.mark.skipif(
    not (_KORZEN / "deploy" / "docker" / "Dockerfile").is_file(),
    # Pakiet biegnie też WEWNĄTRZ obrazu (etap `test`), a szablony konfiguracji tam nie wjeżdżają —
    # obraz nie wozi `.env.example` ani `deploy/`. Wskaźnikiem „to pełne repozytorium" jest
    # Dockerfile, tak samo jak w `tests/test_version_consistency.py`. Dzięki temu asercja niżej
    # („brak szablonu") zostaje twardym błędem tam, gdzie szablon MA być, zamiast cichnąć wszędzie.
    reason="szablony konfiguracji nie wjeżdżają do obrazu",
)
@pytest.mark.parametrize("szablon", _SZABLONY, ids=lambda p: p.name)
def test_szablon_konfiguracji_nie_otwiera_zadnej_bramki(szablon: Path) -> None:
    """Trzecia droga otwarcia bramki: skopiowany szablon.

    ``golden-test`` wyżej mierzy ``from_env`` i domyślną wartość pola. Szablonu nie mierzył nikt,
    a operator kopiuje go 1:1 — więc aktywna linia ``…ENABLE_MEETING_NOTE_WRITE=true`` włączała
    zapis do bazy wiedzy na świeżej flocie, wbrew runbookowi (`deploy/docker/README.md`), który
    opisuje tę zdolność jako świadomy opt-in z osobnym montażem RW.
    """
    assert szablon.is_file(), f"brak szablonu {szablon}"
    otwarte = _aktywne_bramki_w_szablonie(szablon)
    assert not otwarte, (
        f"{szablon.name} otwiera bramki aktywną linią: {sorted(otwarte)}. Zakomentuj je — "
        "szablon jest kopiowany wprost do konfiguracji, więc aktywna linia to wartość domyślna "
        "wdrożenia (CLAUDE.md, ADR 0006)."
    )


def test_heurystyka_nazwy_bramki_widzi_realne_klucze() -> None:
    """Bramka bez materiału przechodzi zawsze — to sprawdzenie samej heurystyki.

    Gdyby konwencja nazw się zmieniła (albo ktoś przepisał ``_nazwa_wyglada_na_bramke``), test
    wyżej milczałby o pustym zbiorze zamiast zapalić się na otwartej bramce.
    """
    assert _nazwa_wyglada_na_bramke("WORKMATE_ENABLE_WRITE")
    assert _nazwa_wyglada_na_bramke("WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE")
    assert _nazwa_wyglada_na_bramke("WORKMATE_TEAMS_DIGEST_ENABLED")
    assert not _nazwa_wyglada_na_bramke("WORKMATE_EVENTS_DB")
    assert not _nazwa_wyglada_na_bramke("WORKMATE_TEAMS_GRAPH_WATCH")


def test_dotenv_z_repozytorium_nie_otwiera_bramki_pod_pytestem() -> None:
    """Lokalny ``.env`` (bywa w nim ``WORKMATE_ENABLE_WRITE=true``) nie może sterować testami.

    ``config.py`` czyta wyłącznie ``os.environ``; ``.env`` wczytują dopiero wejścia drzwi
    (``env.load_dotenv``). Gdyby kiedyś ktoś przeniósł wczytywanie do ``config.py`` „dla wygody",
    cały pakiet zacząłby mierzyć konfigurację maszyny — ta sonda zapala się od razu.
    """
    dotenv = Path(__file__).resolve().parents[1] / ".env"
    if not dotenv.is_file():
        pytest.skip("brak lokalnego .env — nie ma czego przeciekać")
    wlaczony_zapis = [
        wiersz
        for wiersz in dotenv.read_text(encoding="utf-8", errors="replace").splitlines()
        if wiersz.strip().startswith("WORKMATE_ENABLE_WRITE=")
        and wiersz.split("=", 1)[1].strip().strip("\"'").lower() in ("1", "true", "yes", "on")
    ]
    if not wlaczony_zapis:
        pytest.skip("lokalny .env nie włącza zapisu — sonda nie miałaby czego wykryć")

    assert Settings.from_env().enable_write is False
