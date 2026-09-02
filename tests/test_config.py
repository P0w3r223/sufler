"""Testy wspólnych helperów ``config/_env.py`` — parsowanie env, zapisywalność, ścieżki domyślne.

``require_writable`` to fail-fast dla TRWAŁYCH ścieżek: bez niego domyślne ``~/.workmate`` na
koncie kontenera z ``--no-create-home`` (i rootfs ``read_only``) przyjmuje zapis dopiero „w
próżnię" — poller odkrywa problem w pętli łapiącej wyjątki, jako cichy crash-loop bez watermarku.

Helpery ``_*_from_env`` to WSPÓLNY mechanizm KAŻDEGO ustawienia: przez ``_bool_from_env`` idzie
komplet bramek (patrz ``test_gates_closed_by_default``), przez ``_int_from_env`` sufity pollerów,
przez ``_list_from_env`` listy odbiorców i hostów. Testy poszczególnych klas ustawień sprawdzają
je zawsze pośrednio i tylko dla wartości poprawnych — tu stoi warstwa niżej: co robi wartość
niepoprawna, pusta i otoczona spacjami.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path, PurePosixPath

import pytest

from tests.conftest import przestrzen_config
from workmate import config
from workmate.config import (
    TeamsGraphSettings,
    _bool_from_env,
    _float_from_env,
    _int_from_env,
    _list_from_env,
    _optional_path_from_env,
    _path_from_env,
    _repo_root_or_none,
    require_writable,
)


def test_require_writable_accepts_and_creates_writable_dir(tmp_path):
    """Katalog docelowy nie istnieje ⇒ helper go tworzy i potwierdza zapis; śmieci nie zostają."""
    target = tmp_path / "state" / "github_state.json"

    require_writable(target, "WORKMATE_GITHUB_STATE")

    assert target.parent.is_dir()
    # Plik próbny został sprzątnięty — healthcheck/operator nie znajdzie śmiecia.
    assert list(target.parent.glob(".workmate-writetest-*")) == []


def test_require_writable_raises_when_ancestor_is_a_file(tmp_path):
    """Niezapisywalność wymuszamy PLIKIEM w roli katalogu, nie ``chmod``.

    Etap ``test`` obrazu biegnie z uprawnieniami roota, a root omija bity uprawnień — ``chmod
    000`` nie zablokowałby zapisu. ``mkdir(parents=True)`` pod plikiem rzuca ``NotADirectoryError``
    (``OSError``) niezależnie od uid, więc test jest wiarygodny także w obrazie.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="ascii")
    target = blocker / "sub" / "state.json"

    # Komunikat MUSI wskazywać zmienną do nadpisania — operator ma wiedzieć, co ustawić.
    with pytest.raises(ValueError, match="WORKMATE_GITHUB_STATE"):
        require_writable(target, "WORKMATE_GITHUB_STATE")


def test_require_writable_directory_probes_the_directory_itself(tmp_path):
    """``is_directory=True`` sonduje WSKAZANY katalog, nie jego rodzica (ADR 0008).

    Różnicę widać po tym, co powstaje: baza wiedzy bywa osobnym wolumenem wewnątrz zapisywalnego
    ``data/``, więc sonda o poziom wyżej potwierdzałaby zapis do katalogu, którego zapis nie
    dotyczy — i przepuszczała montaż read-only.
    """
    notes = tmp_path / "data" / "notes"

    require_writable(notes, "WORKMATE_NOTES_DIR", is_directory=True)

    assert notes.is_dir()
    assert list(notes.glob(".workmate-writetest-*")) == []


def test_require_writable_file_mode_stops_at_parent(tmp_path):
    """Domyślne (plikowe) wywołanie NIE tworzy katalogu o nazwie pliku — kontrast dla powyższego."""
    target = tmp_path / "state" / "github_state.json"

    require_writable(target, "WORKMATE_GITHUB_STATE")

    assert target.parent.is_dir()
    assert not target.exists(), "ścieżka pliku nie może stać się katalogiem"


