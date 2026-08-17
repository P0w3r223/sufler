"""Dowód zgody: powstaje wyłącznie w bramce i jest wymagany na granicy odczytu."""

from __future__ import annotations

import pytest

from claude_summary.core.consent import (
    ConsentError,
    ConsentProof,
    grant_consent,
    require_consent,
)


def test_gate_returns_proof_for_flag_or_env() -> None:
    assert isinstance(grant_consent(flag=True, env_consent=False), ConsentProof)
    assert isinstance(grant_consent(flag=False, env_consent=True), ConsentProof)
    assert grant_consent(flag=False, env_consent=False) is None


def test_proof_cannot_be_built_beside_the_gate() -> None:
    with pytest.raises(ConsentError):
        ConsentProof()
    with pytest.raises(ConsentError):
        ConsentProof("cokolwiek")


def test_require_consent_rejects_substitutes() -> None:
    for substitute in (None, True, "tak", object()):
        with pytest.raises(ConsentError):
            require_consent(substitute)
    require_consent(grant_consent(flag=True, env_consent=False))
