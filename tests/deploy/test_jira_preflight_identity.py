"""Testy ``_check_identity`` z ``deploy/jira/preflight.py`` — sprawdzenia tożsamości w preflighcie.

Ten plik istnieje z powodu, który warto nazwać: preflight jest narzędziem WERYFIKACYJNYM
operatora, a do 2026-09-04 nie miał ani jednego testu i nie jest objęty ``mypy``
(``pyproject.toml``: ``files = ["src"]``). Kiedy ADR 0070 uczynił ``jira_user`` opcjonalnym,
jedyna linia sukcesu w ``_check_identity`` zaczęła meldować ``OK: rozwiązano na jira_user=''``
z kodem wyjścia ``0`` dla osoby, która narzędzia ``Jira`` nie dostanie w ogóle — a ``_mask("")``
zwraca pusty napis, więc nawet nie było widać, że pole jest puste.

**Fałszywa zieleń narzędzia weryfikacyjnego jest gorsza niż jego brak**: weryfikacja, która nie
umie odpowiedzieć „nie", nie jest weryfikacją. To ta sama klasa braku, co menedżer wykonawców
bez ``healthcheck`` — ``docker compose ps`` pokaże ``Up`` dla usługi, której nie zapytano.

Skrypt ładujemy leniwie, wzorem ``test_manage_tokens``, ale z jedną różnicą: ``deploy/jira/``
NIE wjeżdża do obrazu (``deploy/docker/Dockerfile`` kopiuje wyłącznie ``deploy/http/``), a
``tests/`` wjeżdża i jest bramką etapu ``test``. Ładowanie na poziomie modułu położyłoby więc
KOLEKCJĘ przy budowie obrazu floty, czyli zablokowało wydanie.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "jira" / "preflight.py"

pytestmark = pytest.mark.skipif(not _SCRIPT.is_file(), reason="deploy/jira/ nie wjeżdża do obrazu")


def _preflight() -> ModuleType:
    spec = importlib.util.spec_from_file_location("jira_preflight", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mapa(tmp_path: Path, tresc: str) -> Path:
    path = tmp_path / "identities.yaml"
    path.write_text(tresc, encoding="utf-8")
    return path


def test_unknown_aad_id_is_refused(tmp_path: Path) -> None:
    """Kontrola pozytywna dla fail-closed — ta gałąź działała i ma dalej działać."""
    mapa = _mapa(tmp_path, "EMP-1:\n  aad_user_id: aad-1\n  jira_user: a@example.com\n")

    assert _preflight()._check_identity("aad-obcy", mapa) is False


def test_member_with_a_jira_account_passes(tmp_path: Path) -> None:
    """Kontrola pozytywna: pełny wpis nadal przechodzi — inaczej naprawa byłaby regresem."""
    mapa = _mapa(tmp_path, "EMP-1:\n  aad_user_id: aad-1\n  jira_user: a@example.com\n")

    assert _preflight()._check_identity("aad-1", mapa) is True


def test_member_without_a_jira_account_is_refused_and_not_reported_as_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wpis „tylko Teams" NIE jest sukcesem preflightu drzwi Jiry (ADR 0070 §3).

    Dwie asercje, bo bronią dwóch różnych rzeczy. Kod wyjścia: ``--aad`` jest pytaniem „czy ta
    osoba dostanie Jirę", więc uczciwą odpowiedzią jest „nie". Treść: komunikat nie może wyglądać
    jak wezwanie do uzupełnienia mapy — dopisanie tam cudzego konta pokazałoby tej osobie CUDZE
    zadania, czyli dokładnie usterkę, przed którą ostrzega ADR 0070 §4. Fałszywa czerwień, która
    popycha operatora do „naprawy", byłaby gorsza od fałszywej zieleni, którą ten test zdejmuje.
    """
    mapa = _mapa(tmp_path, "EMP-51:\n  aad_user_id: aad-tadek\n  display_name: Tadeusz\n")

    assert _preflight()._check_identity("aad-tadek", mapa) is False

    komunikat = capsys.readouterr().err
    assert "jira_user=''" not in komunikat  # dawna fałszywa zieleń
    assert "NIE dopisuj" in komunikat  # i zakaz „naprawy", żeby czerwień nie myliła
