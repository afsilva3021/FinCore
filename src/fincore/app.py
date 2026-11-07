"""Inicialização HTTP do FinCore."""

import json
import logging
from pathlib import Path

from robyn import Response, Robyn
from tortoise import Tortoise

from fincore.config.database import TORTOISE_ORM
from fincore.config.settings import settings, startup_problems
from fincore.presentation.health import database_is_ready
from fincore.presentation.security_headers import apply_security_headers
from fincore.presentation.web import (
    enforce_write_permission,
    register_accounting_routes,
    register_api_routes,
    register_counterparty_routes,
    register_demo_integration_routes,
    register_finance_routes,
    register_member_routes,
    register_reconciliation_routes,
    register_report_routes,
    register_web_routes,
)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("fincore")
app = Robyn(__file__)
app.serve_directory(route="/static", directory_path=str(Path(__file__).parents[2] / "static"))


app.before_request()(enforce_write_permission)


@app.after_request()
def security_headers(response: Response) -> Response:
    return apply_security_headers(response, https=settings.secure_cookies)


@app.startup_handler
async def startup() -> None:
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    logger.info("FinCore iniciado")


@app.shutdown_handler
async def shutdown() -> None:
    await Tortoise.close_connections()
    logger.info("FinCore encerrado")


@app.get("/health")
async def health() -> Response:
    if await database_is_ready():
        body = {"status": "ok", "database": "connected"}
        status_code = 200
    else:
        body = {"status": "unavailable", "database": "disconnected"}
        status_code = 503
    return Response(
        status_code=status_code,
        headers={"Content-Type": "application/json"},
        body=json.dumps(body),
    )


register_web_routes(app)
register_counterparty_routes(app)
register_member_routes(app)
register_finance_routes(app)
register_accounting_routes(app)
register_reconciliation_routes(app)
register_report_routes(app)
register_demo_integration_routes(app)
register_api_routes(app)


def main() -> None:
    problems = startup_problems(settings)
    if problems:
        raise SystemExit("Configuração insegura: " + " ".join(problems))
    app.start(host=settings.host, port=settings.port)
