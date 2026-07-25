/**
 * openwebui-client.js — Cliente Open WebUI (CharlyPlugin · IA)
 * ===========================================================
 * Cliente minimo para el endpoint OpenAI-compatible de Open WebUI
 * (POST /api/chat/completions). Nativo del plugin: sin dependencias externas.
 *
 * Seguridad/robustez:
 *   - Timeout propio + soporte de AbortSignal externo (boton "Cancelar").
 *   - La credencial viaja en cabecera Authorization: Bearer (o un header custom,
 *     o un proxyUrl que la adjunte del lado servidor — recomendado en produccion).
 *   - Nunca registra la API key en consola.
 */
export class OpenWebUIClient {
  constructor(cfg = {}) {
    this.baseUrl = (cfg.baseUrl || "").replace(/\/+$/, "");
    this.chatPath = cfg.chatPath || "/api/chat/completions";
    this.modelsPath = cfg.modelsPath || "/api/models";
    this.model = cfg.model || "";
    this.apiKey = cfg.apiKey || "";
    this.proxyUrl = cfg.proxyUrl || "";
    this.customKeyHeader = cfg.customKeyHeader || "";
    this.timeout = cfg.requestTimeout || 45000;
  }

  _url(path) {
    return this.proxyUrl ? this.proxyUrl : this.baseUrl + path;
  }

  _headers() {
    const h = { "Content-Type": "application/json" };
    if (this.proxyUrl) return h; // el proxy adjunta la credencial del lado servidor
    if (this.customKeyHeader) h[this.customKeyHeader] = this.apiKey;
    else if (this.apiKey) h.Authorization = "Bearer " + this.apiKey;
    return h;
  }

  /** fetch con timeout interno y posibilidad de aborto externo. */
  async _fetch(url, opts = {}, externalSignal) {
    const ctrl = new AbortController();
    const onAbort = () => ctrl.abort();
    if (externalSignal) {
      if (externalSignal.aborted) ctrl.abort();
      else externalSignal.addEventListener("abort", onAbort, { once: true });
    }
    const timer = setTimeout(() => ctrl.abort(), this.timeout);
    try {
      const res = await fetch(url, { signal: ctrl.signal, mode: "cors", ...opts });
      if (!res.ok) {
        let detail = "";
        try {
          detail = (await res.text()).slice(0, 300);
        } catch {
          /* sin cuerpo */
        }
        throw new Error("HTTP " + res.status + (detail ? " — " + detail : ""));
      }
      return res;
    } finally {
      clearTimeout(timer);
      if (externalSignal) externalSignal.removeEventListener("abort", onAbort);
    }
  }

  /**
   * Envia la conversacion y devuelve el texto de la respuesta.
   * @param {Array<{role:string, content:string}>} messages
   * @param {AbortSignal} [signal]
   * @returns {Promise<string>}
   */
  async chat(messages, signal) {
    const body = JSON.stringify({ model: this.model, messages, stream: false });
    const res = await this._fetch(
      this._url(this.chatPath),
      { method: "POST", headers: this._headers(), body },
      signal
    );
    const data = await res.json();
    const msg = data && data.choices && data.choices[0] && data.choices[0].message;
    return (msg && msg.content) || "";
  }

  /**
   * Igual que chat() pero en streaming (SSE): invoca onToken con cada fragmento
   * y devuelve el texto completo. Si el endpoint no soporta stream, relanza para
   * que el llamador haga fallback a chat().
   * @param {Array<{role:string, content:string}>} messages
   * @param {AbortSignal} signal
   * @param {(delta:string, full:string)=>void} onToken
   * @returns {Promise<string>}
   */
  async chatStream(messages, signal, onToken) {
    const body = JSON.stringify({ model: this.model, messages, stream: true });
    const res = await this._fetch(
      this._url(this.chatPath),
      { method: "POST", headers: this._headers(), body },
      signal
    );
    if (!res.body || !res.body.getReader) {
      // Sin cuerpo legible: caemos a la respuesta no-stream.
      const data = await res.json();
      const full = (data?.choices?.[0]?.message?.content) || "";
      if (full && onToken) onToken(full, full);
      return full;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let full = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE: eventos separados por linea en blanco; cada uno con lineas "data:".
      const chunks = buffer.split("\n");
      buffer = chunks.pop() || ""; // ultima linea (posiblemente incompleta)
      for (const line of chunks) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const payload = trimmed.slice(5).trim();
        if (payload === "[DONE]") continue;
        try {
          const json = JSON.parse(payload);
          const delta = json?.choices?.[0]?.delta?.content || json?.choices?.[0]?.message?.content || "";
          if (delta) {
            full += delta;
            if (onToken) onToken(delta, full);
          }
        } catch {
          /* fragmento no-JSON (keep-alive, comentario): se ignora */
        }
      }
    }
    return full;
  }

  /** Comprueba conectividad/credencial sin enviar una conversacion. */
  async available(signal) {
    try {
      await this._fetch(this._url(this.modelsPath), { method: "GET", headers: this._headers() }, signal);
      return true;
    } catch {
      return false;
    }
  }
}
