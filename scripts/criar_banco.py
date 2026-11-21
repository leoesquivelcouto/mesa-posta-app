"""Cria o banco indicado em DATABASE_URL, se ainda não existir (útil no primeiro uso local).

Uso: python scripts/criar_banco.py && alembic upgrade head
"""

import psycopg
from psycopg import sql

from mesa.config import config


def main() -> None:
    url = config().database_url.replace("postgresql+psycopg://", "postgresql://")
    base, nome = url.rsplit("/", 1)
    nome = nome.split("?")[0]
    with psycopg.connect(f"{base}/postgres", autocommit=True) as con:
        if con.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nome,)).fetchone():
            print(f"banco {nome} já existe")
        else:
            con.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nome)))
            print(f"banco {nome} criado")


if __name__ == "__main__":
    main()
