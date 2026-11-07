"""Registro das rotas HTTP, um módulo por área do sistema."""

from fincore.presentation.routes.accounting import register_accounting_routes
from fincore.presentation.routes.api import register_api_routes
from fincore.presentation.routes.api_titles import register_title_api_routes
from fincore.presentation.routes.auth import register_web_routes
from fincore.presentation.routes.contacts import register_counterparty_routes
from fincore.presentation.routes.finance import register_finance_routes
from fincore.presentation.routes.integrations import register_demo_integration_routes
from fincore.presentation.routes.members import register_member_routes
from fincore.presentation.routes.reconciliation import register_reconciliation_routes
from fincore.presentation.routes.reports import register_report_routes

__all__ = [
    "register_web_routes",
    "register_finance_routes",
    "register_accounting_routes",
    "register_reconciliation_routes",
    "register_report_routes",
    "register_demo_integration_routes",
    "register_api_routes",
    "register_title_api_routes",
    "register_counterparty_routes",
    "register_member_routes",
]
