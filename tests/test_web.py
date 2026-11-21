"""Telas: o fluxo inteiro como uma pessoa usaria, e as proteções (login, CSRF, papéis, redirecionamento)."""

import re

import pytest
from fastapi.testclient import TestClient

from mesa.app import app
from tests.conftest import SENHA


class Navegador:
    """TestClient com cookies, que envia o token CSRF da visita em todo formulário."""

    def __init__(self):
        # HTTPS: com URL_PUBLICA em https, os cookies saem com a flag Secure e só voltam por HTTPS.
        self.c = TestClient(app, base_url="https://testserver")
        self.c.get("/")  # recebe o cookie CSRF

    @property
    def csrf(self) -> str:
        return self.c.cookies["mp_csrf"]

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, dados=None, **kw):
        return self.c.post(url, data={"csrf": self.csrf, **(dados or {})}, **kw)

    def cadastrar(self, email: str, nome: str = "Pessoa Teste"):
        r = self.post("/cadastro", {"nome": nome, "email": email, "senha": SENHA})
        assert r.status_code == 200, r.text
        return r


@pytest.fixture
def ana() -> Navegador:
    n = Navegador()
    n.cadastrar("ana@cerimonial.com", "Ana Cerimonialista")
    return n


def criar_evento(n: Navegador, nome="Casamento de Bia e Caio") -> int:
    r = n.post(
        "/eventos", {"nome": nome, "data": "2027-05-15", "local": "Chácara Recanto", "prazo": "2027-05-01"}
    )
    assert r.status_code == 200 and "Evento criado" in r.text
    return int(re.search(r"/eventos/(\d+)$", str(r.url)).group(1))


def test_inicio_cadastro_saida_e_login():
    n = Navegador()
    assert "Criar conta grátis" in n.get("/").text
    r = n.cadastrar("leo@exemplo.com", "Leo")
    assert str(r.url).endswith("/eventos")
    assert "Bem-vindo(a), Leo!" in r.text and "Nenhum evento ainda" in r.text

    assert "Você saiu" in n.post("/sair").text
    errado = n.post("/entrar", {"email": "leo@exemplo.com", "senha": "senha-errada"})
    assert errado.status_code == 401 and "E-mail ou senha incorretos" in errado.text
    certo = n.post("/entrar", {"email": "LEO@exemplo.com", "senha": SENHA})
    assert str(certo.url).endswith("/eventos")


def test_cadastro_invalido_e_repetido_mostra_mensagens_em_portugues():
    n = Navegador()
    r = n.post("/cadastro", {"nome": "L", "email": "nao-e-email", "senha": "curta"})
    assert r.status_code == 422
    assert "Nome: precisa ter pelo menos 2 caracteres" in r.text
    assert "não parece um e-mail válido" in r.text
    n.cadastrar("leo@exemplo.com")
    r = Navegador().post("/cadastro", {"nome": "Outro", "email": "leo@exemplo.com", "senha": SENHA})
    assert r.status_code == 409 and "Já existe uma conta" in r.text


def test_cookie_de_sessao_protegido():
    n = Navegador()
    r = n.post(
        "/cadastro", {"nome": "Leo", "email": "leo@exemplo.com", "senha": SENHA}, follow_redirects=False
    )
    cabecalho = r.headers["set-cookie"].lower()
    assert "mp_sessao=" in cabecalho and "httponly" in cabecalho and "samesite=lax" in cabecalho


def test_formulario_sem_csrf_e_recusado(ana):
    r = ana.c.post("/eventos", data={"nome": "Festa", "data": "2027-01-01"})
    assert r.status_code == 403 and "Formulário expirado" in r.text
    r = ana.c.post("/eventos", data={"nome": "Festa", "data": "2027-01-01", "csrf": "x" * 43})
    assert r.status_code == 403


