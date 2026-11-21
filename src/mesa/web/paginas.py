"""Telas do Mesa Posta. As regras de negócio são as mesmas da API (mesa.servicos); aqui só há HTML."""

import io
from datetime import date, datetime, time
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from zoneinfo import ZoneInfo

import segno
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mesa.deps import CERIMONIAL, EDITORES, TODOS, Acesso, Db
from mesa.erros import ErroApp
from mesa.esquemas import CadastroEntrada, ConvidadoAtualizar, ConvidadoEntrada, EventoEntrada, MesaEntrada
from mesa.modelos import Convidado, Evento, Membro, Mesa, Usuario
from mesa.rotas.convidados import TAMANHO_MAXIMO
from mesa.seguranca import HASH_FALSO, confere_senha, hash_senha
from mesa.servicos import (
    alterar_mesa,
    atribuir_mesa,
    buscar_convidado,
    checkin_por_codigo,
    convidado_pelo_token,
    fazer_checkin,
    gerar_convites,
    importar_csv,
    link_convite,
    prazo_encerrado,
    responder_convite,
    resumo,
)
from mesa.web.sessao import (
    COOKIE_AVISO,
    ErroWeb,
    PrecisaEntrar,
    encerrar_sessao,
    iniciar_sessao,
    ler_aviso,
    mensagens,
    redirecionar,
    usuario_id_da_sessao,
    verificar_csrf,
)

FUSO = ZoneInfo("America/Sao_Paulo")
POR_PAGINA = 50
PAPEL_NOME = {"cerimonial": "Cerimonial", "noivos": "Noivos", "recepcao": "Recepção"}

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["data_br"] = lambda d: d.strftime("%d/%m/%Y") if d else ""
templates.env.filters["hora_br"] = lambda d: d.astimezone(FUSO).strftime("%d/%m %H:%M") if d else ""
templates.env.filters["telefone"] = lambda t: (
    f"({t[:2]}) {t[2:-4]}-{t[-4:]}" if t and len(t) in (10, 11) else (t or "")
)
templates.env.globals["PAPEL_NOME"] = PAPEL_NOME

router = APIRouter(include_in_schema=False)
Csrf = Depends(verificar_csrf)


# --- utilitários -------------------------------------------------------------------------------


def pagina(request: Request, modelo: str, status: int = 200, **contexto) -> HTMLResponse:
    aviso = ler_aviso(request)
    resposta = templates.TemplateResponse(
        request,
        modelo,
        {
            "csrf": request.state.csrf,
            "aviso": aviso,
            "usuario": getattr(request.state, "usuario", None),
            **contexto,
        },
        status_code=status,
    )
    if aviso:
        resposta.delete_cookie(COOKIE_AVISO)
    return resposta


