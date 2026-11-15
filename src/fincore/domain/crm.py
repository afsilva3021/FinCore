"""Regras de CRM: leads, funil de negociações, atividades, propostas e indicadores.

Tudo aqui é puro (sem banco nem framework). Os casos de uso em `application/crm.py`
buscam os dados, aplicam estas regras dentro de transações e gravam.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fincore.domain.money import MAX_AMOUNT, quantize_money

LEAD_STATUSES = frozenset({"new", "contacted", "qualified", "disqualified", "converted"})
LEAD_SOURCES = frozenset({"website", "referral", "cold_call", "event", "social", "other"})
DEAL_STATUSES = frozenset({"open", "won", "lost"})
ACTIVITY_TYPES = frozenset({"task", "call", "meeting", "email", "note"})
PROPOSAL_STATUSES = frozenset({"draft", "sent", "accepted", "rejected"})

# Etapas criadas para uma organização nova: (nome, probabilidade de ganho em %).
DEFAULT_STAGES: tuple[tuple[str, int], ...] = (
    ("Prospecção", 10),
    ("Qualificação", 25),
    ("Proposta", 50),
    ("Negociação", 75),
)
MAX_STAGES = 12
# Negociação aberta sem mudar de etapa há mais dias que isto é considerada parada.
STALLED_AFTER_DAYS = 14
MAX_PROPOSAL_ITEMS = 50
MAX_QUANTITY = Decimal("999999.999")
QUANTITY_STEP = Decimal("0.001")

# Transições manuais de lead. "converted" só é alcançado pela conversão.
_LEAD_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"contacted", "qualified", "disqualified"}),
    "contacted": frozenset({"qualified", "disqualified"}),
    "qualified": frozenset({"contacted", "disqualified"}),
    "disqualified": frozenset({"new"}),
    "converted": frozenset(),
}


# --- Leads ----------------------------------------------------------------------------


def ensure_lead_transition(current: str, target: str) -> None:
    if target not in LEAD_STATUSES or target == "converted":
        raise ValueError("Situação de lead inválida.")
    if current == "converted":
        raise ValueError("Lead convertido não pode mais ser alterado.")
    if target not in _LEAD_TRANSITIONS.get(current, frozenset()):
        raise ValueError("Esta mudança de situação do lead não é permitida.")


def ensure_lead_can_convert(status: str) -> None:
    if status == "converted":
        raise ValueError("Este lead já foi convertido.")
    if status == "disqualified":
        raise ValueError("Lead desqualificado não pode ser convertido; reabra-o antes.")


def qualification_score(*, need: bool, budget: bool, authority: bool, timeline: bool) -> int:
    """Quantos dos quatro critérios de qualificação (necessidade, orçamento, decisor, prazo)
    foram confirmados."""
    return sum((need, budget, authority, timeline))


# --- Funil e negociações --------------------------------------------------------------


def validate_probability(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("A probabilidade de ganho deve ficar entre 0 e 100.")
    return value


def validate_deal_amount(value: Decimal) -> Decimal:
    # Limite antes do arredondamento: quantize() de um número enorme lança InvalidOperation.
    if not value.is_finite() or value < 0 or value > MAX_AMOUNT:
        raise ValueError("O valor da negociação deve ser igual ou superior a zero.")
    return quantize_money(value)


def ensure_deal_open(status: str) -> None:
    if status != "open":
        raise ValueError("Somente negociações em aberto podem ser alteradas.")


def ensure_deal_can_reopen(status: str, has_accepted_proposal: bool) -> None:
    if status == "open":
        raise ValueError("Esta negociação já está em aberto.")
    if has_accepted_proposal:
        raise ValueError("Negociação com proposta aceita não pode ser reaberta.")


def validate_lost_reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > 240:
        raise ValueError("Informe o motivo da perda em até 240 caracteres.")
    return cleaned


def weighted_value(amount: Decimal, probability: int) -> Decimal:
    """Valor ponderado pela probabilidade de ganho da etapa (previsão de vendas)."""
    return quantize_money(amount * Decimal(probability) / Decimal(100))


def is_stalled(stage_changed_on: date, today: date, limit_days: int = STALLED_AFTER_DAYS) -> bool:
    return (today - stage_changed_on).days > limit_days


# --- Atividades -----------------------------------------------------------------------


def validate_activity(activity_type: str, subject: str, link_count: int) -> str:
    if activity_type not in ACTIVITY_TYPES:
        raise ValueError("Selecione um tipo de atividade válido.")
    cleaned = subject.strip()
    if not cleaned or len(cleaned) > 200:
        raise ValueError("Informe o assunto da atividade em até 200 caracteres.")
    if link_count != 1:
        raise ValueError("A atividade deve estar ligada a um lead, uma negociação ou um contato.")
    return cleaned


def activity_is_overdue(due_date: date | None, done: bool, today: date) -> bool:
    return not done and due_date is not None and due_date < today


# --- Propostas ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProposalLine:
    description: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class ProposalTotals:
    lines: tuple[ProposalLine, ...]
    subtotal: Decimal
    discount: Decimal
    total: Decimal


def calculate_proposal(
    items: Sequence[tuple[str, Decimal, Decimal]], discount: Decimal = Decimal("0.00")
) -> ProposalTotals:
    """Valida os itens e calcula os totais de uma proposta.

    Cada linha é arredondada para centavos (quantidade × preço unitário); o subtotal é a
    soma das linhas já arredondadas e o total é o subtotal menos o desconto.
    """
    if not items:
        raise ValueError("A proposta precisa de ao menos um item.")
    if len(items) > MAX_PROPOSAL_ITEMS:
        raise ValueError(f"A proposta aceita até {MAX_PROPOSAL_ITEMS} itens.")
    lines = []
    for description, quantity, unit_price in items:
        cleaned = description.strip()
        if not cleaned or len(cleaned) > 200:
            raise ValueError("Informe a descrição de cada item em até 200 caracteres.")
        if not quantity.is_finite() or quantity <= 0 or quantity > MAX_QUANTITY:
            raise ValueError("A quantidade de cada item deve ser maior que zero.")
        if quantity != quantity.quantize(QUANTITY_STEP):
            raise ValueError("A quantidade aceita até três casas decimais.")
        if not unit_price.is_finite() or unit_price <= 0 or unit_price > MAX_AMOUNT:
            raise ValueError("O preço unitário de cada item deve ser maior que zero.")
        price = quantize_money(unit_price)
        if price <= 0:
            raise ValueError("O preço unitário de cada item deve ser maior que zero.")
        line_total = quantize_money(quantity * price)
        if line_total <= 0 or line_total > MAX_AMOUNT:
            raise ValueError("O total do item está fora do limite permitido.")
        lines.append(ProposalLine(cleaned, quantity, price, line_total))
    subtotal = sum((line.total for line in lines), Decimal("0.00"))
    if not discount.is_finite() or discount < 0 or discount > MAX_AMOUNT:
        raise ValueError("O desconto deve ser um valor igual ou superior a zero.")
    discount = quantize_money(discount)
    if discount >= subtotal:
        raise ValueError("O desconto deve ser menor que o subtotal da proposta.")
    if subtotal > MAX_AMOUNT:
        raise ValueError("O total da proposta está fora do limite permitido.")
    return ProposalTotals(tuple(lines), subtotal, discount, quantize_money(subtotal - discount))


def proposal_is_expired(status: str, valid_until: date, today: date) -> bool:
    """Vencida é condição calculada: proposta ainda não decidida com validade no passado."""
    return status in {"draft", "sent"} and valid_until < today


def ensure_proposal_editable(status: str) -> None:
    if status != "draft":
        raise ValueError("Somente propostas em rascunho podem ser alteradas.")


def ensure_proposal_can_send(status: str, valid_until: date, today: date) -> None:
    if status != "draft":
        raise ValueError("Somente propostas em rascunho podem ser enviadas.")
    if valid_until < today:
        raise ValueError("A validade da proposta já passou; crie uma nova proposta.")


def ensure_proposal_can_accept(status: str, valid_until: date, today: date) -> None:
    if status == "accepted":
        raise ValueError("Esta proposta já foi aceita.")
    if status == "rejected":
        raise ValueError("Proposta recusada não pode ser aceita.")
    if status != "sent":
        raise ValueError("Envie a proposta ao cliente antes de registrar o aceite.")
    if valid_until < today:
        raise ValueError("A validade da proposta já passou; crie uma nova proposta.")


def ensure_proposal_can_reject(status: str) -> None:
    if status not in {"draft", "sent"}:
        raise ValueError("Somente propostas em rascunho ou enviadas podem ser recusadas.")


# --- Indicadores ----------------------------------------------------------------------


def percentage(part: int, whole: int) -> Decimal | None:
    """Percentual com uma casa decimal; None quando não há base para calcular."""
    if whole <= 0:
        return None
    return (Decimal(part) * Decimal(100) / Decimal(whole)).quantize(Decimal("0.1"))


def win_rate(won: int, lost: int) -> Decimal | None:
    """Taxa de ganho: ganhas ÷ negociações encerradas (ganhas + perdidas)."""
    return percentage(won, won + lost)


def average_cycle_days(durations: Iterable[int]) -> Decimal | None:
    """Ciclo médio de vendas, em dias, das negociações ganhas."""
    values = list(durations)
    if not values:
        return None
    return (Decimal(sum(values)) / Decimal(len(values))).quantize(Decimal("0.1"))
