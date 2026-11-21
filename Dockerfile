FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir .

RUN useradd --create-home mesa
USER mesa

EXPOSE 8000
# Aplica as migrações e sobe a API. --no-access-log: o log de acesso do uvicorn gravaria o token
# dos convites; a aplicação registra as requisições com o token mascarado.
CMD ["sh", "-c", "alembic upgrade head && uvicorn mesa.app:app --host 0.0.0.0 --port 8000 --no-access-log --proxy-headers --forwarded-allow-ips=*"]