def test_require_writable_directory_raises_when_path_is_a_file(tmp_path):
    """Katalog notatek wskazany na PLIK = błąd startu, nie cicha zgoda.

    Niezapisywalność wymuszamy plikiem w roli katalogu, nie ``chmod`` — etap ``test`` obrazu
    biegnie jako root, a root omija bity uprawnień (jak w teście wyżej).
    """
    blocker = tmp_path / "notes"
    blocker.write_text("x", encoding="ascii")

    with pytest.raises(ValueError, match="WORKMATE_NOTES_DIR"):
        require_writable(blocker, "WORKMATE_NOTES_DIR", is_directory=True)


# --- domyślne ścieżki stanu: absolutne i POZA repozytorium ------------------
#
# Poprzednia wersja tego pliku porównywała ``_DEFAULT_TOKENS_FILE`` z literałem powtórzonym
# z ``config/server.py`` (``if os.name == "nt": assert == Path("C:/ProgramData/...")``) — czyli
# przepisywała implementację i przechodziła także wtedy, gdy zmiana literału była błędem.
# Testujemy WŁASNOŚĆ, którą opisuje komentarz w kodzie: ścieżka ma być absolutna (windowsowe
# „C:/…" na Linuksie stawało się katalogiem WZGLĘDNYM pod CWD) i ma leżeć poza bazą wiedzy.

# Nazwy z CAŁEGO pakietu ``workmate.config``, nie tylko z re-eksportu w ``__init__`` — inaczej
# bramka odkrywania niżej chodziłaby po pustym zbiorze i cichła po każdym nowym module domeny.
_KONFIG = przestrzen_config()

# Trwały stan i sekrety, których domyślna ścieżka musi działać na TEJ platformie.
_TRWALE_SCIEZKI = (
    "_DEFAULT_TOKENS_FILE",
    "_DEFAULT_CONVERSATIONS_DB",
    "_DEFAULT_EVENTS_DB",
    "_DEFAULT_RETRIEVAL_INDEX",
    "_DEFAULT_TEAMS_GRAPH_CACHE",
    "_DEFAULT_TEAMS_GRAPH_STATE",
    "_DEFAULT_GITHUB_STATE",
    "_DEFAULT_TEAMS_DIGEST_STATE",
    "_DEFAULT_WORKSPACE_DIR",
    # Cudzy cache MSAL grafiku (ADR 0059). Ta sama klasa błędu co dawny ``_DEFAULT_TOKENS_FILE``:
    # literał POSIX na Windows nie jest absolutny (brak dysku), a ``ScheduleSettings.is_enabled()``
    # w trybie "auto" sonduje ten plik ``is_file()`` na KAŻDEJ platformie.
    "_DEFAULT_SCHEDULE_CACHE",
)

# Ścieżki ŚWIADOMIE zwolnione z kontroli „absolutna na TEJ platformie": gniazda uniksowe i punkty
# montażu kontenera-wykonawcy (ADR 0057 / infra 0012). Zdolność powłoki nie działa na Windows
# w ogóle — nie ma ani AF_UNIX w tym układzie, ani ``docker.sock`` — więc windowsowy wariant byłby
# fikcją wskazującą na nic. Rejestr jest JAWNY, żeby nowa ścieżka nie mogła się prześlizgnąć jako
# „pewnie też linuksowa".
_SCIEZKI_TYLKO_LINUX = (
    "_DEFAULT_MANAGER_SOCKET",
    "_DEFAULT_DOCKER_SOCKET",
    "_DEFAULT_SCRATCHPAD_ROOT",
    "_DEFAULT_EXEC_SOCK_ROOT",
)


@pytest.mark.parametrize("nazwa", _TRWALE_SCIEZKI)
def test_domyslna_sciezka_stanu_jest_absolutna(nazwa: str):
    """Ścieżka względna zaczepiłaby stan o katalog roboczy procesu — inny dla każdych drzwi."""
    sciezka: Path = _KONFIG[nazwa]
    assert sciezka.is_absolute(), f"{nazwa}={sciezka} jest względna wobec CWD"


