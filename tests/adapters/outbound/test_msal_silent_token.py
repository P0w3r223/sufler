"""Testy cichego tokenu z CUDZEGO cache MSAL (``build_silent_token_provider``, ADR 0059).

Prawdziwy jest tu ``msal.SerializableTokenCache`` — to jemu wręczamy ODCZYTANY tekst pliku, więc
tylko z prawdziwym cache test o kodowaniu cokolwiek znaczy. Atrapą jest wyłącznie
``msal.PublicClientApplication``: jego konstruktor robi OIDC discovery po SIECI (msal 1.37 wywraca
się ``ValueError: Unable to get authority configuration``), a pakiet ma biegać bez sieci.

Moduł produkcyjny importuje ``msal`` LENIWIE, wewnątrz ``get_token``, więc podmiana atrybutu na
module ``msal`` wystarczy — nie ma zamrożonej referencji z czasu importu.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import msal
import pytest

from sufler.adapters.outbound.msal_silent_token import build_silent_token_provider
from sufler.config import ScheduleSettings
from sufler.core.errors import ScheduleReadError

# Cache bota powiadomienia-teams w miniaturze: jedno konto, tyle ile czyta ``get_accounts``.
_KONTO_KLUCZ = "uid.utid-login.microsoftonline.com-contoso.onmicrosoft.com"
# Nazwa konta ze znakami spoza ASCII — realna dla polskiego najemcy i jedyny nośnik, po którym
# widać, czy plik zdekodowano tym kodowaniem, co trzeba.
_UZYTKOWNIK = "zażółć.gęślą@example.com"


def _dokument_cache(username: str = _UZYTKOWNIK) -> dict[str, Any]:
    return {
        "Account": {
            _KONTO_KLUCZ: {
                "home_account_id": "uid.utid",
                "environment": "login.microsoftonline.com",
                "realm": "contoso.onmicrosoft.com",
                "local_account_id": "uid",
                "username": username,
                "authority_type": "MSSTS",
            }
        }
    }


def _zapisz_cache(path: Path, *, username: str = _UZYTKOWNIK, ensure_ascii: bool = True) -> None:
    """Połóż plik cache jako UTF-8 — tak samo, jak robi to bot (``graph/auth.py``)."""
    path.write_text(
        json.dumps(_dokument_cache(username), ensure_ascii=ensure_ascii), encoding="utf-8"
    )


def _ustawienia(path: Path) -> ScheduleSettings:
    return ScheduleSettings(
        client_id="cid",
        tenant_id="tid",
        token_cache_path=path,
        scopes=("Schedule.Read.All", "TeamMember.Read.All"),
    )


class _AtrapaAplikacji:
    """Atrapa ``msal.PublicClientApplication`` — bez sieci, ale nad PRAWDZIWYM cache.

    ``get_accounts`` czyta konta z cache tak, jak robi to msal, więc nazwa konta przechodzi całą
    drogę: bajty pliku → dekodowanie → ``deserialize`` → konto oddane wywołaniu cichego logowania.
    """

    ostatnia: _AtrapaAplikacji | None = None

    def __init__(
        self,
        client_id: str,
        authority: str | None = None,
        token_cache: msal.SerializableTokenCache | None = None,
    ) -> None:
        self.client_id = client_id
        self.authority = authority
        self.token_cache = token_cache
        self.wywolania: list[tuple[list[str], dict[str, Any]]] = []
        self.wynik: dict[str, Any] | None = {"access_token": "tok-123"}
        _AtrapaAplikacji.ostatnia = self

    def get_accounts(self) -> list[dict[str, Any]]:
        assert self.token_cache is not None
        return list(self.token_cache.search(msal.TokenCache.CredentialType.ACCOUNT))

    def acquire_token_silent(
        self, scopes: list[str], account: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.wywolania.append((scopes, account or {}))
        # Prawdziwy msal po odświeżeniu tokenu ZNACZY cache jako zmieniony; utrwalenie tej zmiany
        # to decyzja wołającego — a nasza brzmi „nigdy" (cudzy plik, mont RO).
        assert self.token_cache is not None
        self.token_cache.has_state_changed = True
        return self.wynik


@pytest.fixture
def atrapa_msal(monkeypatch: pytest.MonkeyPatch) -> type[_AtrapaAplikacji]:
    monkeypatch.setattr(msal, "PublicClientApplication", _AtrapaAplikacji)
    _AtrapaAplikacji.ostatnia = None
    return _AtrapaAplikacji


def test_oddaje_token_i_pyta_o_zakresy_z_konfiguracji(
    tmp_path: Path, atrapa_msal: type[_AtrapaAplikacji]
) -> None:
    cache_path = tmp_path / "teams_token_cache.bin"
    _zapisz_cache(cache_path)
    settings = _ustawienia(cache_path)

    assert build_silent_token_provider(settings)() == "tok-123"

    app = atrapa_msal.ostatnia
    assert app is not None
    assert (app.client_id, app.authority) == ("cid", "https://login.microsoftonline.com/tid")
    scopes, account = app.wywolania[0]
    assert scopes == ["Schedule.Read.All", "TeamMember.Read.All"]
    assert account["home_account_id"] == "uid.utid"


def test_cache_czytany_jest_jako_utf8_niezaleznie_od_kodowania_platformy(
    tmp_path: Path, atrapa_msal: type[_AtrapaAplikacji], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nazwa konta ze znakami spoza ASCII wraca w CAŁOŚCI, choć domyślne kodowanie to cp1250.

    Kodowania domyślnego NIE da się ustawić z Pythona: od 3.11 ``TextIOWrapper`` rozwiązuje
    ``encoding="locale"`` w C (``GetACP`` na Windows, ``nl_langinfo`` na POSIX), więc podmiana
    ``locale.getpreferredencoding`` nic nie zmienia, a bez podmiany wynik zależałby od maszyny —
    na ubuntu w CI locale jest UTF-8 i test przepuściłby regres, na Windows by go złapał. Test
    zależny od locale maszyny nie jest testem, więc zamiast zgadywać wstawiamy SZEW: ``read_text``
    bez ``encoding`` dekoduje cp1250, czyli dokładnie to, co robi polski Windows.

    Bez ``encoding="utf-8"`` w produkcji ten sam plik wraca z rozsypaną nazwą konta (albo w ogóle
    nie wraca, bo cp1250 ma bajty niezdefiniowane) — i nie ma wyjątku, jest cicha podmiana znaków.
    """
    prawdziwy_read_text = Path.read_text

    def read_text_z_kodowaniem_platformy(
        self: Path, encoding: str | None = None, **kwargs: Any
    ) -> str:
        return prawdziwy_read_text(self, encoding or "cp1250", **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text_z_kodowaniem_platformy)

    cache_path = tmp_path / "teams_token_cache.bin"
    # ``ensure_ascii=False``: bot pisze cache przez ``json.dumps`` msala (znaki uciekane do \\uXXXX,
    # więc czysty ASCII), ale plik jest CUDZY i format tego nie gwarantuje — JSON jest z definicji
    # UTF-8 i inny producent cache (albo inna wersja msala) może napisać znaki wprost.
    _zapisz_cache(cache_path, ensure_ascii=False)

    assert build_silent_token_provider(_ustawienia(cache_path))() == "tok-123"

    app = atrapa_msal.ostatnia
    assert app is not None
    _, account = app.wywolania[0]
    assert account["username"] == _UZYTKOWNIK


