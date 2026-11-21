"""Sessão por cookie, proteção contra CSRF, avisos entre páginas e erros das telas.

- Sessão: JWT assinado num cookie HttpOnly e SameSite=Lax (JavaScript não lê; outros sites não enviam).
- CSRF: "double submit". Todo visitante recebe um cookie `csrf` aleatório, e todo formulário POST
  precisa reenviar o mesmo valor num campo oculto. Outro site não consegue ler o cookie para copiar o valor.
- Avisos: a mensagem de sucesso ou erro vai num cookie curto e aparece na página seguinte (padrão PRG).
"""

import json
import secrets
from urllib.parse import quote, unquote

from fastapi import Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from mesa.config import config
from mesa.seguranca import criar_jwt, ler_jwt

COOKIE_SESSAO = "mp_sessao"
COOKIE_CSRF = "mp_csrf"
COOKIE_AVISO = "mp_aviso"
DURACAO_SESSAO_MIN = 12 * 60


class ErroWeb(Exception):
    """Erro mostrado numa página (404, 403...) em vez de JSON."""

    def __init__(self, status: int, titulo: str, mensagem: str):
        self.status, self.titulo, self.mensagem = status, titulo, mensagem


class PrecisaEntrar(Exception):
    """Página que exige login acessada sem sessão: leva para /entrar e volta depois."""

    def __init__(self, destino: str):
        self.destino = destino


def _seguro() -> bool:
    return config().url_publica.startswith("https://")


def iniciar_sessao(resposta: RedirectResponse, usuario_id: int) -> RedirectResponse:
    resposta.set_cookie(
        COOKIE_SESSAO,
        criar_jwt(usuario_id, DURACAO_SESSAO_MIN),
        max_age=DURACAO_SESSAO_MIN * 60,
        httponly=True,
        samesite="lax",
        secure=_seguro(),
    )
    return resposta


def encerrar_sessao(resposta: RedirectResponse) -> RedirectResponse:
    resposta.delete_cookie(COOKIE_SESSAO)
    return resposta


def usuario_id_da_sessao(request: Request) -> int | None:
    token = request.cookies.get(COOKIE_SESSAO)
    return ler_jwt(token) if token else None


def token_csrf(request: Request) -> str:
    """O token desta visita (criado no middleware se o navegador ainda não tinha)."""
    return request.state.csrf


def garantir_csrf(request: Request) -> str | None:
    """Chamado pelo middleware: devolve um token novo se for preciso gravar o cookie."""
    atual = request.cookies.get(COOKIE_CSRF)
    if atual and len(atual) >= 32:
        request.state.csrf = atual
        return None
    request.state.csrf = secrets.token_urlsafe(32)
    return request.state.csrf


def gravar_cookie_csrf(resposta, token: str) -> None:
    resposta.set_cookie(COOKIE_CSRF, token, max_age=30 * 24 * 3600, samesite="lax", secure=_seguro())


async def verificar_csrf(request: Request) -> None:
    formulario = await request.form()
    enviado = formulario.get("csrf")
    esperado = request.cookies.get(COOKIE_CSRF)
    if not esperado or not isinstance(enviado, str) or not secrets.compare_digest(enviado, esperado):
        raise ErroWeb(
            403,
            "Formulário expirado",
            "Por segurança, recarregue a página e envie de novo.",
        )


def redirecionar(url: str, aviso: str | None = None, tipo: str = "ok") -> RedirectResponse:
    resposta = RedirectResponse(url, status_code=303)
    if aviso:
        valor = quote(json.dumps({"tipo": tipo, "texto": aviso}, ensure_ascii=False))
        resposta.set_cookie(COOKIE_AVISO, valor, max_age=60, httponly=True, samesite="lax", secure=_seguro())
    return resposta


def ler_aviso(request: Request) -> dict | None:
    bruto = request.cookies.get(COOKIE_AVISO)
    if not bruto:
        return None
    try:
        aviso = json.loads(unquote(bruto))
        return aviso if isinstance(aviso, dict) and "texto" in aviso else None
    except ValueError:
        return None


_CAMPOS = {
    "nome": "Nome",
    "email": "E-mail",
    "senha": "Senha",
    "telefone": "Telefone",
    "grupo": "Grupo",
    "restricao_alimentar": "Restrição alimentar",
    "capacidade": "Lugares",
    "data": "Data",
    "local": "Local",
    "prazo_rsvp": "Prazo para confirmar",
    "papel": "Papel",
}


def mensagens(erro: ValidationError) -> str:
    """Erros do Pydantic em português, para mostrar ao usuário."""
    partes = []
    for e in erro.errors():
        campo = _CAMPOS.get(str(e["loc"][-1]) if e["loc"] else "", "Campo")
        tipo, ctx = e["type"], e.get("ctx") or {}
        if tipo == "string_too_short":
            texto = f"precisa ter pelo menos {ctx.get('min_length')} caracteres"
        elif tipo == "string_too_long":
            texto = f"pode ter no máximo {ctx.get('max_length')} caracteres"
        elif tipo in ("missing", "string_type") or (tipo == "value_error" and "email" in str(e["loc"])):
            texto = "é obrigatório" if tipo != "value_error" else "não parece um e-mail válido"
        elif tipo in ("greater_than_equal", "less_than_equal", "int_parsing"):
            texto = "precisa ser um número entre 1 e 50" if campo == "Lugares" else "valor inválido"
        elif tipo in ("date_from_datetime_parsing", "date_parsing", "datetime_parsing", "timezone_aware"):
            texto = "data inválida"
        elif tipo == "value_error":
            texto = str(e["msg"]).removeprefix("Value error, ")
        else:
            texto = "valor inválido"
        partes.append(f"{campo}: {texto}.")
    return " ".join(partes)