@pytest.mark.parametrize("nazwa", _TRWALE_SCIEZKI)
def test_domyslna_sciezka_stanu_lezy_poza_repozytorium(nazwa: str):
    """Sekrety i dane operacyjne NIGDY w repo ani w ``data/`` (ADR 0007/0010/0019, CLAUDE.md).

    Domyślna w repo znaczyłaby: token HTTP albo baza rozmów w katalogu, który agent czyta
    zachłannie i który wjeżdża do obrazu.
    """
    korzen = _repo_root_or_none(Path(__file__).resolve())
    assert korzen is not None, "test biegnie w drzewie repozytorium"
    sciezka: Path = _KONFIG[nazwa]

    assert korzen.resolve() not in sciezka.resolve().parents, (
        f"{nazwa}={sciezka} domyślnie wewnątrz repozytorium {korzen}"
    )


# --- parsowanie env: fail-closed i przewidywalne -----------------------------


@pytest.mark.parametrize("wartosc", ["true", "TRUE", " True ", "1", "yes", "YES", "on"])
def test_bool_from_env_uznaje_zgode_w_kazdej_pisowni(monkeypatch, wartosc: str):
    monkeypatch.setenv("WORKMATE_PROBA", wartosc)
    assert _bool_from_env("WORKMATE_PROBA", default=False) is True


@pytest.mark.parametrize("wartosc", ["false", "0", "no", "off", "", "  ", "prawda", "tak", "yes!"])
def test_bool_from_env_traktuje_wszystko_inne_jako_brak_zgody(monkeypatch, wartosc: str):
    """Wartość niezrozumiana NIE otwiera bramki — to fundament wszystkich domyślnych „OFF"."""
    monkeypatch.setenv("WORKMATE_PROBA", wartosc)
    assert _bool_from_env("WORKMATE_PROBA", default=False) is False


def test_bool_from_env_bez_zmiennej_oddaje_domyslna(monkeypatch):
    """Brak zmiennej ≠ „false": domyślna ``True`` (np. kompaktowanie) musi przetrwać."""
    monkeypatch.delenv("WORKMATE_PROBA", raising=False)
    assert _bool_from_env("WORKMATE_PROBA", default=True) is True
    assert _bool_from_env("WORKMATE_PROBA", default=False) is False


def test_bool_from_env_pusta_wartosc_wylacza_mimo_domyslnej_wlaczonej(monkeypatch):
    """``WORKMATE_X=`` w ``.env`` to WARTOŚĆ pusta, nie brak zmiennej — i wygrywa z domyślną.

    Rozróżnienie jest realne: skomentowanie linii w ``.env`` daje brak zmiennej, wyczyszczenie
    jej wartości daje pusty napis. Drugie wyłącza zdolność domyślnie włączoną.
    """
    monkeypatch.setenv("WORKMATE_PROBA", "")
    assert _bool_from_env("WORKMATE_PROBA", default=True) is False


def test_int_from_env_odrzuca_wartosc_nieliczbowa_z_nazwa_zmiennej(monkeypatch):
    """Komunikat MUSI nieść nazwę zmiennej — operator ma wiedzieć, którą linię ``.env`` poprawić."""
    monkeypatch.setenv("WORKMATE_PROBA", "dużo")
    with pytest.raises(ValueError, match="WORKMATE_PROBA"):
        _int_from_env("WORKMATE_PROBA", 7)


@pytest.mark.parametrize("wartosc", ["", "   "])
def test_int_from_env_pusta_wartosc_oddaje_domyslna(monkeypatch, wartosc: str):
    """Pusta zmienna z ``.env`` (``WORKMATE_X=``) nie może wywrócić startu ani dać zera."""
    monkeypatch.setenv("WORKMATE_PROBA", wartosc)
    assert _int_from_env("WORKMATE_PROBA", 7) == 7


