"""Numeração sequencial de documentos por organização, sem buracos nem repetição."""

from uuid import uuid4

from tortoise import connections

from fincore.infrastructure.models import DocumentSequence, Organization


async def next_document_number(organization: Organization, kind: str) -> int:
    """Próximo número do tipo de documento. Chame dentro da transação do documento.

    A linha do contador fica bloqueada até a transação terminar: se o documento não for
    gravado, o número volta junto, e dois documentos simultâneos nunca recebem o mesmo.
    """
    await connections.get("default").execute_query(
        'INSERT INTO "documentsequence" (id, organization_id, kind, last_number) '
        "VALUES ($1, $2, $3, 0) ON CONFLICT (organization_id, kind) DO NOTHING",
        [uuid4(), organization.id, kind],
    )
    sequence = (
        await DocumentSequence.filter(organization=organization, kind=kind)
        .select_for_update()
        .get()
    )
    sequence.last_number += 1
    await sequence.save(update_fields=["last_number"])
    return sequence.last_number
