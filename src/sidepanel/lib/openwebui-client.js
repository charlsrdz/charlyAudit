/**
 * ai-client.js (exportado como OpenWebUIClient por compatibilidad)
 * ================================================================
 * Cliente multi-proveedor compatible con la API OpenAI (messages[] nativa).
 * Soporta: OpenWebUI, OpenAI/ChatGPT, Google Gemini, Anthropic Claude y
 * cualquier endpoint compatible con la especificacion OpenAI Chat Completions.
 *
 * Seguridad: la credencial viaja solo en cabeceras HTTP, nunca en el body.
 * Nunca se registra la API key en consola.
 */

/** Configuracion de cada proveedor conocido. */
export const PROVIDERS = {
  openwebui: {
    label: "Open WebUI (propio)",
    chatPath: "/api/chat/completions",
    modelsPath: "/api/models",
    requiresBaseUrl: true,
    defaultModel: "charly-pt",
  },
  openai: {
    label: "OpenAI / ChatGPT",
    baseUrl: "https://api.openai.com",
    chatPath: "/v1/chat/completions",
    modelsPath: "/v1/models",
    defaultModel: "gpt-4o-mini",
  },
  gemini: {
    label: "Google Gemini",
    baseUrl: "https://generativelanguage.googleapis.com",
    // Gemini usa endpoint OpenAI-compatible en /v1beta/openai
    chatPath: "/v1beta/openai/chat/completions",
    modelsPath: "/v1beta/openai/models",
    defaultModel: "gemini-2.0-flash",
  },
  claude: {
    label: "Anthropic Claude",
    baseUrl: "https://api.anthropic.com",
    chatPath: "/v1/messages",
    modelsPath: null, // sin endpoint de listado
    defaultModel: "claude-haiku-4-5-20251001",
    anthropicVersion: "2023-06-01",
  },
  custom: {
    label: "Proveedor personalizado",
    chatPath: "/v1/chat/completions",
    modelsPath: "/v1/models",
    requiresBaseUrl: true,
    defaultModel: "",
  },
};

export class OpenWebUIClient {
  /**
   * @param {object} cfg
   * @param {string} cfg.provider   - clave de PROVIDERS (openwebui|openai|gemini|claude|custom)
   * @param {string} cfg.baseUrl    - requerido para openwebui/custom; ignorado en proveedores con URL fija
   * @param {string} cfg.model
   * @param {string} cfg.apiKey
   * @param {string} [cfg.proxyUrl] - URL proxy que adjunta la credencial del lado servidor
   * @param {number} [cfg.temperature]
   * @param {number} [cfg.maxTokens]
   * @param {number} [cfg.requestTimeout]
   */
  constructor(cfg = {}) {
    this.provider = cfg.provider || "openwebui";
    const pDef = PROVIDERS[this.provider] || PROVIDERS.openwebui;
    this.baseUrl = (cfg.baseUrl || pDef.baseUrl || "").replace(/\/+$/, "");
    this.chatPath = cfg.chatPath || pDef.chatPath;
    this.modelsPath = cfg.modelsPath !== undefined ? cfg.modelsPath : pDef.modelsPath;
    this.model = cfg.model || pDef.defaultModel || "";
    this.apiKey = cfg.apiKey || "";
    this.proxyUrl = cfg.proxyUrl || "";
    this.temperature = cfg.temperature != null ? cfg.temperature : 0.7;
    this.maxTokens = cfg.maxTokens || 1024;
    this.timeout = cfg.requestTimeout || 60000;
    this._anthropicVersion = pDef.anthropicVersion || null;
  }

  _url(path) {
    return this.proxyUrl ? this.proxyUrl : this.baseUrl + path;
  }

  _headers() {
    const h = { "Content-Type": "application/json" };
    if (this.proxyUrl) return h; // el proxy adjunta la credencial
    if (this.provider === "claude") {
      // Anthropic usa x-api-key + version en vez de Authorization: Bearer
      if (this.apiKey) h["x-api-key"] = this.apiKey;
      if (this._anthropicVersion) h["anthropic-version"] = this._anthropicVersion;
    } else {
      if (this.apiKey) h.Authorization = "Bearer " + this.apiKey;
    }
    return h;
  }

