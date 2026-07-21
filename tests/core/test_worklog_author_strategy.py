"""Testy szwu autorstwa wpisu czasu (ADR 0034).

Sedno: ``SelfAuthorStrategy`` NIE zmienia autora w Jirze — dokłada tylko adnotację w treści.
Testy mają tę stratność przypiąć, żeby nikt nie „poprawił" jej na cichą obietnicę atrybucji.
Konta jak w środowisku BIAP: Piotr to właściciel tokenu, Mikołaj to druga osoba.
"""

from __future__ import annotations

import pytest

from workmate.core.application.worklog_author import (
    MODE_SELF,
    PerUserTokenStrategy,
    SelfAuthorStrategy,
    TempoWorklogStrategy,
    build_author_strategy,
)

_PIOTR = "712020:c0ffee00-0000-4000-8000-000000000008"
_MIKOLAJ = "712020:c0ffee00-0000-4000-8000-000000000018"


def test_self_strategy_without_target_adds_no_prefix() -> None:
    plan = SelfAuthorStrategy(_PIOTR).plan()
    assert plan.comment_prefix == ""
    assert plan.annotated is False
    assert plan.effective_author == _PIOTR


def test_self_strategy_treats_own_account_as_own_time() -> None:
    """„w imieniu: ja" byłoby szumem w każdym wpisie — nie dokładamy go."""
    plan = SelfAuthorStrategy(_PIOTR).plan(on_behalf_of=_PIOTR, display_name="Piotr")
    assert plan.annotated is False
    assert plan.on_behalf_of == ""


def test_self_strategy_annotates_cross_user_entry() -> None:
    plan = SelfAuthorStrategy(_PIOTR).plan(on_behalf_of=_MIKOLAJ, display_name="Mikołaj")
    assert plan.comment_prefix.startswith("w imieniu: Mikołaj")
    assert plan.annotated is True
    assert plan.on_behalf_of == _MIKOLAJ


def test_self_strategy_reports_honest_effective_author() -> None:
    """NAJWAŻNIEJSZY test modułu: autorem w Jirze zostaje Piotr, mimo prośby o Mikołaja."""
    plan = SelfAuthorStrategy(_PIOTR).plan(on_behalf_of=_MIKOLAJ, display_name="Mikołaj")
    assert plan.effective_author == _PIOTR
    assert plan.effective_author != plan.on_behalf_of


def test_self_strategy_falls_back_to_account_id_without_display_name() -> None:
    plan = SelfAuthorStrategy(_PIOTR).plan(on_behalf_of=_MIKOLAJ)
    assert _MIKOLAJ in plan.comment_prefix


def test_per_user_token_strategy_is_a_documented_slot() -> None:
    with pytest.raises(NotImplementedError, match="per_user_token"):
        PerUserTokenStrategy().plan(on_behalf_of=_MIKOLAJ)


def test_tempo_strategy_is_a_documented_slot() -> None:
    with pytest.raises(NotImplementedError, match="tempo"):
        TempoWorklogStrategy().plan(on_behalf_of=_MIKOLAJ)


def test_factory_builds_implemented_strategy() -> None:
    strategy = build_author_strategy(MODE_SELF, self_account=_PIOTR)
    assert strategy.name == MODE_SELF


def test_factory_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="nieznana strategia"):
        build_author_strategy("magia", self_account=_PIOTR)
