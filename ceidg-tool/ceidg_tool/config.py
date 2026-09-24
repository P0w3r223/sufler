"""Konfiguracja: token, środowisko, katalogi danych, retencja (uzupelnienie-01.md §B).

Token pochodzi kolejno z: argumentu, systemowego magazynu haseł (`keyring`), zmiennej
środowiskowej `CEIDG_TOKEN`, pliku `.env`. Nigdzie nie jest wypisywany — w logach i bazie
występuje wyłącznie jego skrót. Zgoda na produkcję jest argumentem `resolve_environment`,
nigdy globalną flagą.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from platformdirs import user_data_dir

from .errors import ConfigError, ProdWithoutConsentError

Environment = Literal["test", "prod"]
TokenSource = Literal["keyring", "env", ".env", "argument"]

APP_NAME = "ceidg-tool"
ENVIRONMENTS: tuple[Environment, ...] = ("test", "prod")
DEFAULT_ENVIRONMENT: Environment = "test"
DEFAULT_ENV_FILE = Path(".env")
DEFAULT_CACHE_TTL_DAYS = 7
DEFAULT_RETENTION_DAYS = 30
TOKEN_EXPIRY_WARNING_DAYS = 7

ENV_TOKEN = "CEIDG_TOKEN"
ENV_ENVIRONMENT = "CEIDG_ENV"
ENV_DATA_DIR = "CEIDG_DATA_DIR"
ENV_CACHE_TTL_DAYS = "CEIDG_CACHE_TTL_DAYS"
ENV_RETENTION_DAYS = "CEIDG_RETENTION_DAYS"
ENV_PROFILE_PATH = "CEIDG_PROFILE"

KEYRING_SERVICE = "ceidg-tool"
KEYRING_USERNAME = "CEIDG_TOKEN"

# Drugie poświadczenie: klucz API asystenta (ADR-0011, decyzja 7). Osobna pozycja w magazynie
# haseł, bo to inny sekret o innej stawce — token CEIDG niesie PESEL, więc jego wyciek jest
# incydentem z danymi osobowymi; klucz jest środkiem płatniczym, więc jego wyciek to rachunek
# i podszycie. Obsługa taka sama, powód inny.
ENV_ANTHROPIC_KEY = "ANTHROPIC_API_KEY"


# Zdanie o trybie pokazu. Mieszka tutaj, bo potrzebują go dwie warstwy, które **nie mogą**
# się widzieć: `ui.texts` (pierwszy ekran) i `pipeline.build_metadata` (arkusz `Metadane`).
# Kierunek zależności biegnie `ui` -> `pipeline`, więc import w drugą stronę odwróciłby go,
# a druga kopia zdania rozjechałaby się przy pierwszej poprawce — tak jak rozjechał się
# komentarz o zapasie limitera między `prod.yaml` i `test.yaml`.
DEMO_OSTRZEZENIE = (
    "TRYB POKAZU — dane są WYMYŚLONE. Żadne żądanie nie wychodzi do CEIDG, a firmy, NIP-y "
    "i adresy poniżej nie opisują nikogo. Nie używaj tych wyników do niczego."
)
KEYRING_ASSISTANT_USERNAME = "ANTHROPIC_API_KEY"

TOKEN_SERVICE_URL = "https://www.biznes.gov.pl/pl/e-uslugi/00_9999_00"

# Jedyne hosty, do których narzędzie wysyła token (uzupelnienie-01.md §B).
ALLOWED_HOSTS: frozenset[str] = frozenset({"dane.biznes.gov.pl", "test-dane.biznes.gov.pl"})

# Druga lista, **nigdy nie sumowana z pierwszą** (ADR-0011, decyzja 1). Klient CEIDG ma nie móc
# sięgnąć do modelu, a klient modelu do CEIDG — i to jest cała treść §B po stronie sieci: do
# `api.anthropic.com` idzie treść pytania i słownik PKD, pobrane rekordy nigdy. Suma tych zbiorów
# skasowałaby ten rozdział jednym dopisaniem hosta, więc test pilnuje, że pozostają rozłączne.
MODEL_ALLOWED_HOSTS: frozenset[str] = frozenset({"api.anthropic.com"})
# Host jest związany ze środowiskiem: profil wskazujący produkcję przy środowisku `test`
# omijałby zgodę na produkcję (pipeline.build_deps sprawdza tę parę).
HOST_ENVIRONMENT: dict[str, Environment] = {
    "dane.biznes.gov.pl": "prod",
    "test-dane.biznes.gov.pl": "test",
}

# Wzorzec JWT do maskowania w logach i komunikatach.
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

# Klucz API Anthropic — drugi sekret, który pojawia się w fazie 4 (ADR-0011, decyzja 7).
# Stawka jest inna niż przy tokenie CEIDG: token niesie PESEL, więc jego wyciek to incydent
# z danymi osobowymi; klucz jest środkiem płatniczym, więc jego wyciek to rachunek i podszycie.
# Obsługa taka sama, powód inny — warto to zapisać, żeby nikt nie „uprościł" jednego z nich
# argumentem, że to przecież nie PESEL.
ANTHROPIC_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")

# Maskowanie jest **generyczne wobec kształtu sekretu**, a nie związane z JWT. Do 2026-09-07
# `mask_tokens` znało wyłącznie JWT, więc gwarancja opisana w CLAUDE.md i w phase2_core
# („token nie trafia do logów, komunikatów, bazy ani plików wynikowych") obowiązywała dokładnie
# jeden kształt sekretu. Klucz `sk-ant-…` przechodziłby przez `richtext.safe` i przez
# `MaskingFormatter` dosłownie — i nic by tego nie zauważyło, bo scenariusz 7 szukał tylko
# tokenu CEIDG. Lista jest krotką, a nie sumą wzorców w jednym `re`, bo test scenariusza 7
# wyprowadza z niej sadzone sekrety: dopisanie wzorca bez dopisania go do testu jest wtedy
# niemożliwe, zamiast być łatwe do przeoczenia.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (JWT_RE, ANTHROPIC_KEY_RE)

_FILENAME_FORBIDDEN = re.compile(r"[^A-Za-z0-9_.\-ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+")
MAX_FILENAME_STEM = 100


# Wartości sekretów, które ten proces faktycznie trzyma. Wzorzec łapie **kształt**, ten zbiór
# łapie **wartość** — a token CEIDG wcale nie musi być JWT: `inspect_token` obsługuje token
# nieprzezroczysty (`is_jwt=False`) i `tests/test_config.py` tę ścieżkę utrwala. Bez rejestru
# „maskujemy każdy sekret" znaczyłoby „maskujemy każdy sekret o znanym kształcie", a §D chce
# zera trafień po `grep` całej sesji, nie zera trafień dla dwóch regexów.
#
# Stan globalny jest tu świadomy i minimalny: `MaskingFormatter` dostaje `LogRecord`, a
# `richtext.safe` pojedynczą komórkę — żadne z nich nie widzi `Settings`, więc alternatywą
# byłoby przeciąganie sekretów przez każdy szew wyjścia. Zbiór jest tylko dopisywalny,
# a `forget_secrets()` istnieje wyłącznie po to, żeby testy nie przeciekały do siebie.
_KNOWN_SECRETS: set[str] = set()

# Krótkiej wartości nie rejestrujemy: sekret długości 3 zamieniłby każdy tekst zawierający te
# trzy znaki w `<token>`, czyli maskowanie zjadłoby komunikaty zamiast chronić sekret.
MIN_REGISTERED_SECRET = 12


def register_secret(value: str | None) -> None:
    """Dopisuje konkretną wartość do maskowania (wołane przy składaniu `Settings`)."""
    if value and len(value) >= MIN_REGISTERED_SECRET:
        _KNOWN_SECRETS.add(value)


def forget_secrets() -> None:
    """Czyści rejestr — wyłącznie dla izolacji testów."""
    _KNOWN_SECRETS.clear()


def mask_tokens(text: str) -> str:
    """Zastępuje każdy sekret w tekście znacznikiem `<token>`.

    Nazwa została, bo wymieniają ją CLAUDE.md i `docs/design/phase2_core.md`; zmieniła się
    treść. Dwa mechanizmy, w tej kolejności: znane **wartości** (bo token nie-JWT nie pasuje
    do żadnego wzorca), potem znane **kształty** z `SECRET_PATTERNS` (bo sekret cudzy albo
    jeszcze niezarejestrowany też nie ma prawa przejść)."""
    for secret in _KNOWN_SECRETS:
        text = text.replace(secret, "<token>")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("<token>", text)
    return text


def resolve_environment(requested: str | None, *, prod_consent: bool) -> Environment:
    """Zwraca środowisko; produkcja wymaga jawnej zgody przekazanej z warstwy CLI."""
    value = (requested or DEFAULT_ENVIRONMENT).strip().lower()
    if value not in ENVIRONMENTS:
        raise ConfigError(f"Nieznane środowisko {value!r}. Dozwolone: {', '.join(ENVIRONMENTS)}.")
    if value == "prod" and not prod_consent:
        raise ProdWithoutConsentError(
            "Środowisko produkcyjne wymaga jawnej zgody (np. flaga --produkcja "
            "z potwierdzeniem). Domyślnie używane jest środowisko testowe."
        )
    return "prod" if value == "prod" else "test"


def token_fingerprint(token: str) -> str:
    """Skrót tokenu do logów i historii żądań — sam token nigdzie nie trafia."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def default_data_dir() -> Path:
    """Katalog danych użytkownika (Windows: %LOCALAPPDATA%\\ceidg-tool)."""
    return Path(user_data_dir(APP_NAME, appauthor=False))