def test_int_from_env_czyta_wartosc_ujemna(monkeypatch):
    """Helper NIE waliduje zakresu — od tego są ``validate`` klas ustawień (osobna warstwa)."""
    monkeypatch.setenv("WORKMATE_PROBA", "-5")
    assert _int_from_env("WORKMATE_PROBA", 7) == -5


def test_float_from_env_odrzuca_wartosc_nieliczbowa(monkeypatch):
    monkeypatch.setenv("WORKMATE_PROBA", "8,5")  # przecinek dziesiętny — realna polska literówka
    with pytest.raises(ValueError, match="WORKMATE_PROBA"):
        _float_from_env("WORKMATE_PROBA", 8.0)


def test_list_from_env_tnie_przycina_i_odrzuca_puste(monkeypatch):
    monkeypatch.setenv("WORKMATE_PROBA", " a , b ,, c ")
    assert _list_from_env("WORKMATE_PROBA", ("x",)) == ("a", "b", "c")


@pytest.mark.parametrize("wartosc", ["", " , , "])
def test_list_from_env_lista_bez_elementow_oddaje_domyslna(monkeypatch, wartosc: str):
    """Pusta lista cofa do domyślnej — inaczej ``WORKMATE_ALLOWED_HOSTS=`` zdejmowałoby po cichu
    ochronę przed DNS-rebinding, zamiast ją zostawić na loopbacku."""
    monkeypatch.setenv("WORKMATE_PROBA", wartosc)
    assert _list_from_env("WORKMATE_PROBA", ("127.0.0.1:*",)) == ("127.0.0.1:*",)


def test_path_from_env_rozwija_tylde(monkeypatch):
    monkeypatch.setenv("WORKMATE_PROBA", "~/stan/events.db")
    wynik = _path_from_env("WORKMATE_PROBA", Path("/domyslna"))
    assert "~" not in str(wynik)
    assert wynik.name == "events.db"


def test_path_from_env_pusta_wartosc_oddaje_domyslna(monkeypatch):
    """``WORKMATE_X=`` nie może dać ``Path('')`` — to katalog bieżący, czyli cichy zapis do CWD."""
    monkeypatch.setenv("WORKMATE_PROBA", "")
    assert _path_from_env("WORKMATE_PROBA", Path("/domyslna")) == Path("/domyslna")


def test_nieobslugiwany_transport_wywala_start_z_lista_dozwolonych(monkeypatch):
    """Literówka w ``WORKMATE_TRANSPORT`` ma zatrzymać START, nie wypłynąć przy pierwszym żądaniu.

    To jedyne pole ``Settings``, które ``from_env`` waliduje samo (reszta idzie do ``validate``
    klas per zdolność) — i jedyne, którego zła wartość decyduje, CZY drzwi są sieciowe.
    """
    monkeypatch.setenv("WORKMATE_TRANSPORT", "https")

    with pytest.raises(ValueError, match="stdio.*streamable-http"):
        config.Settings.from_env()


def test_optional_path_from_env_bez_zmiennej_daje_none(monkeypatch):
    """``None`` to zdolność WYŁĄCZONA (metryki, audyt) — nie wolno jej podmienić na ścieżkę."""
    monkeypatch.delenv("WORKMATE_PROBA", raising=False)
    assert _optional_path_from_env("WORKMATE_PROBA") is None
    monkeypatch.setenv("WORKMATE_PROBA", "")
    assert _optional_path_from_env("WORKMATE_PROBA") is None


# --- każda domyślna ścieżka jest ALBO sprawdzana, ALBO jawnie zwolniona ------


