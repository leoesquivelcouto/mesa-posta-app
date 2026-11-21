"""Os testes rodam contra um PostgreSQL de verdade (TEST_DATABASE_URL), com o esquema das migrações."""

import os
from pathlib import Path

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
if not URL:
    pytest.exit("Defina TEST_DATABASE_URL apontando para um banco PostgreSQL descartável.", returncode=2)
os.environ["DATABASE_URL"] = URL
os.environ.setdefault("SEGREDO_JWT", "segredo-so-para-testes-" + "x" * 32)
os.environ["URL_PUBLICA"] = "https://mesaposta.exemplo"
os.environ["BCRYPT_CUSTO"] = "4"

from alembic import command  # noqa: E402
from alembic.config import Config as ConfigAlembic  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from mesa.app import app  # noqa: E402
from mesa.db import engine  # noqa: E402

RAIZ = Path(__file__).parents[1]
SENHA = "senha-segura-123"


@pytest.fixture(scope="session", autouse=True)
def esquema():
    with engine().begin() as con:
        con.execute(text("DROP SCHEMA public CASCADE"))
        con.execute(text("CREATE SCHEMA public"))
    cfg = ConfigAlembic(str(RAIZ / "alembic.ini"))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")  # a migração de volta também precisa funcionar
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def banco_limpo():
    with engine().begin() as con:
        con.execute(text("TRUNCATE usuario, evento RESTART IDENTITY CASCADE"))


@pytest.fixture
def client() -> TestClient:
    """Cliente da API JSON (tudo sob /api)."""
    return TestClient(app, base_url="http://testserver/api")


@pytest.fixture
def nova_conta(client):
    """Cria uma conta, faz login e devolve o cabeçalho de autenticação."""

    def criar(email: str, nome: str = "Pessoa Teste") -> dict[str, str]:
        r = client.post("/auth/cadastro", json={"nome": nome, "email": email, "senha": SENHA})
        assert r.status_code == 201, r.text
        r = client.post("/auth/login", data={"username": email, "password": SENHA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return criar


@pytest.fixture
def cerimonial(nova_conta):
    return nova_conta("ana@cerimonial.com", "Ana Cerimonialista")


@pytest.fixture
def evento(client, cerimonial) -> int:
    r = client.post(
        "/eventos",
        json={"nome": "Casamento de Bia e Caio", "data": "2027-05-15", "local": "Chácara Recanto"},
        headers=cerimonial,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def equipe(client, nova_conta, cerimonial, evento) -> dict[str, dict[str, str]]:
    """Cerimonial, noivos e recepção no evento, e alguém de fora."""
    noivos = nova_conta("bia@noivos.com", "Bia")
    recepcao = nova_conta("rui@recepcao.com", "Rui")
    for email, papel in [("bia@noivos.com", "noivos"), ("rui@recepcao.com", "recepcao")]:
        r = client.put(
            f"/eventos/{evento}/membros", json={"email": email, "papel": papel}, headers=cerimonial
        )
        assert r.status_code == 200, r.text
    return {
        "cerimonial": cerimonial,
        "noivos": noivos,
        "recepcao": recepcao,
        "estranho": nova_conta("zeca@outro.com", "Zeca"),
    }


@pytest.fixture
def convidado(client, cerimonial, evento):
    def criar(nome: str, **extra) -> int:
        r = client.post(f"/eventos/{evento}/convidados", json={"nome": nome, **extra}, headers=cerimonial)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return criar


@pytest.fixture
def mesa(client, cerimonial, evento):
    def criar(nome: str, capacidade: int) -> int:
        r = client.post(
            f"/eventos/{evento}/mesas", json={"nome": nome, "capacidade": capacidade}, headers=cerimonial
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return criar