def safe_filename(stem: str, suffix: str = ".xlsx") -> str:
    """Nazwa pliku bez znaków ścieżki, o ograniczonej długości."""
    cleaned = _FILENAME_FORBIDDEN.sub("_", stem).strip("_.")
    cleaned = re.sub(r"_+", "_", cleaned) or "ceidg"
    return cleaned[:MAX_FILENAME_STEM] + suffix


# ----------------------------------------------------------------------------- token


@dataclass(frozen=True)
class TokenInfo:
    """Metadane tokenu odczytane lokalnie z JWT (bez weryfikacji podpisu)."""

    fingerprint: str
    is_jwt: bool
    issued_at: datetime | None = None
    expires_at: datetime | None = None

    def days_left(self, now: datetime) -> float | None:
        if self.expires_at is None:
            return None
        return (self.expires_at - now).total_seconds() / 86_400

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    def validity_text(self, now: datetime) -> str:
        """Tekst na pierwszy ekran: „ważny do …” albo „brak daty wygaśnięcia w tokenie”."""
        if self.expires_at is not None:
            return f"ważny do {self.expires_at.astimezone().strftime('%Y-%m-%d %H:%M')}"
        issued = (
            f", wystawiony {self.issued_at.astimezone().strftime('%Y-%m-%d %H:%M')}"
            if self.issued_at
            else ""
        )
        return f"brak daty wygaśnięcia w tokenie{issued}"

    def warning(self, now: datetime) -> str | None:
        left = self.days_left(now)
        if left is None:
            return None
        if left <= 0:
            return "Token wygasł — uzyskaj nowy usługą " + TOKEN_SERVICE_URL
        if left <= TOKEN_EXPIRY_WARNING_DAYS:
            return f"Token wygasa za {left:.1f} dnia/dni — przygotuj nowy: {TOKEN_SERVICE_URL}"
        return None


