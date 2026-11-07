"""Rotas: Relatórios e exportação CSV/PDF."""

import csv
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from robyn import Request, Response

from fincore.application.reports import transaction_report
from fincore.presentation.web import (
    CurrentUser,
    _csrf_from_auth,
    _page,
    _response,
    login_required,
)


def register_report_routes(app: object) -> None:
    def report_dates(request: Request) -> tuple[date, date]:
        query = request.query_params
        today = date.today()
        default_start = today.replace(day=1)
        try:
            start = date.fromisoformat(query.get("start", default_start.isoformat()))
            end = date.fromisoformat(query.get("end", today.isoformat()))
        except ValueError as exc:
            raise ValueError("Informe um período válido.") from exc
        return start, end

    @app.get("/reports")
    @login_required
    async def reports_page(request: Request, current: CurrentUser) -> Response:
        user, membership = current
        organization = await membership.organization
        try:
            start, end = report_dates(request)
            movements, income, expenses = await transaction_report(organization, start, end)
        except ValueError as exc:
            return _response(
                _page(
                    "finance/reports.html",
                    name=user.full_name,
                    organization=organization,
                    csrf=_csrf_from_auth(request),
                    error=str(exc),
                    start=date.today().replace(day=1).isoformat(),
                    end=date.today().isoformat(),
                    movements=[],
                    income=Decimal("0"),
                    expenses=Decimal("0"),
                )
            )
        return _response(
            _page(
                "finance/reports.html",
                name=user.full_name,
                organization=organization,
                csrf=_csrf_from_auth(request),
                error=None,
                start=start.isoformat(),
                end=end.isoformat(),
                movements=movements,
                income=income,
                expenses=expenses,
            )
        )

    @app.get("/reports.csv")
    @login_required
    async def reports_csv(request: Request, current: CurrentUser) -> Response:
        try:
            start, end = report_dates(request)
            organization = await current[1].organization
            movements, _, _ = await transaction_report(organization, start, end)
        except ValueError as exc:
            return _response(str(exc), 400)
        output = StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(
            [
                "Data competência",
                "Movimentação",
                "Descrição",
                "Conta",
                "Categoria",
                "Status",
                "Valor BRL",
            ]
        )
        for item in movements:
            signed = (
                item.amount if item.transaction_type in {"income", "transfer_in"} else -item.amount
            )
            writer.writerow(
                [
                    item.competence_date.isoformat(),
                    item.transaction_type,
                    item.description,
                    item.account.name,
                    item.category.name if item.category else "",
                    item.status,
                    f"{signed:.2f}",
                ]
            )
        return Response(
            status_code=200,
            headers={
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": f'attachment; filename="fincore-{start}-{end}.csv"',
            },
            body="\ufeff" + output.getvalue(),
        )

    @app.get("/reports.pdf")
    @login_required
    async def reports_pdf(request: Request, current: CurrentUser) -> Response:
        try:
            start, end = report_dates(request)
            organization = await current[1].organization
            movements, income, expenses = await transaction_report(organization, start, end)
        except ValueError as exc:
            return _response(str(exc), 400)
        buffer = BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=landscape(A4), title="Relatório FinCore")
        styles = getSampleStyleSheet()
        summary = (
            f"Receitas realizadas: R$ {income:.2f} · Despesas realizadas: R$ {expenses:.2f} "
            f"· Saldo do período: R$ {income - expenses:.2f}"
        )
        story = [
            Paragraph("FinCore · Relatório financeiro", styles["Title"]),
            Paragraph(
                f"{escape(organization.name)} · {start:%d/%m/%Y} a {end:%d/%m/%Y}",
                styles["Normal"],
            ),
            Spacer(1, 12),
            Paragraph(summary, styles["Normal"]),
            Spacer(1, 12),
        ]
        rows = [["Data", "Tipo", "Descrição", "Conta", "Categoria", "Estado", "Valor BRL"]]
        for item in movements:
            signed = (
                item.amount if item.transaction_type in {"income", "transfer_in"} else -item.amount
            )
            rows.append(
                [
                    item.competence_date.strftime("%d/%m/%Y"),
                    item.transaction_type,
                    item.description[:50],
                    item.account.name[:24],
                    item.category.name[:24] if item.category else "—",
                    item.status,
                    f"{signed:.2f}",
                ]
            )
        table = Table(rows, repeatRows=1, colWidths=[65, 65, 185, 110, 110, 55, 75])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#102a43")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("PADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(table)
        document.build(story)
        return Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": f'attachment; filename="fincore-{start}-{end}.pdf"',
            },
            body=buffer.getvalue(),
        )