def test_kazda_domyslna_sciezka_stoi_w_jednym_z_dwoch_rejestrow():
    """Bramka odkrywania: nowa stała ``_DEFAULT_*`` nie może się wymknąć obu listom wyżej.

    Sonda „ścieżka absolutna" chodzi po ręcznej krotce, więc kolejna stała dopisana obok — jak
    swego czasu ``_DEFAULT_SCHEDULE_CACHE`` — po prostu nie była oglądana. Tu wymuszamy decyzję:
    albo ścieżka wchodzi pod kontrolę, albo trafia do rejestru zwolnień z uzasadnieniem.
    """
    wszystkie = {
        nazwa
        for nazwa, wartosc in _KONFIG.items()
        if nazwa.startswith("_DEFAULT_") and isinstance(wartosc, Path)
    }
    nieobjete = wszystkie - set(_TRWALE_SCIEZKI) - set(_SCIEZKI_TYLKO_LINUX)
    assert not nieobjete, (
        f"domyślne ścieżki poza oboma rejestrami: {sorted(nieobjete)}. Dopisz je do "
        "_TRWALE_SCIEZKI (kontrola) albo do _SCIEZKI_TYLKO_LINUX (zwolnienie z uzasadnieniem)."
    )


@pytest.mark.parametrize("nazwa", _SCIEZKI_TYLKO_LINUX)
def test_sciezka_tylko_linuksowa_jest_absolutna_po_posixowemu(nazwa: str):
    """Zwolnienie dotyczy PLATFORMY, nie poprawności — literał nadal musi być absolutny POSIX-owo.

    Względne ``var/run/...`` zaczepiłoby gniazdo o katalog roboczy procesu także na flocie.
    Miarą jest ``PurePosixPath``, bo na Windows ``Path.is_absolute()`` na tych literałach mówi
    ``False`` (brak dysku) — i to jest właśnie powód, dla którego stoją w rejestrze zwolnień.
    """
    sciezka: Path = _KONFIG[nazwa]
    assert PurePosixPath(sciezka.as_posix()).is_absolute(), f"{nazwa}={sciezka} nie jest absolutna"


# --- Settings.validate(): jedyna klasa ustawień, która go nie miała ----------


def test_settings_validate_odrzuca_nieznany_poziom_logowania(monkeypatch):
    """``WORKMATE_LOG_LEVEL=verbose`` wywracał start komunikatem, który nie nazywał zmiennej.

    ``logging.basicConfig`` mówi „Unknown level: 'VERBOSE'", uvicorn swoje — obie wiadomości
    zostawiają operatora bez informacji, KTÓRĄ linię ``.env`` poprawić. Sonda pilnuje, że nazwa
    zmiennej jest w komunikacie.
    """
    monkeypatch.setenv("WORKMATE_LOG_LEVEL", "verbose")
    ustawienia = config.Settings.from_env()

    with pytest.raises(ValueError, match="WORKMATE_LOG_LEVEL"):
        ustawienia.validate()


@pytest.mark.parametrize("poziom", ["INFO", "info", "  Debug  ", "WARNING", "ERROR", "CRITICAL"])
def test_settings_validate_przepuszcza_poziomy_kanoniczne(monkeypatch, poziom):
    """Kontrast: kontrola nie może być tak ciasna, żeby odrzucała realną konfigurację."""
    monkeypatch.setenv("WORKMATE_LOG_LEVEL", poziom)
    config.Settings.from_env().validate()


@pytest.mark.parametrize("alias", ["WARN", "warn", "FATAL", "NOTSET"])
def test_settings_validate_przepuszcza_aliasy_znane_bibliotece_logging(monkeypatch, alias):
    """Kontrola miała ZAMIENIĆ niejasny błąd biblioteki, a nie zawęzić zbioru wejść.

    ``logging.getLevelName`` zna ``WARN``, ``FATAL`` i ``NOTSET`` — instalacja z
    ``WORKMATE_LOG_LEVEL=WARN`` wstawała przed dołożeniem ``validate`` i musi wstawać dalej.
    Odrzucanie ich byłoby regresem wprowadzonym przez bramkę, która miała pomagać.
    """
    monkeypatch.setenv("WORKMATE_LOG_LEVEL", alias)
    config.Settings.from_env().validate()