def test_brak_pliku_cache_daje_czytelna_odmowe(
    tmp_path: Path, atrapa_msal: type[_AtrapaAplikacji]
) -> None:
    cache_path = tmp_path / "nie-ma-montu.bin"
    settings = _ustawienia(cache_path)

    with pytest.raises(ScheduleReadError) as exc:
        build_silent_token_provider(settings)()

    assert str(cache_path) in str(exc.value)
    assert "powiadomienia-teams" in str(exc.value)
    # Przyczyna zostaje w ``__cause__`` (log), a nie w treści dla człowieka.
    assert isinstance(exc.value.__cause__, OSError)
    assert atrapa_msal.ostatnia is None


def test_uszkodzony_cache_nie_wycieka_wyjatkiem_json(
    tmp_path: Path, atrapa_msal: type[_AtrapaAplikacji]
) -> None:
    cache_path = tmp_path / "teams_token_cache.bin"
    cache_path.write_text("{ to nie jest json", encoding="utf-8")

    with pytest.raises(ScheduleReadError) as exc:
        build_silent_token_provider(_ustawienia(cache_path))()

    assert isinstance(exc.value.__cause__, ValueError)
    assert atrapa_msal.ostatnia is None


def test_cudzy_cache_bez_naszego_konta_konczy_sie_odmowa(
    tmp_path: Path, atrapa_msal: type[_AtrapaAplikacji]
) -> None:
    """Cache jest, ale nie ma w nim konta (bot się wylogował) — odmowa, NIE device-code."""
    cache_path = tmp_path / "teams_token_cache.bin"
    cache_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ScheduleReadError) as exc:
        build_silent_token_provider(_ustawienia(cache_path))()

    assert "Schedule.Read.All" in str(exc.value)
    app = atrapa_msal.ostatnia
    assert app is not None
    assert app.wywolania == []  # bez konta nie pytamy o token


