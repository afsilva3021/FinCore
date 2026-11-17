from decimal import Decimal as D

import pytest

from fincore.domain.crm import (
    DEFAULT_STAGES,
    clean_text,
    conversion_rate,
    ensure_lead_open,
    ensure_stage_move,
)


def test_default_pipeline_has_open_stages_then_won_and_lost() -> None:
    kinds = [kind for _, _, kind in DEFAULT_STAGES]
    assert kinds == ["open", "open", "open", "open", "won", "lost"]
    assert [position for _, position, _ in DEFAULT_STAGES] == [1, 2, 3, 4, 5, 6]


def test_stage_move_rules() -> None:
    ok = {"to_kind": "open", "has_customer": False, "lost_reason": None, "same_stage": False}
    ensure_stage_move(**ok)
    ensure_stage_move(**{**ok, "to_kind": "won", "has_customer": True})
    ensure_stage_move(**{**ok, "to_kind": "lost", "lost_reason": "Preço"})
    with pytest.raises(ValueError, match="já está"):
        ensure_stage_move(**{**ok, "same_stage": True})
    with pytest.raises(ValueError, match="Converta o lead"):
        ensure_stage_move(**{**ok, "to_kind": "won"})
    with pytest.raises(ValueError, match="motivo"):
        ensure_stage_move(**{**ok, "to_kind": "lost"})


def test_lead_must_be_open_to_convert_or_discard() -> None:
    ensure_lead_open("open")
    for status in ("converted", "discarded"):
        with pytest.raises(ValueError):
            ensure_lead_open(status)


def test_conversion_rate_counts_only_closed_opportunities() -> None:
    assert conversion_rate(0, 0) is None
    assert conversion_rate(1, 3) == D("25.0")
    assert conversion_rate(2, 1) == D("66.7")
    assert conversion_rate(4, 0) == D("100.0")


def test_clean_text() -> None:
    assert clean_text("  Maria   Silva ", "O nome", 160) == "Maria Silva"
    assert clean_text("   ", "A empresa", 160, required=False) is None
    with pytest.raises(ValueError, match="Informe o nome"):
        clean_text(" ", "O nome", 160)
    with pytest.raises(ValueError, match="no máximo 5"):
        clean_text("abcdef", "O nome", 5)
