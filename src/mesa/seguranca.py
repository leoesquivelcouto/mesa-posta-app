"""Senhas, JWT e tokens de convite."""

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from mesa.config import config

ALGORITMO = "HS256"
# Custo do bcrypt: 12 em produção. Os testes usam 4 (o mínimo) para não demorar.
CUSTO_BCRYPT = int(os.environ.get("BCRYPT_CUSTO", "12"))


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt(CUSTO_BCRYPT)).decode()


def confere_senha(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode(), senha_hash.encode())
    except ValueError:
        return False


# Usado quando o e-mail não existe, para o login levar o mesmo tempo nos dois casos
# e não revelar quais e-mails têm conta.
HASH_FALSO = hash_senha(secrets.token_hex(16))


def criar_jwt(usuario_id: int, minutos: int | None = None) -> str:
    agora = datetime.now(UTC)
    c = config()
    validade = timedelta(minutes=minutos or c.jwt_minutos)
    dados = {"sub": str(usuario_id), "iat": agora, "exp": agora + validade}
    return jwt.encode(dados, c.segredo_jwt, algorithm=ALGORITMO)


def ler_jwt(token: str) -> int | None:
    """Devolve o id do usuário, ou None se o token for inválido, adulterado ou expirado."""
    try:
        dados = jwt.decode(
            token, config().segredo_jwt, algorithms=[ALGORITMO], options={"require": ["exp", "sub"]}
        )
        return int(dados["sub"])
    except (jwt.PyJWTError, ValueError):
        return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def novo_token_convite() -> tuple[str, str]:
    """Token de 256 bits (impossível de adivinhar) e o hash que vai para o banco."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)