@pytest.mark.parametrize(
    "wynik",
    [None, {}, {"error": "invalid_grant", "error_description": "sesja wygasła"}],
    ids=["brak-wyniku", "pusty-wynik", "wygasly-refresh-token"],
)
def test_ciche_logowanie_bez_tokenu_konczy_sie_odmowa(
    tmp_path: Path,
    atrapa_msal: type[_AtrapaAplikacji],
    monkeypatch: pytest.MonkeyPatch,
    wynik: dict[str, Any] | None,
) -> None:
    cache_path = tmp_path / "teams_token_cache.bin"
    _zapisz_cache(cache_path)

    class _Wygasla(_AtrapaAplikacji):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.wynik = wynik

    monkeypatch.setattr(msal, "PublicClientApplication", _Wygasla)

    with pytest.raises(ScheduleReadError) as exc:
        build_silent_token_provider(_ustawienia(cache_path))()

    assert exc.value.__cause__ is None
    assert "wygasła" in str(exc.value)


def test_nie_zapisujemy_cudzego_cache(tmp_path: Path, atrapa_msal: type[_AtrapaAplikacji]) -> None:
    """Mont jest RO i cudzy: nawet gdy msal ZNACZY cache jako zmieniony, plik zostaje nietknięty."""
    cache_path = tmp_path / "teams_token_cache.bin"
    _zapisz_cache(cache_path)
    przed = cache_path.read_bytes()

    build_silent_token_provider(_ustawienia(cache_path))()

    app = atrapa_msal.ostatnia
    assert app is not None
    assert app.token_cache is not None
    assert app.token_cache.has_state_changed is True  # atrapa udała odświeżenie
    assert cache_path.read_bytes() == przed
    assert list(tmp_path.iterdir()) == [cache_path]  # żadnego pliku tymczasowego obok


def test_kazde_wywolanie_czyta_cache_od_nowa(
    tmp_path: Path, atrapa_msal: type[_AtrapaAplikacji]
) -> None:
    """Tamten proces rotuje refresh-token — bierzemy stan z DYSKU, nie zamrożony przy budowie."""
    cache_path = tmp_path / "teams_token_cache.bin"
    _zapisz_cache(cache_path, username="stare@example.com")
    get_token = build_silent_token_provider(_ustawienia(cache_path))

    get_token()
    pierwsza = atrapa_msal.ostatnia
    _zapisz_cache(cache_path, username="nowe@example.com")
    get_token()
    druga = atrapa_msal.ostatnia

    assert pierwsza is not None and druga is not None
    assert pierwsza is not druga  # nowa aplikacja i nowy cache na każde wywołanie
    assert pierwsza.wywolania[0][1]["username"] == "stare@example.com"
    assert druga.wywolania[0][1]["username"] == "nowe@example.com"
