"""Usuário de banco com privilégio mínimo para a aplicação.

A aplicação em execução não precisa criar, alterar ou apagar tabelas: só ler e gravar
dados. Por isso o container da aplicação se conecta com um usuário restrito, e somente
o passo de migração usa o usuário dono do schema. Se a aplicação for comprometida, o
invasor não consegue apagar tabelas, alterar o schema nem criar outros usuários.
"""

import os
import re

import asyncpg

ROLE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def validate_role_settings(role: str, password: str, owner: str) -> None:
    if not ROLE_NAME_PATTERN.match(role):
        raise ValueError("APP_DB_USER deve ter só letras minúsculas, números e _.")
    if role == owner:
        raise ValueError("APP_DB_USER deve ser diferente do usuário dono do banco.")
    if len(password) < 16:
        raise ValueError("APP_DB_PASSWORD deve ter pelo menos 16 caracteres.")


async def ensure_app_role(dsn: str, role: str, password: str) -> None:
    """Cria/atualiza o usuário da aplicação e concede apenas DML no schema public.

    Idempotente: pode rodar a cada deploy, logo depois das migrations, inclusive em
    bancos que já existiam antes desta configuração.
    """
    connection = await asyncpg.connect(dsn)
    try:
        owner = await connection.fetchval("SELECT current_user")
        database = await connection.fetchval("SELECT current_database()")
        validate_role_settings(role, password, owner)
        async with connection.transaction():
            exists = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role
            )
            verb = "ALTER" if exists else "CREATE"
            # DDL não aceita parâmetros; format(%I, %L) faz o escape no próprio PostgreSQL.
            statement = await connection.fetchval(
                "SELECT format('%s ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS', $1::text, $2::text, "
                "$3::text)",
                verb,
                role,
                password,
            )
            await connection.execute(statement)
            grants = [
                "REVOKE ALL ON DATABASE {db} FROM PUBLIC",
                "GRANT CONNECT, TEMPORARY ON DATABASE {db} TO {role}",
                "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
                "GRANT USAGE ON SCHEMA public TO {role}",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
                "ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
                "ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
                "GRANT USAGE, SELECT ON SEQUENCES TO {role}",
            ]
            quote = connection.fetchval
            names = {
                "db": await quote("SELECT quote_ident($1::text)", database),
                "role": await quote("SELECT quote_ident($1::text)", role),
                "owner": await quote("SELECT quote_ident($1::text)", owner),
            }
            for grant in grants:
                await connection.execute(grant.format(**names))
            # O histórico de migrations só é tocado pelo passo de migração.
            if await connection.fetchval("SELECT to_regclass('public.tortoise_migrations')"):
                await connection.execute(
                    "REVOKE ALL ON TABLE public.tortoise_migrations FROM {role}".format(**names)
                )
    finally:
        await connection.close()


def settings_from_env() -> tuple[str, str]:
    role = os.getenv("APP_DB_USER", "")
    password = os.getenv("APP_DB_PASSWORD", "")
    if not role or not password:
        raise SystemExit("Defina APP_DB_USER e APP_DB_PASSWORD para o usuário da aplicação.")
    return role, password