def destino_seguro(url: str | None, padrao: str) -> str:
    """Só caminhos internos: evita redirecionar para outro site (open redirect)."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return padrao


def usuario_web(request: Request, db: Db) -> Usuario:
    usuario_id = usuario_id_da_sessao(request)
    usuario = db.get(Usuario, usuario_id) if usuario_id else None
    if usuario is None:
        raise PrecisaEntrar(request.url.path)
    request.state.usuario = usuario
    return usuario


Logado = Annotated[Usuario, Depends(usuario_web)]


def acesso(db: Db, usuario: Usuario, evento_id: int, papeis: tuple[str, ...] = TODOS) -> Acesso:
    membro = db.get(Membro, (evento_id, usuario.id))
    if membro is None:
        raise ErroWeb(
            404, "Evento não encontrado", "Este evento não existe ou você não faz parte da equipe dele."
        )
    if membro.papel not in papeis:
        raise ErroWeb(403, "Sem permissão", "O seu papel neste evento não permite acessar esta página.")
    evento = db.get(Evento, evento_id)
    assert evento is not None
    return Acesso(evento=evento, papel=membro.papel, usuario=usuario)


def prazo_do_dia(dia: date | None) -> datetime | None:
    return datetime.combine(dia, time(23, 59), FUSO) if dia else None


def base(evento_id: int) -> str:
    return f"/eventos/{evento_id}"


# --- início e conta ----------------------------------------------------------------------------


@router.get("/")
def inicio(request: Request, db: Db):
    usuario_id = usuario_id_da_sessao(request)
    if usuario_id and db.get(Usuario, usuario_id):
        return redirecionar("/eventos")
    return pagina(request, "inicio.html")


@router.get("/entrar")
def entrar_form(request: Request, proximo: str = "/eventos"):
    return pagina(request, "conta.html", modo="entrar", proximo=destino_seguro(proximo, "/eventos"))


@router.post("/entrar", dependencies=[Csrf])
def entrar(
    request: Request,
    db: Db,
    email: Annotated[str, Form()],
    senha: Annotated[str, Form()],
    proximo: Annotated[str, Form()] = "/eventos",
):
    usuario = db.scalar(select(Usuario).where(Usuario.email == email.strip().lower()))
    senha_ok = confere_senha(senha, usuario.senha_hash if usuario else HASH_FALSO)
    if usuario is None or not senha_ok:
        return pagina(
            request,
            "conta.html",
            401,
            modo="entrar",
            proximo=proximo,
            email=email,
            erro="E-mail ou senha incorretos.",
        )
    return iniciar_sessao(redirecionar(destino_seguro(proximo, "/eventos")), usuario.id)


@router.get("/cadastro")
def cadastro_form(request: Request):
    return pagina(request, "conta.html", modo="cadastro")


@router.post("/cadastro", dependencies=[Csrf])
def cadastrar(
    request: Request,
    db: Db,
    nome: Annotated[str, Form()],
    email: Annotated[str, Form()],
    senha: Annotated[str, Form()],
):
    try:
        dados = CadastroEntrada(nome=nome, email=email.strip(), senha=senha)
    except ValidationError as e:
        return pagina(request, "conta.html", 422, modo="cadastro", nome=nome, email=email, erro=mensagens(e))
    usuario = Usuario(nome=dados.nome.strip(), email=dados.email.lower(), senha_hash=hash_senha(dados.senha))
    db.add(usuario)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        erro = "Já existe uma conta com este e-mail. Use a opção Entrar."
        return pagina(request, "conta.html", 409, modo="cadastro", nome=nome, email=email, erro=erro)
    return iniciar_sessao(
        redirecionar("/eventos", f"Bem-vindo(a), {usuario.nome}! Crie o seu primeiro evento."), usuario.id
    )


@router.post("/sair", dependencies=[Csrf])
def sair():
    return encerrar_sessao(redirecionar("/", "Você saiu da sua conta."))


# --- eventos -----------------------------------------------------------------------------------


@router.get("/eventos")
def eventos(request: Request, db: Db, usuario: Logado):
    linhas = db.execute(
        select(Evento, Membro.papel)
        .join(Membro, Membro.evento_id == Evento.id)
        .where(Membro.usuario_id == usuario.id)
        .order_by(Evento.data, Evento.id)
    ).all()
    contagem = dict(
        db.execute(
            select(Convidado.evento_id, func.count())
            .where(Convidado.evento_id.in_([e.id for e, _ in linhas]))
            .group_by(Convidado.evento_id)
        ).all()
    )
    confirmados = dict(
        db.execute(
            select(Convidado.evento_id, func.count())
            .where(Convidado.evento_id.in_([e.id for e, _ in linhas]), Convidado.resposta == "confirmado")
            .group_by(Convidado.evento_id)
        ).all()
    )
    return pagina(
        request, "eventos.html", linhas=linhas, contagem=contagem, confirmados=confirmados, hoje=date.today()
    )


@router.post("/eventos", dependencies=[Csrf])
def criar_evento(
    db: Db,
    usuario: Logado,
    nome: Annotated[str, Form()],
    data: Annotated[str, Form()],
    local: Annotated[str, Form()] = "",
    prazo: Annotated[str, Form()] = "",
):
    try:
        dados = EventoEntrada(
            nome=nome,
            data=data,
            local=local.strip() or None,
            prazo_rsvp=prazo_do_dia(date.fromisoformat(prazo)) if prazo else None,
        )
    except (ValidationError, ValueError) as e:
        texto = mensagens(e) if isinstance(e, ValidationError) else "Prazo para confirmar: data inválida."
        return redirecionar("/eventos", texto, "erro")
    evento = Evento(**dados.model_dump())
    db.add(evento)
    db.flush()
    db.add(Membro(evento_id=evento.id, usuario_id=usuario.id, papel="cerimonial"))
    db.commit()
    return redirecionar(base(evento.id), "Evento criado. Agora adicione os convidados.")


@router.get("/eventos/{evento_id}")
def painel(request: Request, db: Db, usuario: Logado, evento_id: int):
    a = acesso(db, usuario, evento_id)
    r = resumo(db, evento_id)
    mesas = db.scalar(select(func.count()).select_from(Mesa).where(Mesa.evento_id == evento_id)) or 0
    com_convite = (
        db.scalar(
            select(func.count()).where(Convidado.evento_id == evento_id, Convidado.token_hash.is_not(None))
        )
        or 0
    )
    return pagina(request, "painel.html", a=a, aba="painel", r=r, mesas=mesas, com_convite=com_convite)


# --- convidados --------------------------------------------------------------------------------

FILTROS = {
    "todos": None,
    "confirmados": Convidado.resposta == "confirmado",
    "pendentes": Convidado.resposta == "pendente",
    "recusados": Convidado.resposta == "recusado",
    "sem_mesa": (Convidado.mesa_id.is_(None)) & (Convidado.resposta != "recusado"),
    "presentes": Convidado.checkin_em.is_not(None),
}


@router.get("/eventos/{evento_id}/convidados")
def convidados(
    request: Request,
    db: Db,
    usuario: Logado,
    evento_id: int,
    busca: Annotated[str, Query(max_length=60)] = "",
    filtro: str = "todos",
    pagina_n: Annotated[int, Query(alias="pagina", ge=1)] = 1,
):
    a = acesso(db, usuario, evento_id)
    condicoes = [Convidado.evento_id == evento_id]
    if busca.strip():
        termo = busca.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        condicoes.append(Convidado.nome.ilike(f"%{termo}%"))
    if FILTROS.get(filtro) is not None:
        condicoes.append(FILTROS[filtro])
    total = db.scalar(select(func.count()).select_from(Convidado).where(*condicoes)) or 0
    lista = db.scalars(
        select(Convidado)
        .where(*condicoes)
        .order_by(Convidado.nome, Convidado.id)
        .limit(POR_PAGINA)
        .offset((pagina_n - 1) * POR_PAGINA)
    ).all()
    mesas = dict(db.execute(select(Mesa.id, Mesa.nome).where(Mesa.evento_id == evento_id)).all())
    return pagina(
        request,
        "convidados.html",
        a=a,
        aba="convidados",
        lista=lista,
        total=total,
        busca=busca,
        filtro=filtro if filtro in FILTROS else "todos",
        pagina_n=pagina_n,
        paginas=max(1, -(-total // POR_PAGINA)),
        mesas=mesas,
        r=resumo(db, evento_id),
    )


@router.post("/eventos/{evento_id}/convidados", dependencies=[Csrf])
def adicionar_convidado(
    db: Db,
    usuario: Logado,
    evento_id: int,
    nome: Annotated[str, Form()],
    telefone: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    grupo: Annotated[str, Form()] = "",
):
    acesso(db, usuario, evento_id, EDITORES)
    voltar = f"{base(evento_id)}/convidados"
    try:
        dados = ConvidadoEntrada(
            nome=nome, telefone=telefone or None, email=email.strip() or None, grupo=grupo
        )
    except ValidationError as e:
        return redirecionar(voltar, mensagens(e), "erro")
    db.add(Convidado(evento_id=evento_id, **dados.model_dump()))
    db.commit()
    return redirecionar(voltar, f"{dados.nome} foi adicionado(a) à lista.")


@router.get("/eventos/{evento_id}/convidados/modelo.csv")
def modelo_planilha(db: Db, usuario: Logado, evento_id: int):
    acesso(db, usuario, evento_id, EDITORES)
    conteudo = "﻿nome;telefone;email;grupo\nMaria da Silva;(61) 99999-0000;maria@email.com;Família da noiva\n"
    return Response(
        conteudo,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="modelo-convidados.csv"'},
    )


@router.post("/eventos/{evento_id}/convidados/importar", dependencies=[Csrf])
async def importar(
    request: Request, db: Db, usuario: Logado, evento_id: int, arquivo: Annotated[UploadFile, File()]
):
    acesso(db, usuario, evento_id, EDITORES)
    voltar = f"{base(evento_id)}/convidados"
    conteudo = await arquivo.read(TAMANHO_MAXIMO + 1)
    if not conteudo:
        return redirecionar(voltar, "Escolha um arquivo CSV para importar.", "erro")
    if len(conteudo) > TAMANHO_MAXIMO:
        return redirecionar(voltar, "O arquivo passa de 1 MB.", "erro")
    try:
        importados, ignorados = importar_csv(db, evento_id, conteudo)
    except ErroApp as e:
        detalhes = ""
        if isinstance(e.detalhes, list):
            detalhes = " " + " ".join(f"Linha {d['linha']}: {'; '.join(d['erros'])}." for d in e.detalhes[:5])
        return redirecionar(voltar, e.mensagem + detalhes, "erro")
    texto = f"{importados} convidado(s) importado(s)."
    if ignorados:
        texto += f" {len(ignorados)} já estavam na lista e foram ignorados."
    return redirecionar(voltar, texto)


@router.get("/eventos/{evento_id}/convidados/{convidado_id}")
def editar_convidado_form(request: Request, db: Db, usuario: Logado, evento_id: int, convidado_id: int):
    a = acesso(db, usuario, evento_id, EDITORES)
    c = _convidado(db, evento_id, convidado_id)
    mesas = db.execute(
        select(Mesa.id, Mesa.nome).where(Mesa.evento_id == evento_id).order_by(Mesa.nome)
    ).all()
    return pagina(request, "convidado.html", a=a, aba="convidados", c=c, mesas=mesas)


def _convidado(db: Db, evento_id: int, convidado_id: int) -> Convidado:
    try:
        return buscar_convidado(db, evento_id, convidado_id)
    except ErroApp as e:
        raise ErroWeb(404, "Convidado não encontrado", "Ele pode ter sido excluído da lista.") from e


@router.post("/eventos/{evento_id}/convidados/{convidado_id}", dependencies=[Csrf])
def editar_convidado(
    db: Db,
    usuario: Logado,
    evento_id: int,
    convidado_id: int,
    nome: Annotated[str, Form()],
    resposta: Annotated[str, Form()],
    telefone: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    grupo: Annotated[str, Form()] = "",
    restricao_alimentar: Annotated[str, Form()] = "",
):
    acesso(db, usuario, evento_id, EDITORES)
    c = _convidado(db, evento_id, convidado_id)
    pagina_edicao = f"{base(evento_id)}/convidados/{convidado_id}"
    try:
        dados = ConvidadoAtualizar(
            nome=nome,
            telefone=telefone or None,
            email=email.strip() or None,
            grupo=grupo,
            resposta=resposta,
            restricao_alimentar=restricao_alimentar,
        )
    except ValidationError as e:
        return redirecionar(pagina_edicao, mensagens(e), "erro")
    if dados.resposta != c.resposta:
        c.respondido_em = func.now()
    if dados.resposta == "recusado":
        c.mesa_id = None
    for campo, valor in dados.model_dump().items():
        setattr(c, campo, valor)
    db.commit()
    return redirecionar(f"{base(evento_id)}/convidados", f"Dados de {c.nome} salvos.")


@router.post("/eventos/{evento_id}/convidados/{convidado_id}/excluir", dependencies=[Csrf])
def excluir_convidado(db: Db, usuario: Logado, evento_id: int, convidado_id: int):
    acesso(db, usuario, evento_id, EDITORES)
    c = _convidado(db, evento_id, convidado_id)
    nome = c.nome
    db.delete(c)
    db.commit()
    return redirecionar(f"{base(evento_id)}/convidados", f"{nome} foi removido(a) da lista.")


@router.post("/eventos/{evento_id}/convidados/{convidado_id}/mesa", dependencies=[Csrf])
def sentar(
    db: Db,
    usuario: Logado,
    evento_id: int,
    convidado_id: int,
    mesa_id: Annotated[str, Form()] = "",
    voltar: Annotated[str, Form()] = "",
):
    acesso(db, usuario, evento_id, EDITORES)
    destino = destino_seguro(voltar, f"{base(evento_id)}/mesas")
    try:
        c = atribuir_mesa(db, evento_id, convidado_id, int(mesa_id) if mesa_id else None)
    except ErroApp as e:
        return redirecionar(destino, e.mensagem, "erro")
    except ValueError:
        return redirecionar(destino, "Mesa inválida.", "erro")
    if c.mesa_id is None:
        return redirecionar(destino, f"{c.nome} ficou sem mesa.")
    mesa = db.get(Mesa, c.mesa_id)
    return redirecionar(destino, f"{c.nome} foi para a {mesa.nome if mesa else 'mesa'}.")


# --- mesas -------------------------------------------------------------------------------------


@router.get("/eventos/{evento_id}/mesas")
def mesas(request: Request, db: Db, usuario: Logado, evento_id: int):
    a = acesso(db, usuario, evento_id)
    lista_mesas = db.scalars(select(Mesa).where(Mesa.evento_id == evento_id).order_by(Mesa.nome)).all()
    sentados: dict[int, list[Convidado]] = {m.id: [] for m in lista_mesas}
    for c in db.scalars(
        select(Convidado)
        .where(Convidado.evento_id == evento_id, Convidado.mesa_id.is_not(None))
        .order_by(Convidado.nome)
    ):
        sentados[c.mesa_id].append(c)
    sem_mesa = db.scalars(
        select(Convidado)
        .where(
            Convidado.evento_id == evento_id, Convidado.mesa_id.is_(None), Convidado.resposta != "recusado"
        )
        .order_by((Convidado.resposta == "confirmado").desc(), Convidado.nome)  # confirmados primeiro
    ).all()
    return pagina(
        request, "mesas.html", a=a, aba="mesas", lista_mesas=lista_mesas, sentados=sentados, sem_mesa=sem_mesa
    )


@router.post("/eventos/{evento_id}/mesas", dependencies=[Csrf])
def criar_mesas(
    db: Db,
    usuario: Logado,
    evento_id: int,
    capacidade: Annotated[str, Form()],
    nome: Annotated[str, Form()] = "",
    quantidade: Annotated[str, Form()] = "1",
):
    """Cria uma mesa com nome, ou várias numeradas ("Mesa 1", "Mesa 2"...), pulando nomes que já existem."""
    acesso(db, usuario, evento_id, EDITORES)
    voltar = f"{base(evento_id)}/mesas"
    try:
        n = int(quantidade or 1)
        if not 1 <= n <= 60:
            raise ValueError
    except ValueError:
        return redirecionar(voltar, "Quantidade: escolha entre 1 e 60 mesas.", "erro")
    existentes = set(db.scalars(select(Mesa.nome).where(Mesa.evento_id == evento_id)))
    if nome.strip():
        nomes = [nome.strip()]
    else:
        nomes, i = [], 1
        while len(nomes) < n:
            if f"Mesa {i}" not in existentes:
                nomes.append(f"Mesa {i}")
            i += 1
    if nomes[0] in existentes:
        return redirecionar(voltar, f"Já existe uma mesa chamada {nomes[0]}.", "erro")
    try:
        validas = [MesaEntrada(nome=x, capacidade=capacidade) for x in nomes]
    except ValidationError as e:
        return redirecionar(voltar, mensagens(e), "erro")
    db.add_all(Mesa(evento_id=evento_id, nome=m.nome, capacidade=m.capacidade) for m in validas)
    db.commit()
    texto = f"{validas[0].nome} criada." if len(validas) == 1 else f"{len(validas)} mesas criadas."
    return redirecionar(voltar, texto)


@router.post("/eventos/{evento_id}/mesas/{mesa_id}", dependencies=[Csrf])
def editar_mesa(
    db: Db,
    usuario: Logado,
    evento_id: int,
    mesa_id: int,
    nome: Annotated[str, Form()],
    capacidade: Annotated[str, Form()],
):
    acesso(db, usuario, evento_id, EDITORES)
    voltar = f"{base(evento_id)}/mesas"
    try:
        dados = MesaEntrada(nome=nome.strip(), capacidade=capacidade)
        alterar_mesa(db, evento_id, mesa_id, dados.nome, dados.capacidade)
        db.commit()
    except ValidationError as e:
        return redirecionar(voltar, mensagens(e), "erro")
    except ErroApp as e:
        return redirecionar(voltar, e.mensagem, "erro")
    except IntegrityError:
        db.rollback()
        return redirecionar(voltar, "Já existe uma mesa com esse nome.", "erro")
    return redirecionar(voltar, f"{dados.nome} atualizada.")


@router.post("/eventos/{evento_id}/mesas/{mesa_id}/excluir", dependencies=[Csrf])
def excluir_mesa(db: Db, usuario: Logado, evento_id: int, mesa_id: int):
    acesso(db, usuario, evento_id, EDITORES)
    mesa = db.scalar(select(Mesa).where(Mesa.id == mesa_id, Mesa.evento_id == evento_id))
    if mesa is None:
        raise ErroWeb(404, "Mesa não encontrada", "Ela pode já ter sido excluída.")
    nome = mesa.nome
    db.delete(mesa)
    db.commit()
    return redirecionar(f"{base(evento_id)}/mesas", f"{nome} excluída. Os convidados dela ficaram sem mesa.")


# --- convites ----------------------------------------------------------------------------------


def _mensagem_whatsapp(evento: Evento, nome: str, link: str) -> str:
    texto = (
        f"Olá, {nome.split()[0]}! Você está convidado(a) para {evento.nome}, "
        f"em {evento.data:%d/%m/%Y}. Confirme sua presença por este link: {link}"
    )
    return quote(texto)


@router.get("/eventos/{evento_id}/convites")
def convites(request: Request, db: Db, usuario: Logado, evento_id: int):
    a = acesso(db, usuario, evento_id, CERIMONIAL)
    lista = db.scalars(
        select(Convidado).where(Convidado.evento_id == evento_id).order_by(Convidado.nome)
    ).all()
    sem_convite = sum(1 for c in lista if not c.tem_convite)
    return pagina(request, "convites.html", a=a, aba="convites", lista=lista, sem_convite=sem_convite)


def _mostrar_gerados(request: Request, a: Acesso, gerados) -> HTMLResponse:
    itens = [
        {
            "nome": g.nome,
            "telefone": g.telefone,
            "link": g.link,
            "whatsapp": (
                f"https://wa.me/55{g.telefone}?text={_mensagem_whatsapp(a.evento, g.nome, g.link)}"
                if g.telefone
                else None
            ),
        }
        for g in gerados
    ]
    resposta = pagina(request, "convites_gerados.html", a=a, aba="convites", itens=itens)
    resposta.headers["Cache-Control"] = "no-store"  # os links só existem nesta página
    return resposta


@router.post("/eventos/{evento_id}/convites", dependencies=[Csrf])
def gerar(request: Request, db: Db, usuario: Logado, evento_id: int, modo: Annotated[str, Form()] = "novos"):
    a = acesso(db, usuario, evento_id, CERIMONIAL)
    gerados = gerar_convites(db, evento_id, regenerar=(modo == "todos"))
    if not gerados:
        return redirecionar(f"{base(evento_id)}/convites", "Todos os convidados já têm convite.", "alerta")
    return _mostrar_gerados(request, a, gerados)


@router.post("/eventos/{evento_id}/convidados/{convidado_id}/convite", dependencies=[Csrf])
def gerar_um(request: Request, db: Db, usuario: Logado, evento_id: int, convidado_id: int):
    """Novo link para um convidado só (perdeu o link, ou o antigo foi compartilhado por engano)."""
    from mesa.esquemas import ConviteSaida
    from mesa.seguranca import novo_token_convite

    a = acesso(db, usuario, evento_id, CERIMONIAL)
    c = _convidado(db, evento_id, convidado_id)
    token, c.token_hash = novo_token_convite()
    db.commit()
    return _mostrar_gerados(
        request,
        a,
        [ConviteSaida(convidado_id=c.id, nome=c.nome, telefone=c.telefone, link=link_convite(token))],
    )


# --- equipe e configurações --------------------------------------------------------------------


@router.get("/eventos/{evento_id}/equipe")
def equipe(request: Request, db: Db, usuario: Logado, evento_id: int):
    a = acesso(db, usuario, evento_id, CERIMONIAL)
    membros = db.execute(
        select(Usuario, Membro.papel)
        .join(Membro, Membro.usuario_id == Usuario.id)
        .where(Membro.evento_id == evento_id)
        .order_by(Usuario.nome)
    ).all()
    return pagina(request, "equipe.html", a=a, aba="equipe", membros=membros)


def _cerimoniais(db: Db, evento_id: int) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Membro)
            .where(Membro.evento_id == evento_id, Membro.papel == "cerimonial")
        )
        or 0
    )


@router.post("/eventos/{evento_id}/equipe", dependencies=[Csrf])
def adicionar_membro(
    db: Db, usuario: Logado, evento_id: int, email: Annotated[str, Form()], papel: Annotated[str, Form()]
):
    acesso(db, usuario, evento_id, CERIMONIAL)
    voltar = f"{base(evento_id)}/equipe"
    if papel not in PAPEL_NOME:
        return redirecionar(voltar, "Escolha um papel.", "erro")
    pessoa = db.scalar(select(Usuario).where(Usuario.email == email.strip().lower()))
    if pessoa is None:
        texto = f"Não há conta com {email}. Peça para a pessoa se cadastrar no Mesa Posta e tente de novo."
        return redirecionar(voltar, texto, "erro")
    membro = db.get(Membro, (evento_id, pessoa.id))
    if membro is None:
        db.add(Membro(evento_id=evento_id, usuario_id=pessoa.id, papel=papel))
    else:
        if membro.papel == "cerimonial" and papel != "cerimonial" and _cerimoniais(db, evento_id) <= 1:
            return redirecionar(voltar, "O evento precisa de ao menos um cerimonial.", "erro")
        membro.papel = papel
    db.commit()
    return redirecionar(voltar, f"{pessoa.nome} agora faz parte da equipe como {PAPEL_NOME[papel]}.")


@router.post("/eventos/{evento_id}/equipe/{usuario_id}/remover", dependencies=[Csrf])
def remover_membro(db: Db, usuario: Logado, evento_id: int, usuario_id: int):
    acesso(db, usuario, evento_id, CERIMONIAL)
    voltar = f"{base(evento_id)}/equipe"
    membro = db.get(Membro, (evento_id, usuario_id))
    if membro is None:
        return redirecionar(voltar, "Essa pessoa não está na equipe.", "erro")
    if membro.papel == "cerimonial" and _cerimoniais(db, evento_id) <= 1:
        return redirecionar(voltar, "O evento precisa de ao menos um cerimonial.", "erro")
    db.delete(membro)
    db.commit()
    if usuario_id == usuario.id:
        return redirecionar("/eventos", "Você saiu da equipe deste evento.")
    return redirecionar(voltar, "Pessoa removida da equipe.")


@router.get("/eventos/{evento_id}/configuracoes")
def configuracoes(request: Request, db: Db, usuario: Logado, evento_id: int):
    a = acesso(db, usuario, evento_id, CERIMONIAL)
    prazo = a.evento.prazo_rsvp.astimezone(FUSO).date() if a.evento.prazo_rsvp else None
    return pagina(request, "configuracoes.html", a=a, aba="configuracoes", prazo=prazo)


@router.post("/eventos/{evento_id}/configuracoes", dependencies=[Csrf])
def salvar_configuracoes(
    db: Db,
    usuario: Logado,
    evento_id: int,
    nome: Annotated[str, Form()],
    data: Annotated[str, Form()],
    local: Annotated[str, Form()] = "",
    prazo: Annotated[str, Form()] = "",
):
    a = acesso(db, usuario, evento_id, CERIMONIAL)
    voltar = f"{base(evento_id)}/configuracoes"
    try:
        dados = EventoEntrada(
            nome=nome,
            data=data,
            local=local.strip() or None,
            prazo_rsvp=prazo_do_dia(date.fromisoformat(prazo)) if prazo else None,
        )
    except (ValidationError, ValueError) as e:
        return redirecionar(
            voltar, mensagens(e) if isinstance(e, ValidationError) else "Data inválida.", "erro"
        )
    for campo, valor in dados.model_dump().items():
        setattr(a.evento, campo, valor)
    db.commit()
    return redirecionar(voltar, "Dados do evento salvos.")


@router.post("/eventos/{evento_id}/excluir", dependencies=[Csrf])
def excluir_evento(db: Db, usuario: Logado, evento_id: int, confirmacao: Annotated[str, Form()] = ""):
    a = acesso(db, usuario, evento_id, CERIMONIAL)
    if confirmacao.strip().casefold() != a.evento.nome.strip().casefold():
        return redirecionar(
            f"{base(evento_id)}/configuracoes", "Para excluir, digite o nome do evento exatamente.", "erro"
        )
    nome = a.evento.nome
    db.delete(a.evento)
    db.commit()
    return redirecionar("/eventos", f"O evento {nome} foi excluído.")


# --- recepção (check-in) -----------------------------------------------------------------------


@router.get("/eventos/{evento_id}/recepcao")
def recepcao(
    request: Request,
    db: Db,
    usuario: Logado,
    evento_id: int,
    busca: Annotated[str, Query(max_length=60)] = "",
):
    a = acesso(db, usuario, evento_id)
    encontrados = []
    if len(busca.strip()) >= 2:
        termo = busca.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        encontrados = db.scalars(
            select(Convidado)
            .where(Convidado.evento_id == evento_id, Convidado.nome.ilike(f"%{termo}%"))
            .order_by(Convidado.nome)
            .limit(20)
        ).all()
    recentes = db.scalars(
        select(Convidado)
        .where(Convidado.evento_id == evento_id, Convidado.checkin_em.is_not(None))
        .order_by(Convidado.checkin_em.desc())
        .limit(8)
    ).all()
    mesas = dict(db.execute(select(Mesa.id, Mesa.nome).where(Mesa.evento_id == evento_id)).all())
    return pagina(
        request,
        "recepcao.html",
        a=a,
        aba="recepcao",
        busca=busca,
        encontrados=encontrados,
        recentes=recentes,
        mesas=mesas,
        r=resumo(db, evento_id),
    )


def _resultado_checkin(db: Db, evento_id: int, c: Convidado):
    mesa = db.get(Mesa, c.mesa_id) if c.mesa_id else None
    voltar = f"{base(evento_id)}/recepcao"
    if c.resposta != "confirmado":
        texto = f"{c.nome} entrou, mas NÃO tinha confirmado presença. Avise o cerimonial."
        return redirecionar(voltar, texto, "alerta")
    if mesa is None:
        return redirecionar(voltar, f"{c.nome} entrou. Confirmado(a), mas ainda SEM MESA.", "alerta")
    return redirecionar(voltar, f"Bem-vindo(a), {c.nome}! {mesa.nome}.")


@router.post("/eventos/{evento_id}/recepcao/codigo", dependencies=[Csrf])
def checkin_codigo(db: Db, usuario: Logado, evento_id: int, codigo: Annotated[str, Form()]):
    acesso(db, usuario, evento_id)
    try:
        c = checkin_por_codigo(db, evento_id, codigo)
    except ErroApp as e:
        return redirecionar(f"{base(evento_id)}/recepcao", e.mensagem, "erro")
    return _resultado_checkin(db, evento_id, c)


@router.post("/eventos/{evento_id}/recepcao/{convidado_id}", dependencies=[Csrf])
def checkin_manual(db: Db, usuario: Logado, evento_id: int, convidado_id: int):
    acesso(db, usuario, evento_id)
    try:
        c = fazer_checkin(db, evento_id, convidado_id)
    except ErroApp as e:
        return redirecionar(f"{base(evento_id)}/recepcao", e.mensagem, "erro")
    return _resultado_checkin(db, evento_id, c)


# --- página do convidado (pública: o link é a credencial) --------------------------------------


def _pelo_token(db: Db, token: str):
    try:
        return convidado_pelo_token(db, token)
    except ErroApp as e:
        raise ErroWeb(
            404, "Convite não encontrado", "Confira se o link está completo, ou peça um novo ao cerimonial."
        ) from e


@router.get("/c/{token}")
def convite(request: Request, db: Db, token: str):
    c, evento = _pelo_token(db, token)
    mesa = db.get(Mesa, c.mesa_id) if c.mesa_id else None
    resposta = pagina(
        request, "convite.html", c=c, evento=evento, mesa=mesa, token=token, encerrado=prazo_encerrado(evento)
    )
    resposta.headers["Cache-Control"] = "no-store"
    resposta.headers["Referrer-Policy"] = "no-referrer"  # não vazar o link para outros sites
    return resposta


@router.post("/c/{token}", dependencies=[Csrf])
def responder(
    db: Db,
    token: str,
    resposta: Annotated[str, Form()],
    restricao_alimentar: Annotated[str, Form()] = "",
):
    _pelo_token(db, token)
    if resposta not in ("confirmado", "recusado"):
        return redirecionar(f"/c/{token}", "Escolha uma das opções.", "erro")
    restricao = " ".join(restricao_alimentar.split())[:200] or None
    try:
        responder_convite(db, token, resposta, restricao)
    except ErroApp as e:
        return redirecionar(f"/c/{token}", e.mensagem, "erro")
    if resposta == "confirmado":
        return redirecionar(
            f"/c/{token}", "Presença confirmada! Guarde esta página: o QR code é a sua entrada."
        )
    return redirecionar(f"/c/{token}", "Resposta registrada. Obrigado por avisar!")


@router.get("/c/{token}/qr.svg")
def qr(db: Db, token: str):
    _pelo_token(db, token)
    buffer = io.BytesIO()
    segno.make(link_convite(token), error="m").save(buffer, kind="svg", scale=8, border=2, dark="#1f2a24")
    return Response(
        buffer.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "private, no-store"}
    )
