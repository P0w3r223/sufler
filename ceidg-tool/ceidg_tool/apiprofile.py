"""Profil API — ustalenia z sondy (faza 1) jako dane, nie stałe w kodzie (ADR-0001).

Profil opisuje wyłącznie protokół. Przełączniki zachowania narzędzia (np. „pobieraj
szczegóły”) należą do `Criteria` albo `Settings`.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import ALLOWED_HOSTS
from .errors import ConfigError

PagingMode = Literal["links", "numeric"]
CountSemantics = Literal["total", "page"]
DetailMode = Literal["query", "path"]
PkdFormat = Literal["compact", "dotted"]
ValueCase = Literal["upper", "lower"]


class RateProfile(BaseModel):
    """Parametry limitera i ponawiania — patrz ADR-0003."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    windows: tuple[tuple[int, float], ...] = ((48, 180.0), (960, 3600.0))
    # 3,75 s, a nie 3,6 s zalecane w dokumentacji API: oba okna wypadają po tyle
    # (180/48 = 3600/960 = 3,75), a odstęp mniejszy od tego, co narzuca okno, nie przyspiesza —
    # pozwala puszczać gęściej i nadrabia postojem. Zmierzone na prawdziwym limiterze przy 3,6 s:
    # szczyt 49 żądań na 180 s przy limicie API 50. Wartość jest tu, a nie tylko w obu profilach,
    # bo profil podstawiony przez `CEIDG_PROFILE` bez sekcji `rate:` brał domyślną i po cichu
    # wracał do gęstszego tempa, podczas gdy `estimating.effective_spacing` liczyło już 3,75.
    min_spacing_s: float = Field(default=3.75, ge=0.0)
    cooldown_s: float = Field(default=185.0, ge=0.0)
    max_retries_5xx: int = Field(default=3, ge=0)
    backoff_base_s: float = Field(default=5.0, ge=0.0)
    timeout_s: float = Field(default=60.0, gt=0.0)

    @field_validator("windows")
    @classmethod
    def _windows_positive(
        cls, value: tuple[tuple[int, float], ...]
    ) -> tuple[tuple[int, float], ...]:
        for limit, span in value:
            if limit <= 0 or span <= 0:
                raise ValueError(f"okno limitera musi być dodatnie, jest: ({limit}, {span})")
        return value


NON_DIALECT_FIELDS: set[str] = {"rate"}
"""Pola profilu, które nie wchodzą do `profile_hash`, bo nie zmieniają odczytu odpowiedzi.

Na razie tylko `rate`. `max_pages` i `max_url_length` też są zabezpieczeniami, nie dialektem,
ale zmienia się je rzadko, a każde poszerzenie tej listy dopuszcza wznowienie na profilu
odrobinę innym niż ten, którym pobierano — więc rośnie tylko wtedy, gdy jest po co."""


class ApiProfile(BaseModel):
    """Dialekt API dla jednego środowiska. Każde pole odpowiada pytaniu z docs/decisions.md."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str
    paging_mode: PagingMode = "links"
    page_start: int = Field(default=0, ge=0)
    send_page_on_first_request: bool = False
    default_limit: int = Field(default=25, ge=1)
    max_limit_firmy: int = Field(default=25, ge=1)
    max_limit_zmiana: int = Field(default=500, ge=1)
    count_semantics: CountSemantics = "total"
    ids_batch_size: int = Field(default=1, ge=1)
    max_url_length: int = Field(default=4000, ge=200)
    detail_mode: DetailMode = "query"
    list_root_key: str = "firmy"
    detail_root_key: str = "firma"
    changes_root_key: str = "identyfikatoryWpisow"
    reports_root_key: str = "raporty"
    empty_result_statuses: tuple[int, ...] = (204,)
    list_param_suffix: str = ""
    wojewodztwo_case: ValueCase = "upper"
    pkd_format: PkdFormat = "compact"
    date_format: str = "%Y-%m-%d"
    max_pages: int = Field(default=10_000, ge=1)
    rate: RateProfile = RateProfile()

    @field_validator("base_url")
    @classmethod
    def _base_url_allowed(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise ValueError(
                "base_url musi wskazywać https i jeden z hostów: "
                + ", ".join(sorted(ALLOWED_HOSTS))
            )
        return value

    def canonical_json(self) -> str:
        """Pełny profil — do logów i diagnostyki. Skrót liczy się z `dialect_json`."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def dialect_json(self) -> str:
        """Profil bez `rate`, czyli to, co naprawdę zmienia odczyt odpowiedzi.

        Skrót istnieje po to, żeby wznowienie nie zmieszało dwóch dialektów w jednym pliku:
        innej numeracji stron, innych kluczy korzenia, innego rozmiaru strony. Tempo żądań
        nie należy do tej listy — a dopóki wchodziło do skrótu, każda korekta limitera
        porzucała pobrania w toku. Tempo koryguje się właśnie po 429, czyli dokładnie wtedy,
        gdy jest co wznawiać."""
        return json.dumps(
            self.model_dump(mode="json", exclude=NON_DIALECT_FIELDS),
            sort_keys=True,
            separators=(",", ":"),
        )

    def profile_hash(self) -> str:
        """Skrót dialektu zapisywany w runie; wznowienie na innym dialekcie jest odrzucane."""
        return hashlib.sha256(self.dialect_json().encode("utf-8")).hexdigest()[:16]


def load_profile(environment: str, path: Path | None = None) -> ApiProfile:
    """Ładuje profil z pliku użytkownika albo z pakietu (`ceidg_tool/profiles/<env>.yaml`)."""
    if path is not None:
        source = str(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Nie można odczytać profilu API {source}: {exc}") from exc
    else:
        source = f"ceidg_tool/profiles/{environment}.yaml"
        resource = resources.files("ceidg_tool").joinpath("profiles", f"{environment}.yaml")
        try:
            text = resource.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            raise ConfigError(
                f"Brak wbudowanego profilu API dla środowiska {environment!r}."
            ) from exc

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Profil API {source} nie jest poprawnym YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Profil API {source} musi być mapą klucz: wartość.")
    try:
        return ApiProfile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Niepoprawny profil API {source}:\n{exc}") from exc
