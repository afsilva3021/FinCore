"""CRM: etapas do funil, regras de movimentação e indicadores."""

from decimal import Decimal

OPEN = "open"
WON = "won"
LOST = "lost"
STAGE_KINDS = frozenset({OPEN, WON, LOST})
# (nome, posição, tipo) das etapas criadas com a organização.
DEFAULT_STAGES: tuple[tuple[str, int, str], ...] = (
    ("Novo", 1, OPEN),
    ("Qualificado", 2, OPEN),
    ("Proposta", 3, OPEN),
    ("Negociação", 4, OPEN),
    ("Ganho", 5, WON),
    ("Perdido", 6, LOST),
)
LEAD_STATUSES = frozenset({"open", "converted", "discarded"})
LEAD_STATUS_LABELS = {"open": "Em aberto", "converted": "Convertido", "discarded": "Descartado"}
ACTIVITY_KINDS = {"task": "Tarefa", "call": "Ligação", "meeting": "Reunião", "note": "Nota"}


def clean_text(value: str, label: str, limit: int, *, required: bool = True) -> str | None:
    text = " ".join(value.split())
    if len(text) > limit:
        raise ValueError(f"{label} deve ter no máximo {limit} caracteres.")
    if not text:
        if required:
            raise ValueError(f"Informe {label[0].lower()}{label[1:]}.")
        return None
    return text


def ensure_stage_move(
    *, to_kind: str, has_customer: bool, lost_reason: str | None, same_stage: bool
) -> None:
    """Regras para mudar a etapa de uma oportunidade."""
    if same_stage:
        raise ValueError("A oportunidade já está nesta etapa.")
    if to_kind == WON and not has_customer:
        raise ValueError("Converta o lead em cliente antes de marcar a oportunidade como ganha.")
    if to_kind == LOST and not lost_reason:
        raise ValueError("Informe o motivo da perda.")


def ensure_lead_open(status: str) -> None:
    if status != "open":
        raise ValueError("Este lead já foi convertido ou descartado.")


def conversion_rate(won: int, lost: int) -> Decimal | None:
    """Ganhas sobre encerradas (ganhas + perdidas), em % com uma casa; None sem encerradas."""
    closed = won + lost
    if closed == 0:
        return None
    return (Decimal(won) * 100 / closed).quantize(Decimal("0.1"))
