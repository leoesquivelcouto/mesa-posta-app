// Pequenas melhorias; todas as páginas funcionam também sem JavaScript.
"use strict";

// Confirmação antes de ações destrutivas: <form data-confirmar="Tem certeza?">
document.addEventListener("submit", (e) => {
  const msg = e.target.dataset && e.target.dataset.confirmar;
  if (msg && !window.confirm(msg)) e.preventDefault();
});

// Copiar link: <button data-copiar="https://...">
document.addEventListener("click", async (e) => {
  const botao = e.target.closest("[data-copiar]");
  if (!botao) return;
  try {
    await navigator.clipboard.writeText(botao.dataset.copiar);
    const original = botao.textContent;
    botao.textContent = "Copiado!";
    setTimeout(() => (botao.textContent = original), 1800);
  } catch {
    window.prompt("Copie o link:", botao.dataset.copiar);
  }
});

// Baixar a tabela de links como planilha (gerada no navegador; os links não ficam salvos no servidor).
document.addEventListener("click", (e) => {
  const botao = e.target.closest("[data-baixar-csv]");
  if (!botao) return;
  const tabela = document.querySelector(botao.dataset.baixarCsv);
  const campo = (t) => `"${String(t).replace(/"/g, '""')}"`;
  const linhas = [["nome", "telefone", "link"].join(";")];
  tabela.querySelectorAll("tbody tr").forEach((tr) => {
    const td = tr.querySelectorAll("td");
    linhas.push([td[0].textContent.trim(), td[1].textContent.trim(), td[2].querySelector("input").value].map(campo).join(";"));
  });
  const blob = new Blob(["﻿" + linhas.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: botao.dataset.nome || "convites.csv" });
  a.click();
  URL.revokeObjectURL(a.href);
});

// Avisos de sucesso somem sozinhos.
document.querySelectorAll(".aviso-ok[data-aviso]").forEach((el) => setTimeout(() => el.remove(), 6000));