def _b64url_json(segment: str) -> dict[str, Any] | None:
    try:
        padded = segment + "=" * (-len(segment) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _epoch_to_dt(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def inspect_token(token: str) -> TokenInfo:
    """Odczytuje `iat`/`exp` z JWT; token nie-JWT jest traktowany jako nieprzezroczysty."""
    parts = token.split(".")
    if len(parts) != 3 or not JWT_RE.fullmatch(token):
        return TokenInfo(fingerprint=token_fingerprint(token), is_jwt=False)
    payload = _b64url_json(parts[1]) or {}
    return TokenInfo(
        fingerprint=token_fingerprint(token),
        is_jwt=True,
        issued_at=_epoch_to_dt(payload.get("iat")),
        expires_at=_epoch_to_dt(payload.get("exp")),
    )


def read_token_from_keyring(username: str = KEYRING_USERNAME) -> str | None:
    """Sekret z systemowego magazynu haseł; brak magazynu to nie błąd, tylko brak sekretu.

    `username` wybiera pozycję: token CEIDG albo klucz asystenta. Jedna funkcja na oba, bo
    obsługa jest identyczna — druga kopia z podmienioną stałą to druga kopia do poprawienia,
    gdy magazyn zacznie zachowywać się inaczej.
    """
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        return None
    try:
        value = keyring.get_password(KEYRING_SERVICE, username)
    except KeyringError:
        return None
    return value.strip() if value else None


def store_token_in_keyring(token: str, username: str = KEYRING_USERNAME) -> None:
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise ConfigError("Pakiet keyring nie jest zainstalowany.") from exc
    try:
        keyring.set_password(KEYRING_SERVICE, username, token)
    except KeyringError as exc:
        raise ConfigError(f"Nie można zapisać sekretu w magazynie haseł: {exc}") from exc


def delete_token_from_keyring(username: str = KEYRING_USERNAME) -> bool:
    try:
        import keyring
        from keyring.errors import KeyringError, PasswordDeleteError
    except ImportError:
        return False
    try:
        keyring.delete_password(KEYRING_SERVICE, username)
    except PasswordDeleteError:
        return False
    except KeyringError as exc:
        raise ConfigError(f"Nie można usunąć sekretu z magazynu haseł: {exc}") from exc
    return True


def ensure_private_env_file(path: Path) -> str | None:
    """Na POSIX wymusza `600` na `.env`; na Windows NTFS nie ma trybu POSIX — bez zmian."""
    if not path.exists() or sys.platform.startswith("win"):
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        try:
            path.chmod(0o600)
        except OSError:
            return f"Plik {path} jest czytelny dla innych użytkowników (uprawnienia {mode:o})."
    return None


# ----------------------------------------------------------------------------- ustawienia


@dataclass(frozen=True)
class Settings:
    """Ustawienia jednego uruchomienia. `repr` nie ujawnia tokenu."""

    token: str = field(repr=False)
    environment: Environment = DEFAULT_ENVIRONMENT
    data_dir: Path = field(default_factory=default_data_dir)
    cache_ttl_days: int = DEFAULT_CACHE_TTL_DAYS
    retention_days: int = DEFAULT_RETENTION_DAYS
    profile_path: Path | None = None
    token_source: TokenSource = "env"
    # Klucz asystenta jest **opcjonalny**: jego brak wyłącza asystenta, a nie program.
    # To jest cała różnica wobec tokenu CEIDG, którego brak zatrzymuje pracę przed pierwszym
    # żądaniem — bez asystenta kreator i CLI prowadzą do pliku tak samo.
    anthropic_key: str | None = field(default=None, repr=False)
    anthropic_key_source: TokenSource | None = None
    warnings: tuple[str, ...] = ()

    @property
    def store_path(self) -> Path:
        return self.data_dir / f"store-{self.environment}.sqlite"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "wyniki"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logi"

    @property
    def token_fp(self) -> str:
        return token_fingerprint(self.token)

    @property
    def token_info(self) -> TokenInfo:
        return inspect_token(self.token)

    @property
    def assistant_key_fp(self) -> str | None:
        """Odcisk klucza asystenta — nigdy jego wartość. Do `sprawdz-token` i do logu."""
        return token_fingerprint(self.anthropic_key) if self.anthropic_key else None


def _read_int(raw: str | None, *, name: str, default: int) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} musi być liczbą całkowitą, jest: {raw!r}.") from exc
    if value < 0:
        raise ConfigError(f"{name} nie może być ujemne.")
    return value


def load_settings(
    *,
    env_file: Path | None = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
    environment: str | None = None,
    prod_consent: bool = False,
    data_dir: Path | None = None,
    profile_path: Path | None = None,
    token: str | None = None,
    use_keyring: bool = True,
    now: datetime | None = None,
) -> Settings:
    """Składa ustawienia. Token: argument > keyring > zmienna środowiskowa > `.env`.

    `environ` i `use_keyring=False` pozwalają testom pominąć zasoby systemowe.
    Token wygasły zatrzymuje program przed pierwszym żądaniem (scenariusz 6).
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    file_values: Mapping[str, str | None] = (
        dotenv_values(env_file) if env_file is not None and env_file.exists() else {}
    )
    warnings: list[str] = []

    def pick(key: str) -> str | None:
        value = env.get(key)
        if value is None or value.strip() == "":
            value = file_values.get(key)
        return value.strip() if value else None

    source: TokenSource
    file_token = (file_values.get(ENV_TOKEN) or "").strip()
    if token and token.strip():
        source, resolved_token = "argument", token.strip()
    elif use_keyring and (keyring_token := read_token_from_keyring()):
        source, resolved_token = "keyring", keyring_token
    elif env.get(ENV_TOKEN, "").strip():
        source, resolved_token = "env", env[ENV_TOKEN].strip()
    elif file_token:
        source, resolved_token = ".env", file_token
        if env_file is not None and (hint := ensure_private_env_file(env_file)):
            warnings.append(hint)
    else:
        raise ConfigError(
            f"Brak tokenu: zapisz go poleceniem `ceidg-tool token zapisz`, ustaw {ENV_TOKEN} "
            f"w zmiennych środowiskowych albo w pliku .env. Token uzyskasz usługą "
            f"{TOKEN_SERVICE_URL} (logowanie Profilem Zaufanym). Ta paczka tokenu nie "
            f"zawiera — bez własnego działa `szukaj-pkd` i polecenia przyjmujące `--demo`."
        )

    info = inspect_token(resolved_token)
    moment = now or datetime.now(tz=UTC)
    if info.is_expired(moment):
        raise ConfigError(
            f"Token wygasł ({info.validity_text(moment)}). Uzyskaj nowy: {TOKEN_SERVICE_URL}"
        )
    if warning := info.warning(moment):
        warnings.append(warning)

    env_name = resolve_environment(environment or pick(ENV_ENVIRONMENT), prod_consent=prod_consent)
    data_dir_raw = pick(ENV_DATA_DIR)
    resolved_data_dir = data_dir or (Path(data_dir_raw) if data_dir_raw else default_data_dir())
    profile_raw = pick(ENV_PROFILE_PATH)
    resolved_profile = profile_path or (Path(profile_raw) if profile_raw else None)

    # Klucz asystenta: keyring → zmienna środowiskowa → `.env`. **Nigdy własny łańcuch SDK**
    # (`ANTHROPIC_AUTH_TOKEN`, profil `ant auth login` z dysku, zmienne federacyjne) — narzędzie
    # wydawałoby wtedy poświadczenie, którego nikt mu nie dał, a `sprawdz-token` nie umiałby
    # o nim opowiedzieć. Reguła granic 12 wymusza jawne `api_key=` po drugiej stronie.
    #
    # Brak klucza **nie jest błędem**: wyłącza asystenta, nie program.
    assistant_source: TokenSource | None = None
    assistant_key: str | None = None
    if use_keyring and (z_magazynu := read_token_from_keyring(KEYRING_ASSISTANT_USERNAME)):
        assistant_source, assistant_key = "keyring", z_magazynu
    elif env.get(ENV_ANTHROPIC_KEY, "").strip():
        assistant_source, assistant_key = "env", env[ENV_ANTHROPIC_KEY].strip()
    elif z_pliku := (file_values.get(ENV_ANTHROPIC_KEY) or "").strip():
        assistant_source, assistant_key = ".env", z_pliku
        # Ta sama kontrola uprawnień co przy tokenie. Bez niej układ „token w keyringu, klucz
        # w .env" zostawiał plik ze środkiem płatniczym bez sprawdzenia, bo warunek niżej
        # patrzy wyłącznie na to, skąd przyszedł **token**.
        if env_file is not None and (hint := ensure_private_env_file(env_file)):
            if hint not in warnings:
                warnings.append(hint)
    register_secret(assistant_key)

    # Rejestracja przed zwróceniem ustawień: od tej chwili maskowanie zna wartość tokenu,
    # także wtedy, gdy nie jest on JWT-em i nie pasuje do żadnego wzorca.
    register_secret(resolved_token)

    return Settings(
        token=resolved_token,
        environment=env_name,
        data_dir=resolved_data_dir,
        cache_ttl_days=_read_int(
            pick(ENV_CACHE_TTL_DAYS), name=ENV_CACHE_TTL_DAYS, default=DEFAULT_CACHE_TTL_DAYS
        ),
        retention_days=_read_int(
            pick(ENV_RETENTION_DAYS), name=ENV_RETENTION_DAYS, default=DEFAULT_RETENTION_DAYS
        ),
        profile_path=resolved_profile,
        token_source=source,
        anthropic_key=assistant_key,
        anthropic_key_source=assistant_source,
        warnings=tuple(warnings),
    )
