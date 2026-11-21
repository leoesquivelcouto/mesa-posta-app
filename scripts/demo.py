"""Cria um casamento fictício para demonstração local (nomes inventados; nenhum dado real).

Uso: python scripts/demo.py
Contas criadas (só no banco local):
  cerimonial@demo.local  e  recepcao@demo.local   senha: mesaposta-demo
Os links de alguns convidados vão para logs/demo-links.txt.
"""

import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from mesa.db import fabrica
from mesa.modelos import Convidado, Evento, Membro, Mesa, Usuario
from mesa.seguranca import hash_senha, novo_token_convite
from mesa.servicos import link_convite

SENHA = "mesaposta-demo"
NOMES = """
Ana Beatriz Rocha, Bruno Carvalho, Camila Duarte, Daniel Freitas, Eduarda Gomes, Felipe Andrade,
Gabriela Lima, Henrique Pacheco, Isabela Moura, João Pedro Alves, Karina Teixeira, Lucas Batista,
Mariana Castro, Nicolas Prado, Olívia Martins, Paulo Henrique Dias, Rafaela Nunes, Samuel Cardoso,
Tatiane Ribeiro, Vinícius Lopes, Yasmin Barros,
Arthur Monteiro, Beatriz Farias, Caio Mendes, Débora Sales, Enzo Rezende, Fernanda Pires, Gustavo Vieira,
Helena Campos, Igor Fonseca, Júlia Antunes, Kevin Moraes, Larissa Correia, Matheus Brito, Natália Sampaio,
Otávio Guimarães, Patrícia Lacerda, Renato Siqueira, Sabrina Pinheiro, Thiago Queiroz, Úrsula Amaral,
Vitor Hugo Seixas, Wagner Tavares, Ximena Rangel, Yuri Bastos, Zuleica Paiva, Alice Viana, Bernardo Coelho,
Cecília Torres, Diego Sardinha, Elisa Peixoto, Fábio Neves, Giovana Lacerda, Heitor Barreto, Ingrid Falcão,
Jorge Luiz Pimenta, Lívia Moreira, Marcelo Aguiar, Nina Fontes, Otávio Lemos Filho
"""
NOMES = [n.strip() for n in NOMES.replace("\n", " ").split(",")]
GRUPOS = [
    "Família da noiva",
    "Família do noivo",
    "Amigos da faculdade",
    "Trabalho da noiva",
    "Trabalho do noivo",
]


def conta(db, nome: str, email: str) -> Usuario:
    usuario = db.scalar(select(Usuario).where(Usuario.email == email))
    if usuario is None:
        usuario = Usuario(nome=nome, email=email, senha_hash=hash_senha(SENHA))
        db.add(usuario)
        db.flush()
    return usuario


def main() -> None:
    aleatorio = random.Random(42)
    with fabrica()() as db:
        cerimonial = conta(db, "Carla (cerimonial)", "cerimonial@demo.local")
        recepcao = conta(db, "Renato (recepção)", "recepcao@demo.local")
        evento = Evento(
            nome="Casamento de Júlia & Rafael",
            data=date.today() + timedelta(days=45),
            local="Espaço Jardim das Flores, Brasília",
            prazo_rsvp=datetime.now(UTC) + timedelta(days=30),
        )
        db.add(evento)
        db.flush()
        db.add_all(
            [
                Membro(evento_id=evento.id, usuario_id=cerimonial.id, papel="cerimonial"),
                Membro(evento_id=evento.id, usuario_id=recepcao.id, papel="recepcao"),
            ]
        )
        mesas = [Mesa(evento_id=evento.id, nome=f"Mesa {i}", capacidade=8) for i in range(1, 8)]
        mesas.append(Mesa(evento_id=evento.id, nome="Mesa dos noivos", capacidade=10))
        db.add_all(mesas)
        db.flush()

        links = []
        ocupacao = {m.id: 0 for m in mesas}
        for i, nome in enumerate(NOMES):
            sorteio = aleatorio.random()
            resposta = "confirmado" if sorteio < 0.62 else "recusado" if sorteio < 0.75 else "pendente"
            token, token_hash = novo_token_convite()
            c = Convidado(
                evento_id=evento.id,
                nome=nome,
                telefone=f"6199{aleatorio.randint(1000000, 9999999)}",
                grupo=GRUPOS[i % len(GRUPOS)],
                resposta=resposta,
                respondido_em=datetime.now(UTC) - timedelta(days=aleatorio.randint(1, 10))
                if resposta != "pendente"
                else None,
                restricao_alimentar=aleatorio.choice(
                    [None] * 6 + ["vegetariana", "sem lactose", "sem glúten"]
                )
                if resposta == "confirmado"
                else None,
                token_hash=token_hash,
            )
            if resposta == "confirmado" and aleatorio.random() < 0.8:
                livres = [m for m in mesas if ocupacao[m.id] < m.capacidade]
                if livres:
                    mesa = aleatorio.choice(livres)
                    c.mesa_id = mesa.id
                    ocupacao[mesa.id] += 1
            db.add(c)
            if len(links) < 6:
                links.append(f"{nome} ({resposta}): {link_convite(token)}")
        db.commit()

    saida = Path("logs")
    saida.mkdir(exist_ok=True)
    (saida / "demo-links.txt").write_text("\n".join(links) + "\n", encoding="utf-8")
    print(f"Evento de demonstração criado (id {evento.id}). Entre com cerimonial@demo.local / {SENHA}")
    print(f"Links de convidados em {saida / 'demo-links.txt'}")


if __name__ == "__main__":
    main()
