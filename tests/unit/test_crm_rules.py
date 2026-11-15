"""Regras puras do CRM, testadas sem banco."""

from datetime import date
from decimal import Decimal

import pytest

from fincore.domain.crm import (
    activity_is_overdue,
    average_cycle_days,
    calculate_proposal,
    ensure_deal_can_reopen,
    ensure_deal_open,
    ensure_lead_can_convert,
    ensure_lead_transition,
    ensure_proposal_can_accept,
    ensure_proposal_can_reject,
    ensure_proposal_can_send,
    is_stalled,
    percentage,
    proposal_is_expired,
    qualification_score,
    validate_activity,
    validate_deal_amount,
    validate_lost_reason,
    validate_probability,
    weighted_value,
    win_rate,
)

TODAY = date(2026, 11, 15)


def test_lead_transitions_follow_the_allowed_flow() -> None:
    ensure_lead_transition("new", "contacted")
    ensure_lead_transition("contacted", "qualified")
    ensure_lead_transition("disqualified", "new")
    with pytest.raises(ValueError, match="não é permitida"):
        ensure_lead_transition("disqualified", "qualified")
    with pytest.raises(ValueError, match="inválida"):
        ensure_lead_transition("new", "converted")
    with pytest.raises(ValueError, match="convertido"):
        ensure_lead_transition("converted", "new")


def test_only_live_leads_can_be_converted() -> None:
    ensure_lead_can_convert("new")
    ensure_lead_can_convert("qualified")
    with pytest.raises(ValueError, match="já foi convertido"):
        ensure_lead_can_convert("converted")
    with pytest.raises(ValueError, match="desqualificado"):
        ensure_lead_can_convert("disqualified")


def test_qualification_score_counts_confirmed_criteria() -> None:
    assert qualification_score(need=True, budget=False, authority=True, timeline=False) == 2
    assert qualification_score(need=False, budget=False, authority=False, timeline=False) == 0


def test_weighted_value_rounds_half_up_and_never_uses_float() -> None:
    assert weighted_value(Decimal("1000.00"), 25) == Decimal("250.00")
    assert weighted_value(Decimal("0.05"), 50) == Decimal("0.03")
    assert weighted_value(Decimal("333.33"), 10) == Decimal("33.33")
    assert weighted_value(Decimal("100.00"), 0) == Decimal("0.00")


@pytest.mark.parametrize("value", [-1, 101, True, "50"])
def test_probability_must_be_an_integer_percentage(value) -> None:
    with pytest.raises(ValueError):
        validate_probability(value)


def test_deal_amount_accepts_zero_but_not_negative() -> None:
    assert validate_deal_amount(Decimal("0")) == Decimal("0.00")
    with pytest.raises(ValueError):
        validate_deal_amount(Decimal("-0.01"))


def test_closed_deals_are_immutable_and_reopen_has_rules() -> None:
    ensure_deal_open("open")
    with pytest.raises(ValueError, match="em aberto"):
        ensure_deal_open("won")
    ensure_deal_can_reopen("lost", has_accepted_proposal=False)
    with pytest.raises(ValueError, match="proposta aceita"):
        ensure_deal_can_reopen("won", has_accepted_proposal=True)
    with pytest.raises(ValueError, match="já está em aberto"):
        ensure_deal_can_reopen("open", has_accepted_proposal=False)


def test_lost_reason_is_required() -> None:
    assert validate_lost_reason("  Preço  ") == "Preço"
    with pytest.raises(ValueError):
        validate_lost_reason("   ")


def test_stalled_deal_is_calculated_from_the_stage_date() -> None:
    assert not is_stalled(date(2026, 11, 1), TODAY)  # exatamente 14 dias
    assert is_stalled(date(2026, 10, 31), TODAY)


def test_activity_needs_exactly_one_link_and_overdue_is_derived() -> None:
    assert validate_activity("call", " Ligar ", 1) == "Ligar"
    with pytest.raises(ValueError, match="ligada"):
        validate_activity("call", "Ligar", 0)
    with pytest.raises(ValueError, match="ligada"):
        validate_activity("call", "Ligar", 2)
    with pytest.raises(ValueError, match="tipo"):
        validate_activity("fax", "Ligar", 1)
    assert activity_is_overdue(date(2026, 11, 14), False, TODAY)
    assert not activity_is_overdue(date(2026, 11, 14), True, TODAY)
    assert not activity_is_overdue(None, False, TODAY)


def test_proposal_totals_round_each_line_and_subtract_the_discount() -> None:
    totals = calculate_proposal(
        [
            ("Consultoria", Decimal("1.5"), Decimal("333.33")),  # 499.995 -> 500.00
            ("Entrega", Decimal("1"), Decimal("50.05")),
        ],
        Decimal("50.05"),
    )
    assert [line.total for line in totals.lines] == [Decimal("500.00"), Decimal("50.05")]
    assert totals.subtotal == Decimal("550.05")
    assert totals.total == Decimal("500.00")


@pytest.mark.parametrize(
    ("items", "discount", "message"),
    [
        ([], "0", "ao menos um item"),
        ([("", Decimal("1"), Decimal("10"))], "0", "descrição"),
        ([("Item", Decimal("0"), Decimal("10"))], "0", "quantidade"),
        ([("Item", Decimal("1.0005"), Decimal("10"))], "0", "três casas"),
        ([("Item", Decimal("1"), Decimal("0"))], "0", "preço unitário"),
        ([("Item", Decimal("1"), Decimal("10"))], "10", "menor que o subtotal"),
        ([("Item", Decimal("1"), Decimal("10"))], "-1", "igual ou superior a zero"),
    ],
)
def test_invalid_proposals_are_rejected(items, discount, message) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_proposal(items, Decimal(discount))


def test_proposal_state_machine() -> None:
    valid = date(2026, 11, 20)
    past = date(2026, 11, 14)
    ensure_proposal_can_send("draft", valid, TODAY)
    with pytest.raises(ValueError, match="rascunho"):
        ensure_proposal_can_send("sent", valid, TODAY)
    ensure_proposal_can_accept("sent", valid, TODAY)
    ensure_proposal_can_accept("sent", TODAY, TODAY)  # vale até o fim do dia da validade
    with pytest.raises(ValueError, match="Envie a proposta"):
        ensure_proposal_can_accept("draft", valid, TODAY)
    with pytest.raises(ValueError, match="validade"):
        ensure_proposal_can_accept("sent", past, TODAY)
    with pytest.raises(ValueError, match="recusada"):
        ensure_proposal_can_accept("rejected", valid, TODAY)
    ensure_proposal_can_reject("sent")
    with pytest.raises(ValueError):
        ensure_proposal_can_reject("accepted")
    assert proposal_is_expired("sent", past, TODAY)
    assert not proposal_is_expired("accepted", past, TODAY)


def test_indicators_handle_empty_bases() -> None:
    assert percentage(1, 0) is None
    assert percentage(1, 3) == Decimal("33.3")
    assert win_rate(0, 0) is None
    assert win_rate(3, 1) == Decimal("75.0")
    assert average_cycle_days([]) is None
    assert average_cycle_days([10, 15]) == Decimal("12.5")