@pytest.mark.parametrize(
    ("wejscie", "oczekiwany"),
    [
        ("WARN", "warning"),
        ("FATAL", "critical"),
        ("NOTSET", "trace"),
        ("  Info  ", "info"),
        ("DEBUG", "debug"),
    ],
)
def test_uvicorn_log_level_tlumaczy_aliasy_na_slownik_uvicorna(monkeypatch, wejscie, oczekiwany):
    """``uvicorn`` ma WŁASNY, węższy słownik nazw — alias ``logging`` to tam ``KeyError``.

    Bez tłumaczenia poszerzenie kontroli przeniosłoby tylko awarię: zamiast odmowy startu
    z czytelnym komunikatem, drzwi HTTP wywracałyby się w środku ``uvicorn.Config``.
    """
    monkeypatch.setenv("WORKMATE_LOG_LEVEL", wejscie)
    assert config.Settings.from_env().uvicorn_log_level == oczekiwany


def test_uvicorn_zna_kazdy_poziom_ktory_przepuszcza_walidacja(monkeypatch):
    """Sonda kontraktu z BIBLIOTEKĄ, nie z naszą mapą: pytamy realny słownik ``uvicorn``.

    Ręczna mapa aliasów rozjedzie się cicho, jeśli ``uvicorn`` przemianuje albo usunie poziom —
    a wtedy wywali się dopiero start drzwi HTTP na produkcji.
    """
    from uvicorn.config import LOG_LEVELS

    for poziom in config._ALLOWED_LOG_LEVELS:
        monkeypatch.setenv("WORKMATE_LOG_LEVEL", poziom)
        przetlumaczony = config.Settings.from_env().uvicorn_log_level
        assert przetlumaczony in LOG_LEVELS, (
            f"WORKMATE_LOG_LEVEL={poziom} przechodzi walidację, ale uvicorn nie zna "
            f"{przetlumaczony!r} — drzwi HTTP padłyby przy starcie."
        )


def test_settings_validate_odrzuca_port_poza_zakresem(monkeypatch):
    """Port 0/70000 to literówka operatora — ma zatrzymać start, nie ``uvicorn`` w locie."""
    monkeypatch.setenv("WORKMATE_BIND_PORT", "70000")

    with pytest.raises(ValueError, match="WORKMATE_BIND_PORT"):
        config.Settings.from_env().validate()


def test_settings_validate_lapie_zly_transport_takze_po_replace(monkeypatch):
    """``from_env`` waliduje transport przy budowie, ale wiring składa też przez ``replace``."""
    ustawienia = dataclasses.replace(config.Settings.from_env(), transport="https")

    with pytest.raises(ValueError, match="WORKMATE_TRANSPORT"):
        ustawienia.validate()


# --- trwałe ścieżki Settings: komplet do kontroli zapisywalności -------------


def test_persistent_paths_obejmuje_kazda_trwala_sciezke_settings(monkeypatch, tmp_path):
    """Migawki, metryki i audyt wypadły z kontroli ``require_writable`` — każde osobno i po cichu.

    Wszystkie trzy domyślnie lądują pod montażem read-only floty, a rejestrator audytu łyka błędy
    per wywołanie: operator miał „dziennik" z zerem wierszy zamiast błędu startu. Ta sonda liczy
    POLA, a nie wymienia ich z palca — nowa trwała ścieżka w ``Settings`` musi wejść na listę albo
    zapalić ten test.
    """
    monkeypatch.setenv("WORKMATE_METRICS_DB", str(tmp_path / "metrics.db"))
    monkeypatch.setenv("WORKMATE_AUDIT_DB", str(tmp_path / "audit.db"))
    ustawienia = config.Settings.from_env()

    zmienne = {env_var for _, env_var, _ in ustawienia.persistent_paths()}

    assert zmienne == {
        "WORKMATE_NOTE_SNAPSHOTS_DIR",
        "WORKMATE_METRICS_DB",
        "WORKMATE_AUDIT_DB",
    }
    # Migawki to KATALOG docelowy (jak baza wiedzy), nie plik w katalogu — inaczej sonda badałaby
    # poziom wyżej i przepuszczała ``data/snapshots`` zamontowane read-only wewnątrz ``data/``.
    katalogi = {env_var for _, env_var, is_dir in ustawienia.persistent_paths() if is_dir}
    assert katalogi == {"WORKMATE_NOTE_SNAPSHOTS_DIR"}