def test_paginas_exigem_login_e_voltam_depois():
    n = Navegador()
    r = n.get("/eventos/1/convidados", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrar?proximo=/eventos/1/convidados"


def test_login_nao_redireciona_para_outro_site():
    n = Navegador()
    n.cadastrar("leo@exemplo.com")
    n.post("/sair")
    for destino in ("https://site-malicioso.com", "//site-malicioso.com", "/\\site-malicioso.com"):
        r = n.post(
            "/entrar",
            {"email": "leo@exemplo.com", "senha": SENHA, "proximo": destino},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/eventos"


def test_fluxo_completo_do_cadastro_ao_checkin(ana):
    ev = criar_evento(ana)
    painel = ana.get(f"/eventos/{ev}")
    assert (
        "Monte a lista de convidados" in painel.text and "Evento criado" not in painel.text
    )  # aviso aparece 1 vez

    # convidados: um com erro, um certo e uma planilha
    r = ana.post(f"/eventos/{ev}/convidados", {"nome": "Rui", "telefone": "123"})
    assert "Telefone: telefone deve ter DDD" in r.text
    ana.post(
        f"/eventos/{ev}/convidados", {"nome": "Maria Souza", "telefone": "(61) 99999-0000", "grupo": "Noiva"}
    )
    planilha = "nome;telefone\nJoão Lima;61988887777\nMaria Souza;61999990000\n".encode()
    r = ana.post(f"/eventos/{ev}/convidados/importar", files={"arquivo": ("lista.csv", planilha, "text/csv")})
    assert "1 convidado(s) importado(s). 1 já estavam na lista" in r.text
    assert "(61) 99999-0000" in r.text

    # mesas: duas de uma vez
    r = ana.post(f"/eventos/{ev}/mesas", {"quantidade": "2", "capacidade": "1"})
    assert "2 mesas criadas" in r.text and "Mesa 1" in r.text and "Mesa 2" in r.text

    # convites: o link aparece uma vez, com WhatsApp para quem tem telefone
    r = ana.post(f"/eventos/{ev}/convites", {"modo": "novos"})
    assert r.headers["cache-control"] == "no-store"
    links = re.findall(r'value="(https://mesaposta\.exemplo/c/[^"]+)"', r.text)
    assert len(links) == 2 and "wa.me/5561" in r.text
    token = links[1].rsplit("/", 1)[1]  # Maria Souza (ordem alfabética)
    assert "Todos os convidados já têm convite" in ana.post(f"/eventos/{ev}/convites", {"modo": "novos"}).text

    # a convidada confirma pelo celular, sem conta
    convidada = Navegador()
    pagina = convidada.get(f"/c/{token}")
    assert "Olá, <strong>Maria Souza</strong>" in pagina.text and "Vou!" in pagina.text
    assert pagina.headers["referrer-policy"] == "no-referrer"
    r = convidada.post(f"/c/{token}", {"resposta": "confirmado", "restricao_alimentar": "vegetariana"})
    assert "Presença confirmada" in r.text and f"/c/{token}/qr.svg" in r.text
    assert convidada.get(f"/c/{token}/qr.svg").headers["content-type"] == "image/svg+xml"

    # o cerimonial vê a resposta e senta a convidada
    lista = ana.get(f"/eventos/{ev}/convidados?filtro=confirmados")
    assert "Maria Souza" in lista.text and "vegetariana" in lista.text and "João Lima" not in lista.text
    maria_id = re.search(rf"/eventos/{ev}/convidados/(\d+)/mesa", ana.get(f"/eventos/{ev}/mesas").text).group(
        1
    )
    mesa1 = re.search(r'<option value="(\d+)">Mesa 1', ana.get(f"/eventos/{ev}/mesas").text).group(1)
    r = ana.post(f"/eventos/{ev}/convidados/{maria_id}/mesa", {"mesa_id": mesa1})
    assert "Maria Souza foi para a Mesa 1" in r.text
    assert "Sua mesa: <strong>Mesa 1</strong>" in convidada.get(f"/c/{token}").text

    # mesa de 1 lugar cheia: o João não cabe
    joao_id = re.search(rf"/eventos/{ev}/convidados/(\d+)/mesa", ana.get(f"/eventos/{ev}/mesas").text).group(
        1
    )
    r = ana.post(f"/eventos/{ev}/convidados/{joao_id}/mesa", {"mesa_id": mesa1})
    assert "já está com 1 de 1 lugares ocupados" in r.text

    # recepção: QR lido duas vezes
    r = ana.post(f"/eventos/{ev}/recepcao/codigo", {"codigo": links[1]})
    assert "Bem-vindo(a), Maria Souza! Mesa 1." in r.text
    r = ana.post(f"/eventos/{ev}/recepcao/codigo", {"codigo": links[1]})
    assert "Maria Souza já fez check-in" in r.text
    r = ana.post(f"/eventos/{ev}/recepcao/codigo", {"codigo": "https://mesaposta.exemplo/c/codigo-invalido"})
    assert "QR code não reconhecido" in r.text
    # check-in manual de quem não confirmou gera alerta
    r = ana.post(f"/eventos/{ev}/recepcao/{joao_id}")
    assert "NÃO tinha confirmado presença" in r.text

    resumo = ana.get(f"/eventos/{ev}").text
    assert re.search(r"<strong>2</strong><span>já chegaram</span>", resumo)


def test_convidado_recusa_e_prazo(ana):
    ev = criar_evento(ana)
    ana.post(f"/eventos/{ev}/convidados", {"nome": "Paulo"})
    link = re.search(r'value="(https://[^"]+/c/[^"]+)"', ana.post(f"/eventos/{ev}/convites").text).group(1)
    token = link.rsplit("/", 1)[1]
    convidado = Navegador()
    assert "Resposta registrada" in convidado.post(f"/c/{token}", {"resposta": "recusado"}).text
    ana.post(
        f"/eventos/{ev}/configuracoes", {"nome": "Casamento", "data": "2027-05-15", "prazo": "2020-01-01"}
    )
    pagina = convidado.get(f"/c/{token}").text
    assert "O prazo para responder terminou" in pagina and 'name="resposta"' not in pagina
    assert (
        "O prazo para confirmar presença terminou"
        in convidado.post(f"/c/{token}", {"resposta": "confirmado"}).text
    )
    assert convidado.get("/c/link-que-nao-existe").status_code == 404


def test_papeis_e_quem_nao_participa(ana):
    ev = criar_evento(ana)
    rui = Navegador()
    rui.cadastrar("rui@recepcao.com", "Rui")
    assert (
        "Rui agora faz parte da equipe como Recepção"
        in ana.post(f"/eventos/{ev}/equipe", {"email": "rui@recepcao.com", "papel": "recepcao"}).text
    )
    assert (
        "Não há conta com"
        in ana.post(f"/eventos/{ev}/equipe", {"email": "nin@guem.com", "papel": "noivos"}).text
    )

    abas = rui.get(f"/eventos/{ev}").text
    assert "Recepção</a>" in abas and "Convites</a>" not in abas and "Equipe</a>" not in abas
    assert rui.get(f"/eventos/{ev}/convites").status_code == 403
    assert rui.post(f"/eventos/{ev}/convidados", {"nome": "Intruso"}).status_code == 403
    assert rui.get(f"/eventos/{ev}/recepcao").status_code == 200

    estranho = Navegador()
    estranho.cadastrar("zeca@outro.com")
    r = estranho.get(f"/eventos/{ev}")
    assert r.status_code == 404 and "Evento não encontrado" in r.text

    # o evento nunca fica sem cerimonial
    ana_id = re.search(r"/equipe/(\d+)/remover", ana.get(f"/eventos/{ev}/equipe").text).group(1)
    assert "precisa de ao menos um cerimonial" in ana.post(f"/eventos/{ev}/equipe/{ana_id}/remover").text


def test_editar_e_excluir(ana):
    ev = criar_evento(ana)
    ana.post(f"/eventos/{ev}/convidados", {"nome": "Carla"})
    cid = re.search(rf"/eventos/{ev}/convidados/(\d+)\"", ana.get(f"/eventos/{ev}/convidados").text).group(1)
    r = ana.post(f"/eventos/{ev}/convidados/{cid}", {"nome": "Carla Dias", "resposta": "confirmado"})
    assert "Dados de Carla Dias salvos" in r.text
    assert "Carla Dias foi removido(a)" in ana.post(f"/eventos/{ev}/convidados/{cid}/excluir").text

    r = ana.post(f"/eventos/{ev}/excluir", {"confirmacao": "outro nome"})
    assert "digite o nome do evento exatamente" in r.text
    r = ana.post(f"/eventos/{ev}/excluir", {"confirmacao": "casamento de bia e caio"})
    assert "foi excluído" in r.text and "Nenhum evento ainda" in r.text
