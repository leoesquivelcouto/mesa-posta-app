// Leitor de QR code da recepção: usa a câmera do celular e a biblioteca jsQR (servida localmente,
// funciona mesmo com internet ruim no salão). Ao ler um código, envia o formulário de check-in.
"use strict";

(function () {
  const caixa = document.querySelector("[data-leitor]");
  const botao = document.querySelector("[data-abrir-camera]");
  const form = document.querySelector("[data-form-codigo]");
  if (!caixa || !botao || !form) return;

  const video = caixa.querySelector("video");
  const canvas = caixa.querySelector("canvas");
  const estado = caixa.querySelector(".camera-estado");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  let fluxo = null;
  let enviado = false;

  function parar() {
    if (fluxo) fluxo.getTracks().forEach((t) => t.stop());
    fluxo = null;
    caixa.classList.remove("ativa");
    botao.textContent = "Abrir câmera";
  }

  function ler() {
    if (!fluxo || enviado) return;
    if (video.readyState === video.HAVE_ENOUGH_DATA && window.jsQR) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const imagem = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const qr = window.jsQR(imagem.data, imagem.width, imagem.height, { inversionAttempts: "dontInvert" });
      if (qr && qr.data && qr.data.length >= 10) {
        enviado = true;
        estado.textContent = "QR code lido. Registrando entrada...";
        if (navigator.vibrate) navigator.vibrate(120);
        form.querySelector("[name=codigo]").value = qr.data;
        sessionStorage.setItem("camera-aberta", "1"); // reabre sozinha para o próximo convidado
        parar();
        form.submit();
        return;
      }
    }
    requestAnimationFrame(ler);
  }

  botao.addEventListener("click", async () => {
    if (fluxo) {
      sessionStorage.setItem("camera-aberta", "0");
      return parar();
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      estado.textContent = "Este navegador não dá acesso à câmera. Use a busca pelo nome.";
      return;
    }
    try {
      fluxo = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
      video.srcObject = fluxo;
      await video.play();
      caixa.classList.add("ativa");
      botao.textContent = "Fechar câmera";
      estado.textContent = "Aponte para o QR code do convidado.";
      requestAnimationFrame(ler);
    } catch (erro) {
      parar();
      estado.textContent = erro && erro.name === "NotAllowedError"
        ? "Permissão da câmera negada. Libere nas configurações do navegador ou use a busca pelo nome."
        : "Não foi possível abrir a câmera. Use a busca pelo nome.";
    }
  });

  // Depois de cada entrada pela câmera, a página recarrega com o resultado e a câmera reabre sozinha.
  if (sessionStorage.getItem("camera-aberta") === "1") botao.click();
})();
