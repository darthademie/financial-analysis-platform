// Solo en la app Android. MainActivity lo inserta en index.html antes que js/main.js.
//
// La interfaz llama a fetch("/api/..."), igual que en el navegador. Aquí esas llamadas se desvían a Python por
// el canal FinanzasBridge (WebViewCompat.addWebMessageListener, limitado al origen de la app), sin servidor ni puerto.
// Así app/static no necesita ningún cambio para funcionar en Android.
(() => {
  const bridge = window.FinanzasBridge;
  if (!bridge) return;

  const pending = new Map();
  let nextId = 1;

  bridge.onmessage = (event) => {
    const message = JSON.parse(event.data);
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    const body = message.base64 !== undefined ? fromBase64(message.base64) : message.text ?? "";
    waiter.resolve(new Response(body, { status: message.status, headers: message.headers }));
  };

  const send = (payload) => bridge.postMessage(JSON.stringify(payload));

  function fromBase64(text) {
    const binary = atob(text);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  const toBase64 = (blob) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).slice(String(reader.result).indexOf(",") + 1));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });

  async function envelope(url, init) {
    const headers = {};
    new Headers(init.headers ?? {}).forEach((value, key) => { headers[key] = value; });
    const message = { method: (init.method ?? "GET").toUpperCase(), path: url.pathname, query: url.search.slice(1), headers };
    if (init.body instanceof FormData) {
      const form = { fields: {}, files: {} };
      for (const [key, value] of init.body.entries()) {
        if (value instanceof Blob) form.files[key] = { name: value.name ?? "fichero", type: value.type, data: await toBase64(value) };
        else form.fields[key] = value;
      }
      message.form = form;
    } else if (typeof init.body === "string") {
      message.body = init.body;
    } else if (init.body != null) {
      throw new TypeError("Cuerpo de petición no soportado en la app Android.");
    }
    return message;
  }

  function callPython(url, init = {}) {
    return envelope(url, init).then((message) => new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      send({ kind: "request", id, ...message });
    }));
  }

  const isApi = (url) => url.origin === location.origin && url.pathname.startsWith("/api/");

  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href);
    return isApi(url) ? callPython(url, init) : nativeFetch(input, init);
  };

  // Descargas (exportar CSV): un enlace a /api/... no se puede abrir en el WebView, así que se pide a Python
  // y se guarda con el selector de Android. Se escucha en document para que el onclick del enlace ya haya
  // puesto su href.
  document.addEventListener("click", async (event) => {
    const link = event.target.closest?.("a[href]");
    if (!link) return;
    const url = new URL(link.getAttribute("href"), location.href);
    if (!isApi(url)) return;
    event.preventDefault();
    const response = await callPython(url);
    if (!response.ok) return;
    const disposition = response.headers.get("content-disposition") ?? "";
    const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? "descarga";
    const blob = await response.blob();
    send({ kind: "save", name, mime: blob.type || "application/octet-stream", data: await toBase64(blob) });
  });

  // Colores de las barras del sistema según el tema que esté usando la interfaz.
  const dark = matchMedia("(prefers-color-scheme: dark)");
  const reportTheme = () => {
    const theme = document.documentElement.dataset.theme;
    send({ kind: "theme", dark: theme ? theme === "dark" : dark.matches });
  };
  dark.addEventListener("change", reportTheme);
  new MutationObserver(reportTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  document.addEventListener("DOMContentLoaded", reportTheme);
})();
