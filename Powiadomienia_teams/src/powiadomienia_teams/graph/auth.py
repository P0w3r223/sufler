"""Logowanie do Microsoft Graph przez device-code flow (delegowane, jako użytkownik).

- Pierwsze uruchomienie / po utracie tokenu: jednorazowe interaktywne logowanie
  ``powiadomienia-teams --login`` (kod + microsoft.com/devicelogin).
- W pętli usługi dostawca tokenu jest WYŁĄCZNIE CICHY (``acquire_token_silent`` z refresh-tokenu
  w cache) — NIGDY nie inicjuje interaktywnego device-flow, bo ten blokuje aż do zalogowania i
  w usłudze bez terminala zawiesiłby cały nasłuch.
- Utrata refresh-tokenu (rolling ~90 dni albo Conditional Access) → ``AuthExpiredError`` zamiast
  zawisu; orkiestracja loguje CRITICAL i zatrzymuje się czysto (patrz ``app.run_forever``).
- App MSAL i cache budowane RAZ w ``build_token_provider`` (nie co wywołanie) — token trzyma się
  w pamięci, plik czytany na starcie, zapisywany dopiero gdy refresh-token się zmieni.
- Cache tokenu jest SEKRETEM — poza repo i ``data/`` (patrz ``Settings.token_cache_path``).

Import ``msal`` jest LENIWY (w fabryce aplikacji), więc sam import modułu i testy wyższych warstw
go nie wymagają; ``app_factory`` jest wstrzykiwalny, więc testy podają atrapę bez ``msal``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from powiadomienia_teams.config import Settings

logger = logging.getLogger(__name__)


class AuthExpiredError(RuntimeError):
    """Utracono refresh-token — wymagane ponowne logowanie ``powiadomienia-teams --login``.

    Rzucany zamiast blokowania pętli usługi na interaktywnym device-code, żeby proces bez
    terminala nie zawisł na cichej próbie odświeżenia tokenu.
    """

    @property
    def publiczny(self) -> str:
        """Treść nadająca się na kanał ZEWNĘTRZNY (webhook alertów) — bez danych osobowych.

        Domyślnie równa pełnemu komunikatowi; podklasy niosące dane osobowe nadpisują ją węższą
        wersją. Log usługi zostaje pełny — to kanał wewnętrzny, czytany przez operatora.
        """
        return str(self)


class AmbiguousAccountError(AuthExpiredError):
    """Cache MSAL zawiera kilka kont — nie wiadomo, którą tożsamością bot ma pisać.

    Wydzielony z ``AuthExpiredError``, bo naprawa jest INNA: ponowne logowanie nie pomaga,
    tylko dokłada kolejne konto do cache. Jedyne wyjście to usunięcie pliku cache przez
    człowieka, dlatego ścieżka startowa nie może na to odpowiedzieć device-flow.

    Pełny komunikat wymienia ADRESY E-MAIL kont — to dane osobowe pracowników. Trafiają do logu
    usługi (kanał wewnętrzny), ale NIE na webhook alertów: ten bywa poza organizacją (Power
    Automate, Slack, dowolny endpoint operatora), więc ``publiczny`` podaje samą liczbę kont.
    """

    def __init__(
        self, message: str, *, liczba_kont: int = 0, cache_path: Path | None = None
    ) -> None:
        super().__init__(message)
        self._liczba_kont = liczba_kont
        self._cache_path = cache_path

    @property
    def publiczny(self) -> str:
        # Wersja publiczna jest budowana OD ZERA, nie przez wycinanie z pełnego komunikatu:
        # redakcja przez usuwanie zawodzi po cichu, gdy tekst źródłowy się zmieni.
        ile = f"{self._liczba_kont} kont" if self._liczba_kont else "kilka kont"
        gdzie = f" Usuń plik {self._cache_path}." if self._cache_path else ""
        return (
            f"Cache tokenu zawiera {ile} — nie wiadomo, którą tożsamością pisać "
            f"(adresy kont są w logu usługi).{gdzie} Potem zaloguj się ponownie: --login"
        )


def _jedyne_konto(accounts: list[Any], cache_path: Path) -> None:
    """Upewnij się, że cache wskazuje JEDNĄ tożsamość — inaczej zatrzymaj się przed wysyłką.

    Kolejność ``get_accounts()`` nie jest kontraktem MSAL, więc „weź pierwsze" przy dwóch sesjach
    to losowanie nadawcy. Skutek widzi cały zespół (wiadomości od niewłaściwej osoby) i jest
    nieodwracalny, a wdrożeniowe „pamiętaj wyczyścić cache" wykonuje się raz i zapomina.
    """
    if len(accounts) <= 1:
        return
    nazwy = ", ".join(sorted(str(a.get("username", "?")) for a in accounts))
    raise AmbiguousAccountError(
        f"Cache tokenu zawiera {len(accounts)} kont ({nazwy}) — nie wiadomo, którą tożsamością "
        f"pisać. Usuń plik {cache_path}, a potem zaloguj się ponownie: --login",
        liczba_kont=len(accounts),
        cache_path=cache_path,
    )


def _load_cache(cache_path: Path) -> Any:
    import msal

    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        try:
            # encoding JAWNIE: cache zawiera claim `name` z ID-tokenu (polskie znaki), a usługa
            # systemd bez LANG dostaje locale POSIX → ASCII i deserializacja wywala się
            # UnicodeDecodeError.
            cache.deserialize(cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            # Uszkodzony plik NIE może kłaść procesu przed jakimkolwiek logiem: `--login`, czyli
            # udokumentowana droga ratunkowa, idzie przez tę samą fabrykę i wywaliłby się
            # identycznie — usługa byłaby nie do odzyskania bez ręcznego kasowania pliku
            # w wolumenie. Pusty cache oznacza tylko ponowne logowanie, czyli stan naprawialny.
            logger.warning(
                "Uszkodzony cache tokenu %s — zaloguj się ponownie: `powiadomienia-teams --login`.",
                cache_path,
            )
    return cache


def _otworz_wylacznie(tmp: Path) -> int:
    """Utwórz plik tymczasowy z prawami 600, sprzątając resztkę po ubitym procesie.

    ``O_EXCL`` gwarantuje, że prawa pochodzą z TEGO utworzenia, a nie z cudzego pliku zostawionego
    pod innym umask. Ale sam ``O_EXCL`` na gorącej ścieżce odświeżania tokenu jest pułapką: jedna
    resztka po SIGKILL wywracałaby KAŻDĄ kolejną rotację refresh-tokenu, czyli po ~90 dniach
    usługę nie do odzyskania bez ręcznego kasowania pliku w wolumenie. Dlatego kolizję traktujemy
    jako sytuację do posprzątania — a nie do przemilczenia (``suppress`` zamieniłby ją z powrotem
    w cichy ``FileExistsError`` z drugiej próby).
    """
    flagi = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        return os.open(tmp, flagi, 0o600)
    except FileExistsError:
        logger.warning("Usuwam resztkę po przerwanym zapisie cache tokenu: %s", tmp)
        tmp.unlink()  # nieudane sprzątanie MA być głośne — inaczej wracamy do pułapki wyżej
        return os.open(tmp, flagi, 0o600)


def _save_cache(cache: Any, cache_path: Path) -> None:
    """Zapisz cache tylko gdy MSAL zmienił stan (np. rotacja refresh-tokenu). Prawa 600 (POSIX).

    Plik zawiera REFRESH-TOKEN, czyli sekret o czasie życia liczonym w tygodniach. Powstaje przez
    ``os.open(..., 0o600)``, a nie ``write_text`` + ``chmod``: ta druga kolejność tworzyła plik pod
    domyślnym umask (zwykle 0o022, czyli czytelny dla grupy i świata), zapisywała do niego token
    i dopiero POTEM zamykała prawa. Okno było krótkie, ale otwierało się przy każdej rotacji
    tokenu, a wystarczy jeden odczyt, żeby przejąć tożsamość bota.

    ``fsync`` przed podmianą: bez niego zapis mógł siedzieć w buforze, a nagła utrata zasilania
    zostawiała pusty cache mimo udanego ``os.replace`` — czyli usługę wymagającą ręcznego
    ``--login`` przy najbliższym starcie (jak w ``state.save_state``).
    """
    if not getattr(cache, "has_state_changed", False):
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Zapis ATOMOWY (jak `state.save_state`): `write_text` obcina plik przed zapisem, więc SIGKILL
    # po karencji `docker compose down` albo OOM w trakcie rotacji refresh-tokenu zostawiał ucięty
    # cache — a ten wywracał następny start jeszcze przed pierwszym logiem.
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    with os.fdopen(_otworz_wylacznie(tmp), "w", encoding="utf-8") as plik:
        plik.write(cache.serialize())
        plik.flush()
        os.fsync(plik.fileno())
    os.replace(tmp, cache_path)


_MAX_ERROR_DESC = 300  # ile znaków opisu błędu z Entra ID trafia do komunikatu
_MSAL_TIMEOUT_S = 30  # limit czasu żądań do Entra ID (spójny z klientem Graph i Anthropica)

# Kody AADSTS, które realnie kończą sesję tej usługi — pomocne przy diagnozie z samego alertu:
#   700082 / 70008 → refresh-token wygasł z BEZCZYNNOŚCI (usługa nie chodziła > 90 dni)
#   50173          → grant cofnięty (zmiana lub reset hasła konta bota)
#   50076 / 50158  → wymagane MFA albo inna kontrola Conditional Access
#   50078 / 70043  → polityka sign-in frequency (public client nie dostaje odroczenia)
#   530036         → Conditional Access blokuje device code flow — token nie do odzyskania
#   65001          → cofnięta zgoda administratora na aplikację


def _auth_error_message(accounts: Any, result: dict[str, Any] | None) -> str:
    """Zbuduj komunikat utraty sesji wzbogacony o przyczynę zgłoszoną przez Entra ID."""
    if not accounts:
        return "Brak konta w cache tokenu — zaloguj się: `powiadomienia-teams --login`."
    if not result:
        return "Ciche odświeżenie nie zwróciło wyniku — zaloguj się: `powiadomienia-teams --login`."
    kod = str(result.get("error") or "nieznany_blad")
    opis = str(result.get("error_description") or "")[:_MAX_ERROR_DESC]
    return (
        f"Utracono sesję ({kod}) — zaloguj się ponownie: `powiadomienia-teams --login`. "
        f"Przyczyna: {opis}"
    )


def _default_app_and_cache(settings: Settings) -> tuple[Any, Any]:
    """Domyślna fabryka: ``PublicClientApplication`` + serializowalny cache (wymaga ``msal``)."""
    import msal

    cache = _load_cache(settings.token_cache_path)
    # `timeout` JAWNIE: bez niego MSAL wiąże `timeout=None` z metodą `request` swojej wewnętrznej
    # sesji `requests`, czyli żądania do login.microsoftonline.com nie mają ŻADNEGO limitu czasu.
    # Blackhole sieciowy (firewall DROP zamiast REJECT — typowe po zmianie polityki na serwerze)
    # zawieszał wtedy proces bezterminowo. To ścieżka gorąca: `refresh_auth()` woła ją w każdym
    # przebiegu, w listenerze i w pulsie. Docker nie restartuje kontenera „unhealthy", więc
    # healthcheck by tego nie uratował — jedyną obroną jest ten limit.
    app = msal.PublicClientApplication(
        settings.client_id,
        authority=settings.authority,
        token_cache=cache,
        timeout=_MSAL_TIMEOUT_S,
    )
    return app, cache


def build_token_provider(
    settings: Settings,
    *,
    app_factory: Callable[[Settings], tuple[Any, Any]] = _default_app_and_cache,
) -> Callable[[], str]:
    """Zbuduj dostawcę tokenu SILENT-ONLY (cichy refresh; utrata tokenu → ``AuthExpiredError``).

    App i cache budowane RAZ (``app_factory`` — wstrzykiwalny do testów bez ``msal``). ``get_token``
    zwraca token z cichego odświeżenia; przy braku ważnego tokenu rzuca ``AuthExpiredError`` (nie
    inicjuje device-flow — to robi tylko ``login_interactive`` ze startu).
    """
    app, cache = app_factory(settings)
    scopes = list(settings.scopes)
    cache_path = settings.token_cache_path

    def get_token() -> str:
        accounts = app.get_accounts()
        _jedyne_konto(accounts, cache_path)
        # `..._with_error` zamiast `acquire_token_silent`: ta druga zwraca None ZARÓWNO przy pustym
        # cache, JAK I przy odrzuconym odświeżeniu, więc nie da się odróżnić „trzeba się zalogować"
        # od „tenant właśnie zmienił politykę". Wersja z błędem niesie kod AADSTS — jedyną rzecz,
        # która na serwerze bez terminala mówi, co naprawdę się stało.
        result = (
            app.acquire_token_silent_with_error(scopes, account=accounts[0]) if accounts else None
        )
        _save_cache(cache, cache_path)  # utrwal ewentualnie zrotowany refresh-token
        if result and "access_token" in result:
            return str(result["access_token"])
        raise AuthExpiredError(_auth_error_message(accounts, result))

    return get_token


def login_interactive(
    settings: Settings,
    *,
    app_factory: Callable[[Settings], tuple[Any, Any]] = _default_app_and_cache,
) -> None:
    """Jednorazowe interaktywne logowanie device-code (kod + microsoft.com/devicelogin).

    Wołane WYŁĄCZNIE ze startu (``--login`` lub pierwszy start z terminalem), NIGDY z pętli usługi —
    ``acquire_token_by_device_flow`` blokuje aż do zalogowania. Po sukcesie zapisuje refresh-token
    do cache, więc dalsze działanie idzie już cichym odświeżeniem.
    """
    app, cache = app_factory(settings)
    # Bramka PRZED device-flow: przy dwóch kontach w cache logowanie dołożyłoby TRZECIE, a operator
    # zobaczyłby „zalogowano" i usługę padającą przy pierwszym odświeżeniu tokenu.
    _jedyne_konto(app.get_accounts(), settings.token_cache_path)
    flow = app.initiate_device_flow(scopes=list(settings.scopes))
    if "user_code" not in flow:
        raise RuntimeError(
            "Nie udało się rozpocząć device flow: " + json.dumps(flow, ensure_ascii=False)
        )
    print(flow["message"])  # „wejdź na adres i wpisz kod"
    sys.stdout.flush()
    result = app.acquire_token_by_device_flow(flow)  # blokuje aż do zalogowania
    _save_cache(cache, settings.token_cache_path)
    if not result or "access_token" not in result:
        error = (result or {}).get("error")
        description = (result or {}).get("error_description")
        raise RuntimeError(f"Logowanie nie powiodło się: {error} — {description}")
