"""Aplicação: telas (/) e API JSON (/api). Rodar com: uvicorn mesa.app:app --no-access-log"""

import logging
import re
import time
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from mesa import __version__, erros
from mesa.deps import Db
from mesa.rotas import auth, convidados, convites, eventos, mesas
from mesa.web import paginas
from mesa.web.sessao import ErroWeb, PrecisaEntrar, garantir_csrf, gravar_cookie_csrf

logger = logging.getLogger("mesa")

# Nunca registrar o token do convite: ele é a credencial do convidado.
_TOKEN_NA_URL = re.compile(r"(/convite/|/c/)[^/]+")

CABECALHOS_SEGURANCA = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
}


def criar_app() -> FastAPI:
    app = FastAPI(
        title="Mesa Posta",
        version=__version__,
        description="Convidados, confirmação de presença, mesas e check-in para cerimonial de casamentos.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    erros.registrar(app)

    @app.exception_handler(ErroWeb)
    async def _erro_web(request: Request, e: ErroWeb) -> Response:
        return paginas.pagina(request, "erro.html", e.status, titulo=e.titulo, mensagem=e.mensagem)

    @app.exception_handler(PrecisaEntrar)
    async def _precisa_entrar(_: Request, e: PrecisaEntrar) -> Response:
        return RedirectResponse(f"/entrar?proximo={e.destino}", status_code=303)

    @app.middleware("http")
    async def seguranca_e_registro(request: Request, call_next) -> Response:
        inicio = time.perf_counter()
        novo_csrf = garantir_csrf(request)
        resposta = await call_next(request)
        if novo_csrf:
            gravar_cookie_csrf(resposta, novo_csrf)
        for nome, valor in CABECALHOS_SEGURANCA.items():
            resposta.headers.setdefault(nome, valor)
        caminho = _TOKEN_NA_URL.sub(r"\1***", request.url.path)
        duracao = (time.perf_counter() - inicio) * 1000
        logger.info("%s %s %s %.0fms", request.method, caminho, resposta.status_code, duracao)
        return resposta

    for modulo in (auth, eventos, mesas, convidados, convites):
        app.include_router(modulo.router, prefix="/api")
    app.include_router(paginas.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")

    @app.get("/saude", tags=["infra"])
    def saude(db: Db) -> dict[str, str]:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "versao": __version__}

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = criar_app()