  /**
   * Normaliza el array de mensajes al formato del proveedor activo.
   * Claude usa {role, content} pero el system message va en un campo separado,
   * y content puede ser string o array. Aqui se normaliza para que el codigo
   * de llamada siempre pase el formato OpenAI estandar.
   */
  _normalizeMessages(messages, extraParams = {}) {
    if (this.provider !== "claude") {
      return {
        model: this.model,
        messages,
        stream: extraParams.stream || false,
        temperature: this.temperature,
        max_tokens: this.maxTokens,
      };
    }
    // Claude: separa el system message y convierte el resto
    const sysMsg = messages.find((m) => m.role === "system");
    const rest = messages.filter((m) => m.role !== "system");
    const body = {
      model: this.model,
      messages: rest,
      stream: extraParams.stream || false,
      max_tokens: this.maxTokens,
    };
    if (sysMsg) body.system = sysMsg.content;
    return body;
  }

  async _fetch(url, opts = {}, externalSignal) {
    const ctrl = new AbortController();
    const onAbort = () => ctrl.abort();
    if (externalSignal) {
      if (externalSignal.aborted) { ctrl.abort(); }
      else externalSignal.addEventListener("abort", onAbort, { once: true });
    }
    const timer = setTimeout(() => ctrl.abort(), this.timeout);
    try {
      const res = await fetch(url, { signal: ctrl.signal, mode: "cors", ...opts });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.text()).slice(0, 300); } catch { /* sin cuerpo */ }
        throw new Error("HTTP " + res.status + (detail ? " — " + detail : ""));
      }
      return res;
    } finally {
      clearTimeout(timer);
      if (externalSignal) externalSignal.removeEventListener("abort", onAbort);
    }
  }

  /**
   * Envia la conversacion completa usando el array de mensajes nativo
   * (rol system/user/assistant). El historial viaja como mensajes reales,
   * no como texto incrustado en el ultimo user message.
   * @param {Array<{role:string, content:string}>} messages
   * @param {AbortSignal} [signal]
   */
  async chat(messages, signal) {
    const body = JSON.stringify(this._normalizeMessages(messages));
    const res = await this._fetch(this._url(this.chatPath), { method: "POST", headers: this._headers(), body }, signal);
    const data = await res.json();
    // Claude devuelve data.content[0].text; OpenAI devuelve data.choices[0].message.content
    if (this.provider === "claude") {
      return (data?.content?.[0]?.text) || "";
    }
    return (data?.choices?.[0]?.message?.content) || "";
  }

  /**
   * Streaming nativo (SSE). Usa el array de mensajes completo con roles reales.
   */
  async chatStream(messages, signal, onToken) {
    const body = JSON.stringify(this._normalizeMessages(messages, { stream: true }));
    const res = await this._fetch(this._url(this.chatPath), { method: "POST", headers: this._headers(), body }, signal);

    if (!res.body || !res.body.getReader) {
      const data = await res.json();
      const full = this.provider === "claude"
        ? (data?.content?.[0]?.text || "")
        : (data?.choices?.[0]?.message?.content || "");
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
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const payload = trimmed.slice(5).trim();
        if (payload === "[DONE]") continue;
        try {
          const json = JSON.parse(payload);
          // Gemini/OpenAI: choices[0].delta.content
          // Claude streaming: delta.text en content_block_delta
          const delta =
            json?.choices?.[0]?.delta?.content ||
            json?.choices?.[0]?.message?.content ||
            json?.delta?.text || // Anthropic SSE
            "";
          if (delta) { full += delta; if (onToken) onToken(delta, full); }
        } catch { /* fragmento no-JSON */ }
      }
    }
    return full;
  }

  /** Comprueba conectividad y credencial. */
  async available(signal) {
    if (!this.modelsPath) {
      // Proveedor sin endpoint de modelos (Claude): ping con un mensaje mínimo
      try {
        await this.chat([{ role: "user", content: "ping" }], signal);
        return true;
      } catch { return false; }
    }
    try {
      await this._fetch(this._url(this.modelsPath), { method: "GET", headers: this._headers() }, signal);
      return true;
    } catch { return false; }
  }
}
