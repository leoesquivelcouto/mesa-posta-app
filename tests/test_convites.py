from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from mesa.db import fabrica
from mesa.modelos import Convidado
from mesa.servicos import extrair_token


def _gerar(client, cabecalho, evento, **params):
    r = client.post(f"/eventos/{evento}/convites", params=params, headers=cabecalho)
    assert r.status_code == 200, r.text
    return r


def _token(convite: dict) -> str:
    return extrair_token(convite["link"])


def test_gerar_convites_guarda_so_o_hash(client, cerimonial, evento, convidado):
    convidado("Ana", telefone="61999998888")
    convidado("Bruno")
    convites = _gerar(client, cerimonial, evento).json()
    assert [c["nome"] for c in convites] == ["Ana", "Bruno"]
    assert convites[0]["link"].startswith("https://mesaposta.exemplo/c/")

    token = _token(convites[0])
    assert len(token) >= 43  # 256 bits em base64
    with fabrica()() as s:
        hashes = s.scalars(select(Convidado.token_hash)).all()
    assert token not in hashes
    assert all(h and len(h) == 64 for h in hashes)

    # Sem regenerar, só quem ainda não tem convite recebe um link novo.
    convidado("Carla")
    assert [c["nome"] for c in _gerar(client, cerimonial, evento).json()] == ["Carla"]


def test_regenerar_invalida_links_antigos(client, cerimonial, evento, convidado):
    convidado("Ana")
    antigo = _token(_gerar(client, cerimonial, evento).json()[0])
    novo = _token(_gerar(client, cerimonial, evento, regenerar=True).json()[0])
    assert antigo != novo
    assert client.get(f"/convite/{antigo}").status_code == 404
    assert client.get(f"/convite/{novo}").status_code == 200


def test_convites_em_csv_para_o_excel(client, cerimonial, evento, convidado):
    convidado("Ana", telefone="61999998888")
    r = _gerar(client, cerimonial, evento, formato="csv")
    assert r.headers["content-type"].startswith("text/csv")
    linhas = r.content.decode("utf-8-sig").splitlines()
    assert linhas[0] == "nome;telefone;link"
    assert linhas[1].startswith("Ana;61999998888;https://mesaposta.exemplo/c/")


def test_convidado_confirma_pelo_link(client, cerimonial, evento, convidado):
    cid = convidado("Ana")
    token = _token(_gerar(client, cerimonial, evento).json()[0])

    r = client.get(f"/convite/{token}")
    assert r.status_code == 200
    assert r.json()["convidado"] == "Ana"
    assert r.json()["evento"] == "Casamento de Bia e Caio"
    assert r.json()["resposta"] == "pendente"

    r = client.post(
        f"/convite/{token}", json={"resposta": "confirmado", "restricao_alimentar": " sem glúten "}
    )
    assert r.status_code == 200
    assert (r.json()["resposta"], r.json()["restricao_alimentar"]) == ("confirmado", "sem glúten")

    ana = client.get(f"/eventos/{evento}/convidados/{cid}", headers=cerimonial).json()
    assert ana["resposta"] == "confirmado"
    assert ana["respondido_em"] is not None

    # A página pública não expõe dados de outros convidados nem do evento além do necessário.
    assert set(client.get(f"/convite/{token}").json()) == {
        "evento",
        "data",
        "local",
        "prazo_rsvp",
        "prazo_encerrado",
        "convidado",
        "resposta",
        "restricao_alimentar",
    }


def test_recusar_pelo_link_libera_a_mesa(client, cerimonial, evento, convidado, mesa):
    cid, m = convidado("Ana"), mesa("Mesa 1", 1)
    client.put(f"/eventos/{evento}/convidados/{cid}/mesa", json={"mesa_id": m}, headers=cerimonial)
    token = _token(_gerar(client, cerimonial, evento).json()[0])
    client.post(f"/convite/{token}", json={"resposta": "recusado"})
    assert client.get(f"/eventos/{evento}/convidados/{cid}", headers=cerimonial).json()["mesa_id"] is None


def test_prazo_encerrado(client, cerimonial, evento, convidado):
    convidado("Ana")
    token = _token(_gerar(client, cerimonial, evento).json()[0])
    ontem = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    client.patch(f"/eventos/{evento}", json={"prazo_rsvp": ontem}, headers=cerimonial)

    assert client.get(f"/convite/{token}").json()["prazo_encerrado"] is True
    r = client.post(f"/convite/{token}", json={"resposta": "confirmado"})
    assert r.status_code == 410
    assert r.json()["erro"]["codigo"] == "prazo_encerrado"


def test_token_invalido_e_resposta_invalida(client, cerimonial, evento, convidado):
    assert client.get("/convite/inexistente").status_code == 404
    convidado("Ana")
    token = _token(_gerar(client, cerimonial, evento).json()[0])
    assert client.post(f"/convite/{token}", json={"resposta": "pendente"}).status_code == 422


def test_qr_code(client, cerimonial, evento, convidado):
    convidado("Ana")
    token = _token(_gerar(client, cerimonial, evento).json()[0])
    r = client.get(f"/convite/{token}/qr.svg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in r.content
    assert client.get("/convite/inexistente/qr.svg").status_code == 404


def test_token_nao_aparece_no_log(client, cerimonial, evento, convidado, caplog):
    convidado("Ana")
    token = _token(_gerar(client, cerimonial, evento).json()[0])
    with caplog.at_level("INFO", logger="mesa"):
        client.get(f"/convite/{token}")
    assert "GET /api/convite/*** 200" in caplog.text
    assert token not in caplog.text
