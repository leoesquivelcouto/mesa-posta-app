import jwt

from mesa.seguranca import ALGORITMO
from tests.conftest import SENHA


def test_cadastro_login_e_eu(client, nova_conta):
    cabecalho = nova_conta("Leo@Exemplo.com", "Leo")
    r = client.get("/auth/eu", headers=cabecalho)
    assert r.status_code == 200
    assert r.json()["email"] == "leo@exemplo.com"  # e-mail guardado em minúsculas
    assert "senha_hash" not in r.json()


def test_email_repetido(client, nova_conta):
    nova_conta("leo@exemplo.com")
    r = client.post("/auth/cadastro", json={"nome": "Outro", "email": "LEO@exemplo.com", "senha": SENHA})
    assert r.status_code == 409
    assert r.json()["erro"]["codigo"] == "email_em_uso"


def test_senha_errada_e_email_inexistente_dao_a_mesma_resposta(client, nova_conta):
    nova_conta("leo@exemplo.com")
    errada = client.post("/auth/login", data={"username": "leo@exemplo.com", "password": "outra-senha"})
    inexistente = client.post("/auth/login", data={"username": "ninguem@exemplo.com", "password": SENHA})
    assert errada.status_code == inexistente.status_code == 401
    assert errada.json() == inexistente.json()


def test_validacao_no_formato_padrao(client):
    r = client.post("/auth/cadastro", json={"nome": "L", "email": "nao-e-email", "senha": "curta"})
    assert r.status_code == 422
    erro = r.json()["erro"]
    assert erro["codigo"] == "dados_invalidos"
    assert {d["campo"] for d in erro["detalhes"]} == {"nome", "email", "senha"}


def test_senha_acima_de_72_bytes_e_recusada(client):
    r = client.post("/auth/cadastro", json={"nome": "Leo", "email": "leo@exemplo.com", "senha": "ç" * 40})
    assert r.status_code == 422


def test_sem_token_token_invalido_e_token_adulterado(client, nova_conta):
    assert client.get("/auth/eu").status_code == 401
    assert client.get("/auth/eu", headers={"Authorization": "Bearer lixo"}).status_code == 401

    cabecalho = nova_conta("leo@exemplo.com")
    token = cabecalho["Authorization"].split()[1]
    dados = jwt.decode(token, options={"verify_signature": False})
    forjado = jwt.encode({**dados, "sub": "999"}, "segredo-de-quem-ataca-" + "y" * 32, algorithm=ALGORITMO)
    r = client.get("/auth/eu", headers={"Authorization": f"Bearer {forjado}"})
    assert r.status_code == 401
    assert r.json()["erro"]["codigo"] == "nao_autenticado"


def test_saude(client):
    assert client.get("http://testserver/saude").json()["status"] == "ok"