def test_persistent_paths_pomija_zdolnosci_wylaczone():
    """``None`` = zdolność wyłączona; nie ma czego sprawdzać, więc nie tworzymy jej katalogu."""
    ustawienia = config.Settings.from_env()
    assert ustawienia.metrics_db is None and ustawienia.audit_db is None
    assert {env_var for _, env_var, _ in ustawienia.persistent_paths()} == {
        "WORKMATE_NOTE_SNAPSHOTS_DIR"
    }


def test_persistent_paths_wskazuja_realnie_sprawdzalne_sciezki(monkeypatch, tmp_path):
    """Kontrakt seamu: każdy zwrócony wpis ma przejść przez ``require_writable`` bez tłumaczenia."""
    monkeypatch.setenv("WORKMATE_NOTE_SNAPSHOTS_DIR", str(tmp_path / "snap"))
    monkeypatch.setenv("WORKMATE_AUDIT_DB", str(tmp_path / "audyt" / "audit.db"))

    for sciezka, env_var, is_dir in config.Settings.from_env().persistent_paths():
        require_writable(sciezka, env_var, is_directory=is_dir)

    assert (tmp_path / "snap").is_dir()
    assert (tmp_path / "audyt").is_dir()


# --- bramki ADR 0064/0065: walidacja krzyżowa jak u sąsiadów -----------------
#
# Reszta kontroli ``TeamsGraphSettings.validate`` stoi w
# ``tests/adapters/inbound/teams_graph/test_settings.py``; te trzy dopisano przy przeglądzie
# kompozycji 2026-08-17, gdy okazało się, że jako jedyne bramki tej klasy wypadły ze wzorca.


def _teams_graph_z_mapa(tmp_path, **pola):
    mapa = tmp_path / "identities.yaml"
    mapa.write_text("czlonkowie: []\n", encoding="utf-8")
    return TeamsGraphSettings(client_id="c", tenant_id="t", meeting_note_identities=mapa, **pola)


def test_mutacja_notatek_bez_mapy_tozsamosci_wywala_start(tmp_path):
    """Jedynym sygnałem był ``logger.error`` w wiringu — bramka wyglądała na włączoną i milczała."""
    ustawienia = TeamsGraphSettings(
        client_id="c",
        tenant_id="t",
        enable_file_tool=True,
        enable_note_mutation=True,
        meeting_note_identities=tmp_path / "nie-ma.yaml",
    )

    with pytest.raises(ValueError, match="WORKMATE_TEAMS_GRAPH_IDENTITIES"):
        ustawienia.validate()


def test_kasowanie_notatek_bez_mutacji_wywala_start(tmp_path):
    """Flaga bez efektu: kasowanie jedzie tą samą ścieżką (``allow_delete``) co edycja."""
    ustawienia = _teams_graph_z_mapa(tmp_path, enable_note_delete=True)

    with pytest.raises(ValueError, match="ENABLE_NOTE_MUTATION"):
        ustawienia.validate()


def test_mutacja_bez_narzedzia_plikowego_wywala_start(tmp_path):
    """``File(edit)`` to akcja narzędzia ``File`` — bez ``ENABLE_FILE_TOOL`` go nie ma."""
    ustawienia = _teams_graph_z_mapa(tmp_path, enable_note_mutation=True)

    with pytest.raises(ValueError, match="ENABLE_FILE_TOOL"):
        ustawienia.validate()


