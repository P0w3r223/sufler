"""Scenariusz 7 (UZUPELNIENIE_01 §D): po pełnej sesji offline `grep` tokenu w logach,
bazie, plikach wynikowych i komunikatach daje zero trafień. Uruchamiany w CI."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from ceidg_tool.config import load_settings, mask_tokens
from ceidg_tool.errors import CeidgError
from ceidg_tool.exporter import write_csv, write_jsonl, write_workbook
from ceidg_tool.logsetup import MaskingFormatter, get_logger, setup_logging
from ceidg_tool.normalizer import normalize
from ceidg_tool.ratelimit import RateLimiter
from ceidg_tool.records import RowContext
from ceidg_tool.richtext import safe
from ceidg_tool.store import Store
from tests.conftest import FakeClock, detail_record, list_record
from tests.support import criteria


def make_jwt() -> str:
    def seg(obj: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'HS256'})}.{seg({'iat': 1788596075, 'pesel': '00000000000'})}.{'a' * 64}"


# Sekrety sadzone w pełnej sesji. Klucz asystenta dołączył 2026-09-07 (ADR-0011, znalezisko F1):
# `mask_tokens` znało wyłącznie kształt JWT, więc gwarancja „token nie trafia do logów, komunikatów,
# bazy ani plików wynikowych" obowiązywała dokładnie jeden sekret.
SECRET_SAMPLES: dict[str, str] = {
    "JWT_RE": make_jwt(),
    "ANTHROPIC_KEY_RE": "sk-ant-api03-" + "Aa0_-" * 8,
}


def test_token_never_appears_in_any_artifact(tmp_path: Path, clock: FakeClock) -> None:
    token = SECRET_SAMPLES["JWT_RE"]
    settings = load_settings(
        env_file=None, environ={"CEIDG_TOKEN": token}, data_dir=tmp_path, use_keyring=False
    )
    assert token not in repr(settings)

    log_path = setup_logging(settings.log_dir)
    log = get_logger("test")
    log.info("start, nagłówek Authorization: Bearer %s", token)
    try:
        raise CeidgError(f"błąd z tokenem {token} w treści")
    except CeidgError:
        log.exception("wyjątek")
    log.error(mask_tokens(f"komunikat dla użytkownika: {token}"))
    # Drugi sekret idzie tą samą drogą: bez tego jedyny kanałowo-niezależny test w suicie
    # (grep całego drzewa) pilnowałby wyłącznie kształtu JWT.
    log.info("klucz asystenta: %s", SECRET_SAMPLES["ANTHROPIC_KEY_RE"])
    for handler in logging.getLogger("ceidg_tool").handlers:
        handler.flush()

    with Store(settings.store_path, environment="test", clock=clock) as store:
        limiter = RateLimiter(
            windows=((48, 180.0),),
            min_spacing_s=0.0,
            cooldown_s=185.0,
            clock=clock,
            history=store.history(settings.token_fp),
        )
        limiter.acquire("firmy")
        limiter.note_response(200)
        run_id = store.start_run(
            run_id="r1",
            criteria_json=criteria(wojewodztwo="podlaskie").canonical_json(),
            criteria_hash="h",
            profile_hash="p",
            mode="szczegoly",
            tool_version="0",
            cursor_mode="links",
        )
        store.save_page(run_id, page_index=0, records=[list_record(1)], next_cursor=None)
        store.save_details(details=[detail_record(1)])
        ctx = RowContext(srodowisko="test", pobrano_utc="2026-09-05T10:00:00Z")
        records = [normalize(r, ctx) for r in store.iter_run_records(run_id)]

    metadata = [("srodowisko", settings.environment), ("token_fp", settings.token_fp)]
    write_workbook(settings.output_dir / "out.xlsx", lambda: iter(records), metadata=metadata)
    write_csv(settings.output_dir / "csv", lambda: iter(records))
    write_jsonl(settings.output_dir / "out.jsonl", lambda: iter(records))

    hits = []
    for path in tmp_path.rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            for secret in SECRET_SAMPLES.values():
                if secret.encode() in blob or secret.encode("utf-16-le") in blob:
                    hits.append(f"{path.name}:{secret[:12]}…")
            if path.suffix == ".xlsx":
                import zipfile

                with zipfile.ZipFile(path) as z:
                    for name in z.namelist():
                        body = z.read(name)
                        for secret in SECRET_SAMPLES.values():
                            if secret.encode() in body:
                                hits.append(f"{path.name}:{name}:{secret[:12]}…")
    assert hits == []
    assert "<token>" in log_path.read_text(encoding="utf-8")


# --- drugi sekret i sekret nieprzezroczysty (ADR-0011, znalezisko F1) -------------------


def test_every_secret_pattern_has_a_sample_here() -> None:
    """Sadzone sekrety wyprowadzamy z `SECRET_PATTERNS`, nie z pamięci.

    Porównujemy **obiekty wzorców**, a nie nazwy kończące się na `_RE`: wzorzec dopisany wprost
    do krotki albo nazwany inaczej nie zmieniłby zbioru nazw i test przeszedłby, mimo że
    scenariusz 7 przestałby go pilnować. To jest dokładnie to przeoczenie, któremu ten test ma
    zapobiegać, więc nie może zależeć od konwencji nazewniczej.
    """
    from ceidg_tool import config

    assert {getattr(config, name) for name in SECRET_SAMPLES} == set(config.SECRET_PATTERNS), (
        f"wzorce w SECRET_PATTERNS bez próbki albo próbki bez wzorca; "
        f"próbki: {sorted(SECRET_SAMPLES)}"
    )


def test_every_secret_shape_is_masked_in_every_channel_the_documents_claim() -> None:
    """Trzy kanały, którym ufa CLAUDE.md — i każdy naprawdę uruchomiony.

    Pierwsza wersja tego testu wołała `mask_tokens` trzy razy pod trzema nazwami, więc
    `MaskingFormatter` nie wykonywał się ani razu: wypatroszenie go do `super().format(record)`
    zostawiało test zielony. Formatter dostaje tu prawdziwy `LogRecord`.
    """
    from ceidg_tool import config

    formatter = MaskingFormatter("%(message)s")

    for name, sample in SECRET_SAMPLES.items():
        assert getattr(config, name).search(sample), f"próbka {name} nie pasuje do wzorca"

        assert sample not in mask_tokens(f"komunikat z {sample} w środku")
        assert sample not in safe(f"nazwa firmy {sample}").plain
        record = logging.LogRecord("t", logging.ERROR, __file__, 0, "klucz %s", (sample,), None)
        assert sample not in formatter.format(record)


def test_an_opaque_token_is_masked_by_value_even_though_no_pattern_matches() -> None:
    """Token CEIDG **nie musi** być JWT-em, a wtedy żaden wzorzec go nie łapie.

    `inspect_token` jawnie obsługuje token nieprzezroczysty (`is_jwt=False`) i `test_config.py`
    tę ścieżkę utrwala — więc dopóki maskowanie znało wyłącznie kształty, „maskujemy każdy
    sekret" znaczyło „maskujemy każdy sekret o znanym kształcie". Rejestr wartości zamyka tę
    różnicę: liczy się to, co proces trzyma, a nie to, jak wygląda.
    """
    from ceidg_tool.config import ANTHROPIC_KEY_RE, JWT_RE, register_secret

    opaque = "9f2c4e1a-nieprzezroczysty-token-bez-kropek"
    assert not JWT_RE.search(opaque) and not ANTHROPIC_KEY_RE.search(opaque)
    assert opaque in mask_tokens(f"przed rejestracją: {opaque}")

    register_secret(opaque)

    assert opaque not in mask_tokens(f"po rejestracji: {opaque}")
    assert opaque not in safe(f"nazwa firmy {opaque}").plain


def test_a_short_value_is_never_registered() -> None:
    """Sekret długości kilku znaków zamieniłby zwykły tekst w `<token>`.

    Maskowanie, które zjada komunikaty, jest gorsze od jego braku: operator przestaje czytać
    ekran, na którym połowa zdań to `<token>`.
    """
    from ceidg_tool.config import MIN_REGISTERED_SECRET, register_secret

    register_secret("abc")
    register_secret("")
    register_secret(None)

    assert mask_tokens("abc to zwykły tekst") == "abc to zwykły tekst"
    assert MIN_REGISTERED_SECRET >= 8
