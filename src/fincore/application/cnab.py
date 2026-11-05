"""Ponto de extensão para layouts CNAB explicitamente configurados."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CNABProfile:
    bank_code: str
    layout: str
    version: str


class CNABAdapter(Protocol):
    """Adapter de um banco/layout específico; não define compatibilidade universal."""

    profile: CNABProfile

    def parse(self, content: str) -> list[dict[str, str]]: ...


_adapters: dict[tuple[str, str, str], CNABAdapter] = {}


def register_cnab_adapter(adapter: CNABAdapter) -> None:
    key = (adapter.profile.bank_code, adapter.profile.layout, adapter.profile.version)
    _adapters[key] = adapter


def parse_cnab(content: str, profile: CNABProfile) -> list[dict[str, str]]:
    """Delegates only to an installed adapter for an exact profile."""
    adapter = _adapters.get((profile.bank_code, profile.layout, profile.version))
    if adapter is None:
        raise ValueError(
            "Layout CNAB não configurado. Informe banco, padrão 240/400 e versão do layout."
        )
    if not content.strip():
        raise ValueError("Arquivo CNAB vazio.")
    return adapter.parse(content)