def test_komplet_bramek_mutacji_przechodzi_walidacje(tmp_path):
    """Kontrast: poprawnie złożona konfiguracja MUSI przejść — inaczej bramka byłaby martwa."""
    _teams_graph_z_mapa(
        tmp_path,
        enable_file_tool=True,
        enable_note_mutation=True,
        enable_note_delete=True,
    ).validate()


# --- Granice zużycia wykonawcy (ADR infra 0013) -----------------------------------------


def _menedzer(**kwargs):
    """Menedżer z kompletem stałego szablonu — zostaje sam sprawdzany parametr."""
    from workmate.config import ExecManagerSettings

    return ExecManagerSettings(
        image="workmate:1.11.0-deploy",
        scratchpad_volume="workmate_workmate-scratchpad",
        sock_volume="workmate_workmate-exec-sock",
        data_volume="workmate_workmate-data",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("pole", "zmienna"),
    [
        ("exec_memory_mb", "WORKMATE_EXEC_MEMORY_MB"),
        ("exec_pids_limit", "WORKMATE_EXEC_PIDS_LIMIT"),
        ("exec_max_file_mb", "WORKMATE_EXEC_MAX_FILE_MB"),
        ("exec_max_open_files", "WORKMATE_EXEC_MAX_OPEN_FILES"),
    ],
)
def test_zerowa_granica_zuzycia_wywala_start_menedzera(pole, zmienna):
    """Zero znaczy dla Docker API „BEZ LIMITU", nie „limit zero".

    Bez tej walidacji literówka w compose zdejmowałaby granicę po cichu, zostawiając wykonawcę
    wyglądającego na utwardzony — a menedżer jest ostatnim miejscem, gdzie ta liczba jest jeszcze
    konfiguracją, a nie faktem o kontenerze.
    """
    with pytest.raises(ValueError, match=zmienna):
        _menedzer(**{pole: 0}).validate()


@pytest.mark.parametrize(
    ("pole", "zmienna", "za_duzo"),
    [
        ("exec_memory_mb", "WORKMATE_EXEC_MEMORY_MB", 99_999),
        ("exec_pids_limit", "WORKMATE_EXEC_PIDS_LIMIT", 99_999),
        ("exec_max_file_mb", "WORKMATE_EXEC_MAX_FILE_MB", 99_999),
        ("exec_max_open_files", "WORKMATE_EXEC_MAX_OPEN_FILES", 9_999_999),
    ],
)
def test_absurdalna_granica_zuzycia_tez_wywala_start(pole, zmienna, za_duzo):
    """Sama podłoga to za mało: literówka w drugą stronę daje granicę, która niczego nie ogranicza,
    a wygląda w compose dokładnie tak samo jak działająca."""
    with pytest.raises(ValueError, match=zmienna):
        _menedzer(**{pole: za_duzo}).validate()


def test_granica_ponizej_minimum_dockera_wywala_start():
    """1 MB pamięci przechodzi „>= 1", a Docker odbija `create` (minimum 6 MB) — awaria byłaby
    głośna, ale późna i w INNYM PROCESIE niż literówka, więc operator szukałby jej nie tam."""
    with pytest.raises(ValueError, match="WORKMATE_EXEC_MEMORY_MB"):
        _menedzer(exec_memory_mb=1).validate()


@pytest.mark.parametrize("wartosc", [0, 0.001, 64])
def test_limit_cpu_poza_zakresem_wywala_start(wartosc):
    with pytest.raises(ValueError, match="WORKMATE_EXEC_CPU_LIMIT"):
        _menedzer(exec_cpu_limit=wartosc).validate()


def test_domyslne_granice_zuzycia_przechodza_walidacje():
    """Kontrast: komplet domyślny MUSI przejść — inaczej wykonawca nie wstaje bez zmiennych,
    a podbicie paczki bez podbicia compose zostawiałoby rozmowy bez powłoki."""
    _menedzer().validate()
