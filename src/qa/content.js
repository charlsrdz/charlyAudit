/**
 * content.js — Content Script (mundo aislado)
 * ===========================================
 * Puente DOM <-> extension. Cubre la Herramienta 1 (clicks + inputs), parte de
 * la 6 (throttling/debouncing + enmascarado de datos) y la 5 (persistencia del
 * buffer en beforeunload). Inyecta injected.js y reenvia TODO al service worker.
 *
 * Mensajes hacia el SW:  { channel:"qa", entry, resumed? }
 * Mensajes desde el SW:  { channel:"qa-config", recording, config }
 */
(() => {
  "use strict";

  if (window.__charlyQAContentLoaded) return;
  window.__charlyQAContentLoaded = true;

  const FROM_INJECTED = "charly-injected";
  const TO_INJECTED = "charly-content";
  const RESUME_KEY = "__charlyQA_resume";

  const local = {
    recording: false,
    injectedReady: false,
    pendingConfigForPage: null,
    pageBuffer: [], // respaldo para beforeunload (Herramienta 5)
    lastInput: { selector: null, value: null },
    lastUserAction: null, // {type, selector, ts} para correlacionar errores
    replaying: false,
    config: {
      watchedGlobals: [],
      patchedFunctions: [],
      sensitiveParams: ["token", "access_token", "auth", "key", "apikey", "password", "secret"],
      maskSelectors: [".private", "[data-private]"],
      throttleScrollMs: 250,
      debounceResizeMs: 300,
    },
  };

  // === 1. injected.js en el mundo MAIN =======================================
  // NO se inyecta aqui dinamicamente: el manifest lo declara como content script
  // con "world": "MAIN" y run_at "document_start", de modo que Chrome lo ejecuta
  // en el contexto real de la pagina ANTES que los scripts del sitio. Eso es
  // imprescindible para que los interceptores de fetch/XHR y window.onerror
  // queden instalados a tiempo. La coordinacion se hace por postMessage (abajo).

  // === Utilidades de mensajeria ==============================================
  const uuid = () =>
    crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;

  function entryOf(type, data) {
    return { id: uuid(), type, ts: Date.now(), data };
  }

  function sendToSW(entry, resumed = false) {
    try {
      chrome.runtime.sendMessage({ channel: "qa", entry, resumed }).catch(() => {});
    } catch {
      /* contexto de extension invalidado tras recarga: se reintenta al cargar */
    }
  }

  /** Captura un evento: lo envia al SW y lo respalda en el buffer de pagina. */
  // Metadata fina de navegacion: distingue full load / reload / back_forward,
  // captura el referrer y el numero de redirects (Navigation Timing).
  function navInfo() {
    const out = { tipo: "full", referrer: document.referrer || null };
    try {
      const nav = performance.getEntriesByType("navigation")[0];
      if (nav) {
        out.tipo = nav.type === "navigate" ? "full" : nav.type; // reload | back_forward
        out.redirects = nav.redirectCount || 0;
        out.ttfbMs = Math.round(nav.responseStart || 0);
        out.domListoMs = Math.round(nav.domContentLoadedEventEnd || 0);
      }
    } catch {
      /* sin Navigation Timing */
    }
    return out;
  }

  // Firma estructural ligera del DOM para baseline/diff (tarea 1). Sin HTML ni
  // texto sensible: solo titulo, URL y conteo de nodos (huella comparable).
  const BASELINE_TYPES = new Set(["click", "dblclick", "middleclick", "input", "key", "dragdrop"]);
  function domSignature() {
    return { title: document.title, nodes: document.getElementsByTagName("*").length, url: location.href };
  }

  function capture(type, data) {
    if (local.replaying) return; // no grabar los eventos sinteticos del replay
    if (!local.recording) return;
    if (BASELINE_TYPES.has(type)) data = { ...data, baseline: domSignature() };
    const entry = entryOf(type, data);
    sendToSW(entry);
    local.pageBuffer.push(entry);
    if (local.pageBuffer.length > 500) local.pageBuffer.shift();
  }

  function postToInjected(type, data) {
    window.postMessage({ __charly: true, source: TO_INJECTED, type, data }, "*");
  }

  /**
   * Registra la ultima accion del usuario y la comparte con injected.js para que
   * los errores/red puedan correlacionarse cronologicamente con ella (Herr. 3/4).
   */
  function markUserAction(type, selector) {
    local.lastUserAction = { type, selector, ts: Date.now() };
    postToInjected("last-action", local.lastUserAction);
  }

  // Peticiones de bloque de codigo bajo demanda (requestId -> callback).
  const pendingSource = new Map();
  let sourceReq = 0;

  // === Selector CSS robusto (Herramienta 1) ==================================
  const DYNAMIC_CLASS = [
    /^css-[a-z0-9]+$/i, // emotion
    /^sc-[a-zA-Z0-9]+$/, // styled-components
    /^jsx-\d+$/, // styled-jsx
    /__[A-Za-z0-9]{5,}$/, // CSS modules (Btn_root__a1B2c)
    /[0-9a-f]{6,}/i, // hash-like
    /^[a-z0-9]{10,}$/i, // token opaco
  ];
  const DYNAMIC_ID = [/^ember\d+$/, /^react-/, /^:r[0-9a-z]+:?$/i, /^[0-9a-f]{8}-[0-9a-f]{4}-/i, /\d{4,}/];

  const isDynamicClass = (c) => !c || c.length > 32 || DYNAMIC_CLASS.some((r) => r.test(c));
  const isDynamicId = (id) => !id || id.length > 40 || DYNAMIC_ID.some((r) => r.test(id));

  function closestElement(node) {
    while (node && node.nodeType !== 1) node = node.parentNode || node.host;
    return node && node.nodeType === 1 ? node : null;
  }

  const escAttr = (v) => String(v).replace(/\\/g, "\\\\").replace(/"/g, '\\"');

  function isUnique(selector) {
    try {
      return document.querySelectorAll(selector).length === 1;
    } catch {
      return false;
    }
  }

  function stableClasses(el) {
    return Array.from(el.classList).filter((c) => !isDynamicClass(c));
  }

  function hierarchicalSelector(el) {
    const segments = [];
    let node = el;
    let depth = 0;
    while (node && node.nodeType === 1 && node !== document.documentElement && depth < 10) {
      depth += 1;
      if (node.id && !isDynamicId(node.id)) {
        segments.unshift(`#${CSS.escape(node.id)}`);
        break;
      }
      let seg = node.tagName.toLowerCase();
      const classes = stableClasses(node).slice(0, 2);
      if (classes.length) seg += "." + classes.map((c) => CSS.escape(c)).join(".");
      const parent = node.parentElement;
      if (parent) {
        const sameTag = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (sameTag.length > 1) seg += `:nth-of-type(${sameTag.indexOf(node) + 1})`;
      }
      segments.unshift(seg);
      node = node.parentElement;
    }
    return segments.join(" > ");
  }

  /**
   * Genera el selector mas estable posible priorizando atributos de QA.
   * @param {Node} target
   * @returns {string}
   */
  function robustSelector(target) {
    const el = closestElement(target);
    if (!el) return "";

    // 1) Atributos pensados para pruebas (los mas estables).
    for (const attr of ["data-qa", "data-testid", "data-cy", "data-test"]) {
      const v = el.getAttribute(attr);
      if (v) {
        const sel = `[${attr}="${escAttr(v)}"]`;
        if (isUnique(sel)) return sel;
      }
    }
    // 2) id no dinamico.
    if (el.id && !isDynamicId(el.id)) {
      const sel = `#${CSS.escape(el.id)}`;
      if (isUnique(sel)) return sel;
    }
    // 3) name de campos de formulario.
    const name = el.getAttribute("name");
    if (name) {
      const sel = `${el.tagName.toLowerCase()}[name="${escAttr(name)}"]`;
      if (isUnique(sel)) return sel;
    }
    // 4) Ruta jerarquica con clases estables.
    return hierarchicalSelector(el);
  }

  // Ancla semantica: identifica el elemento por SIGNIFICADO (rol/aria/texto/name),
  // no por posicion. Es el fallback que elimina casi todos los "no encontrado" en
  // replay cuando el selector posicional (nth-of-type) cambia por re-render.
  function anchorOf(target, masked) {
    const el = closestElement(target);
    if (!el) return null;
    const implicit = { A: "link", BUTTON: "button", INPUT: "textbox", SELECT: "combobox", TEXTAREA: "textbox" }[el.tagName];
    const a = {
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute("role") || implicit || undefined,
      name: el.getAttribute("name") || undefined,
      type: el.getAttribute("type") || undefined,
      ph: el.getAttribute("placeholder") || undefined,
    };
    if (!masked) {
      a.aria = el.getAttribute("aria-label") || undefined;
      a.text = (el.textContent || "").trim().slice(0, 60) || undefined;
    }
    return a;
  }

  // === Arbol de origen (Herramienta 3): describe el camino del evento =========
  function descriptor(el) {
    if (!el || el.nodeType !== 1) return null;
    let s = el.tagName.toLowerCase();
    if (el.id && !isDynamicId(el.id)) s += "#" + el.id;
    const cls = Array.from(el.classList || []).filter((c) => !isDynamicClass(c)).slice(0, 2);
    if (cls.length) s += "." + cls.join(".");
    return s;
  }
  /** Arbol de origen como cadena compacta "div#app > table > button.del". */
  function pathTree(nodes) {
    const arr = (Array.isArray(nodes) ? nodes : [])
      .filter((n) => n instanceof Element)
      .slice(0, 8)
      .map(descriptor)
      .filter(Boolean);
    return arr.length ? arr.join(" > ") : null;
  }
  /** Arbol de origen (cadena) a partir de los ancestros de un elemento. */
  function ancestorTree(el) {
    const out = [];
    let n = el;
    let d = 0;
    while (n && n.nodeType === 1 && d < 8) {
      const desc = descriptor(n);
      if (desc) out.push(desc);
      n = n.parentElement;
      d += 1;
    }
    return out.length ? out.join(" > ") : null;
  }

  // === Enmascarado de datos (Herramienta 6 — privacidad) =====================
  function shouldMask(el) {
    if (!el || el.nodeType !== 1) return false;
    const isPassword = el.tagName === "INPUT" && (el.type || "").toLowerCase() === "password";
    const inPrivate = el.closest && el.closest(local.config.maskSelectors.join(","));
    return Boolean(isPassword || inPrivate);
  }

  // === Clicks (Herramienta 1) ================================================
  document.addEventListener(
    "click",
    (event) => {
      const path = event.composedPath();
      const target = closestElement(path.find((n) => n instanceof Element) || event.target);
      if (!target) return;

      const masked = shouldMask(target);
      const selector = robustSelector(target);
      capture("click", {
        selector,
        tag: target.tagName.toLowerCase(),
        text: masked ? "***" : (target.textContent || "").trim().slice(0, 80),
        anchor: anchorOf(target, masked),
        position: { x: event.clientX, y: event.clientY },
        button: event.button,
        path: pathTree(path),
        pathDepth: path.filter((n) => n instanceof Element).length,
      });
      markUserAction("click", selector);

      // Herramienta 4: snapshot del estado global en cada click.
      postToInjected("snapshot-globals", { trigger: "click" });
    },
    true
  );

  // === Inputs: change + blur (Herramienta 1) =================================
  function captureInput(el) {
    if (!el || !/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
    const masked = shouldMask(el);
    const value = masked ? "***" : String(el.value ?? "");
    // Dedupe de eventos consecutivos identicos (change + blur del mismo campo).
    const selector = robustSelector(el);
    if (local.lastInput.selector === selector && local.lastInput.value === value) return;
    local.lastInput = { selector, value };

    capture("input", {
      selector,
      tag: el.tagName.toLowerCase(),
      inputType: el.tagName === "INPUT" ? (el.type || "text") : el.tagName.toLowerCase(),
      value,
      masked,
      anchor: anchorOf(el, masked),
      path: ancestorTree(el),
    });
    markUserAction("input", selector);
  }
  document.addEventListener("change", (e) => captureInput(closestElement(e.target)), true);
  document.addEventListener("blur", (e) => captureInput(closestElement(e.target)), true);

  // === Raton ampliado: doble click, click central, drag & drop (Herr. 2) =====
  document.addEventListener(
    "dblclick",
    (event) => {
      const el = closestElement(event.composedPath().find((n) => n instanceof Element) || event.target);
      if (!el) return;
      const selector = robustSelector(el);
      capture("dblclick", { selector, tag: el.tagName.toLowerCase(), position: { x: event.clientX, y: event.clientY }, path: pathTree(event.composedPath()) });
      markUserAction("dblclick", selector);
    },
    true
  );
  // auxclick: button 1 = boton central.
  document.addEventListener(
    "auxclick",
    (event) => {
      if (event.button !== 1) return;
      const el = closestElement(event.composedPath().find((n) => n instanceof Element) || event.target);
      if (!el) return;
      const selector = robustSelector(el);
      capture("middleclick", { selector, tag: el.tagName.toLowerCase(), position: { x: event.clientX, y: event.clientY }, path: pathTree(event.composedPath()) });
      markUserAction("middleclick", selector);
    },
    true
  );
  // Drag & drop: registra origen en dragstart y emite el evento al soltar.
  let dragFrom = null;
  document.addEventListener(
    "dragstart",
    (e) => {
      dragFrom = robustSelector(closestElement(e.target));
    },
    true
  );
  document.addEventListener(
    "drop",
    (e) => {
      const to = robustSelector(closestElement(e.composedPath().find((n) => n instanceof Element) || e.target));
      capture("dragdrop", { from: dragFrom, to, position: { x: e.clientX, y: e.clientY }, path: pathTree(e.composedPath()) });
      markUserAction("dragdrop", to);
      dragFrom = null;
    },
    true
  );

  // === Teclado: coalescing de typing + teclas/atajos (Herr. 2/6) =============
  // El texto tecleado en un mismo campo se agrupa en una sola entrada "run"; las
  // teclas no imprimibles (Enter, Tab, flechas…) y los atajos (con modificador)
  // se emiten al momento. Asi no se inflan miles de eventos por escribir.
  let keyRun = null; // { selector, text, masked, path }
  let keyRunTimer = null;
  function flushKeyRun() {
    clearTimeout(keyRunTimer);
    keyRunTimer = null;
    if (keyRun && keyRun.text) {
      capture("key", {
        run: true,
        text: keyRun.masked ? "***" : keyRun.text,
        selector: keyRun.selector,
        masked: keyRun.masked,
        path: keyRun.path,
      });
    }
    keyRun = null;
  }
  document.addEventListener(
    "keydown",
    (event) => {
      const el = closestElement(event.target);
      const selector = el ? robustSelector(el) : null;
      const masked = shouldMask(el);
      const printable = event.key && event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey;
      if (printable) {
        if (keyRun && keyRun.selector !== selector) flushKeyRun();
        if (!keyRun) keyRun = { selector, text: "", masked, path: el ? ancestorTree(el) : null };
        keyRun.text += event.key;
        markUserAction("key", selector);
        clearTimeout(keyRunTimer);
        keyRunTimer = setTimeout(flushKeyRun, 700); // cierra el run tras una pausa
        return;
      }
      // Tecla no imprimible o atajo (modificador): cierra el run y la emite.
      flushKeyRun();
      capture("key", {
        key: event.key,
        code: event.code,
        ctrl: event.ctrlKey,
        alt: event.altKey,
        shift: event.shiftKey,
        meta: event.metaKey,
        selector,
        masked,
        path: el ? ancestorTree(el) : null,
      });
      markUserAction("key", selector);
    },
    true
  );

  // === Auditoria estructural por foco (Herr. 3) ==============================
  // Dedupe por selector (la estructura no cambia) + tope por sesion: limita las
  // llamadas a getComputedStyle (que fuerzan reflow) a elementos unicos.
  const AUDIT_CSS = [
    "display", "position", "visibility", "color", "background-color",
    "font-size", "font-weight", "width", "height", "z-index",
  ];
  const auditedSelectors = new Set();
  const AUDIT_MAX = 300; // tope de elementos auditados por sesion
  function auditElement(el) {
    if (!el || el.nodeType !== 1) return;
    const selector = robustSelector(el);
    if (!selector || auditedSelectors.has(selector)) return; // dedupe
    if (auditedSelectors.size >= AUDIT_MAX) return; // tope por sesion
    auditedSelectors.add(selector);

    const masked = shouldMask(el);
    const attrs = {};
    for (const a of el.attributes || []) {
      attrs[a.name] = masked && /^(value|data-private)$/i.test(a.name) ? "***" : String(a.value).slice(0, 200);
    }
    let css = {};
    try {
      const cs = getComputedStyle(el); // una sola vez por selector
      for (const p of AUDIT_CSS) css[p] = cs.getPropertyValue(p);
    } catch {
      css = {};
    }
    let rect = null;
    try {
      const r = el.getBoundingClientRect();
      rect = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    } catch {
      /* sin layout */
    }
    capture("focus", {
      selector,
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      classes: Array.from(el.classList || []),
      role: el.getAttribute && el.getAttribute("role"),
      name: el.getAttribute && el.getAttribute("name"),
      attributes: attrs,
      css,
      rect,
      masked,
      path: ancestorTree(el),
    });
  }
  document.addEventListener("focusin", (e) => auditElement(closestElement(e.target)), true);
  // Cierra el run de teclado al salir del campo para no perder texto.
  document.addEventListener("focusout", flushKeyRun, true);

  // === Scroll + resize optimizados (Herramienta 6 — rendimiento) =============
  function throttle(fn, ms) {
    let last = 0;
    let timer = null;
    return (...args) => {
      const now = Date.now();
      const remaining = ms - (now - last);
      if (remaining <= 0) {
        last = now;
        fn(...args);
      } else if (!timer) {
        timer = setTimeout(() => {
          last = Date.now();
          timer = null;
          fn(...args);
        }, remaining);
      }
    };
  }
  function debounce(fn, ms) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }
  window.addEventListener(
    "scroll",
    throttle(() => capture("scroll", { x: window.scrollX, y: window.scrollY }), local.config.throttleScrollMs),
    { passive: true }
  );
  window.addEventListener(
    "resize",
    debounce(
      () => capture("resize", { width: window.innerWidth, height: window.innerHeight }),
      local.config.debounceResizeMs
    )
  );

  // === Recepcion de mensajes de injected.js → reenvio al SW ==================
  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const d = event.data;
    if (!d || d.__charly !== true || d.source !== FROM_INJECTED) return;

    if (d.type === "injected-ready") {
      local.injectedReady = true;
      if (local.pendingConfigForPage) {
        postToInjected("config", local.pendingConfigForPage);
        local.pendingConfigForPage = null;
      }
      return;
    }
    // Respuesta de un bloque de codigo bajo demanda (no es evento de timeline).
    if (d.type === "source-block") {
      const cb = pendingSource.get(d.data.requestId);
      if (cb) {
        pendingSource.delete(d.data.requestId);
        cb({ ok: true, block: d.data.block });
      }
      return;
    }
    // Cualquier otro tipo (error, console, network, route, global-state,
    // function-call, unhandledrejection, code-block) se reenvia tal cual al SW.
    if (local.recording) capture(d.type, d.data);
  });

  // === Configuracion y estado de grabacion ===================================
  function pushConfigToPage() {
    const payload = {
      recording: local.recording,
      watchedGlobals: local.config.watchedGlobals,
      patchedFunctions: local.config.patchedFunctions,
      sensitiveParams: local.config.sensitiveParams,
    };
    if (local.injectedReady) postToInjected("config", payload);
    else local.pendingConfigForPage = payload; // se envia tras el handshake
  }

  function startCapture() {
    // Herramienta 8: metadata de la sesion (resolucion, UA...).
    const meta = entryOf("meta", {
      url: location.href,
      userAgent: navigator.userAgent,
      language: navigator.language,
      resolution: { w: screen.width, h: screen.height },
      viewport: { w: window.innerWidth, h: window.innerHeight },
    });
    sendToSW(meta);
  }

  // Indicador visible en la propia pagina mientras se graba (transparencia para
  // quien usa el sitio): banner discreto en Shadow DOM, no interfiere con el CSS
  // ni el layout del host. Se muestra solo mientras dura la grabacion.
  let bannerHost = null;
  function showRecordingBanner() {
    if (bannerHost) return;
    try {
      bannerHost = document.createElement("div");
      bannerHost.style.cssText = "all:initial;position:fixed;z-index:2147483647;left:0;bottom:0;";
      const root = bannerHost.attachShadow({ mode: "closed" });
      root.innerHTML = `<style>
        .b{font:600 11px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;background:#111827;color:#fff;
           padding:6px 10px;border-top-right-radius:8px;display:flex;align-items:center;gap:6px;
           box-shadow:0 2px 10px rgba(0,0,0,.35);opacity:.92;}
        .d{width:7px;height:7px;border-radius:50%;background:#ff6b5e;animation:p 1.4s infinite}
        @keyframes p{0%,100%{opacity:1}50%{opacity:.35}}
      </style><div class="b"><span class="d"></span>CharlyAudit · grabando esta pestana</div>`;
      (document.body || document.documentElement).appendChild(bannerHost);
    } catch {
      /* no critico */
    }
  }
  function hideRecordingBanner() {
    if (bannerHost && bannerHost.isConnected) bannerHost.remove();
    bannerHost = null;
  }

  function applyState(recording, config) {
    const wasRecording = local.recording;
    local.recording = !!recording;
    if (config) Object.assign(local.config, config);
    pushConfigToPage();
    postToInjected("set-recording", { recording: local.recording });
    if (local.recording && !wasRecording) startCapture();
    if (local.recording) showRecordingBanner();
    else hideRecordingBanner();
  }

  // El SW empuja cambios de estado/config (al pulsar grabar en el popup).
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.channel === "qa-config") {
      applyState(msg.recording, msg.config);
    }
    return false;
  });

  // === Motor de Replay: importar y ejecutar (Herramienta 2) ==================
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const findEl = (sel) => {
    try {
      return sel ? document.querySelector(sel) : null;
    } catch {
      return null;
    }
  };
  /**
   * Genera variantes progresivamente mas laxas de un selector para tolerar
   * re-renders (indices nth-of-type que cambian) sin perder identidad. Orden:
   * exacto -> sin :nth-* -> ultimos 2 segmentos -> ultimo segmento.
   */
  function selectorVariants(sel) {
    if (!sel) return [];
    const out = [sel];
    const sinNth = sel.replace(/:nth-(?:of-type|child)\(\d+\)/g, "");
    if (sinNth !== sel) out.push(sinNth);
    const segs = sinNth.split(" > ");
    if (segs.length > 2) out.push(segs.slice(-2).join(" > "));
    if (segs.length > 1) out.push(segs[segs.length - 1]);
    return [...new Set(out)];
  }
  /** Resuelve un selector probando sus variantes; devuelve el primer match visible. */
  /** Busca un elemento por su ANCLA semantica (name/aria/rol/texto). */
  function findByAnchor(anchor) {
    if (!anchor) return null;
    let nodes;
    try {
      nodes = Array.from(document.querySelectorAll(anchor.tag || "*"));
    } catch {
      return null;
    }
    const norm = (s) => (s || "").trim().toLowerCase();
    const at = (n, a) => (n.getAttribute ? norm(n.getAttribute(a)) : "");
    // Prioridad: name exacto > aria exacto > placeholder > texto exacto > texto contiene.
    if (anchor.name) {
      const m = nodes.find((n) => at(n, "name") === norm(anchor.name));
      if (m) return m;
    }
    if (anchor.aria) {
      const m = nodes.find((n) => at(n, "aria-label") === norm(anchor.aria));
      if (m) return m;
    }
    if (anchor.ph) {
      const m = nodes.find((n) => at(n, "placeholder") === norm(anchor.ph));
      if (m) return m;
    }
    if (anchor.text) {
      const want = norm(anchor.text);
      const exact = nodes.find((n) => norm(n.textContent) === want);
      if (exact) return exact;
      const partial = nodes.find((n) => {
        const t = norm(n.textContent);
        return t.includes(want) && t.length < want.length + 40;
      });
      if (partial) return partial;
    }
    return null;
  }

  function findResilient(sel, visible, anchor) {
    for (const v of selectorVariants(sel)) {
      const el = findEl(v);
      if (el && (!visible || isVisible(el))) return el;
    }
    // Fallback semantico: reencuentra por significado (rol/aria/texto/name).
    const byAnchor = findByAnchor(anchor);
    if (byAnchor && (!visible || isVisible(byAnchor))) return byAnchor;
    // Ultimo recurso: cualquier match de las variantes aunque no sea visible.
    for (const v of selectorVariants(sel)) {
      const el = findEl(v);
      if (el) return el;
    }
    return byAnchor || null;
  }
  /** Comprueba que el elemento sea visible e interactuable (no display:none ni 0x0). */
  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }
  /**
   * Espera a que el selector exista (y opcionalmente sea visible), reintentando
   * por sondeo hasta `timeout`. Resuelve el DOM dinamico/SPA en el replay (QA-1).
   */
  async function waitForEl(sel, { timeout = 4000, visible = true, anchor = null } = {}) {
    if (!sel && !anchor) return null;
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      if (!local.replaying) return null; // se detuvo el replay
      const el = findResilient(sel, visible, anchor);
      if (el && (!visible || isVisible(el))) return el;
      await sleep(120); // reintento por sondeo
    }
    return findResilient(sel, false, anchor); // ultimo intento (variantes + ancla)
  }
  function fireMouse(el, type, button = 0) {
    const r = el.getBoundingClientRect();
    const opts = {
      bubbles: true,
      cancelable: true,
      view: window,
      button,
      clientX: Math.round(r.x + r.width / 2),
      clientY: Math.round(r.y + r.height / 2),
    };
    el.dispatchEvent(new MouseEvent(type, opts));
  }

  // === Escritura fiel en inputs (1.1) ========================================
  // Usa el setter nativo del prototipo para que frameworks (React/Vue) detecten
  // el cambio, y emite InputEvent("insertText") por caracter como un usuario real.
  function nativeValueSetter(el) {
    const proto =
      el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : el instanceof HTMLInputElement
          ? HTMLInputElement.prototype
          : el instanceof HTMLSelectElement
            ? HTMLSelectElement.prototype
            : null;
    const desc = proto && Object.getOwnPropertyDescriptor(proto, "value");
    return desc && desc.set ? desc.set : null;
  }
  function setNativeValue(el, value) {
    const setter = nativeValueSetter(el);
    if (setter) setter.call(el, value);
    else if ("value" in el) el.value = value;
  }
  /** Teclea `text` caracter por caracter actualizando el valor real del input. */
  async function typeInto(el, text, perCharDelay = 0) {
    el.focus();
    const editable = el.isContentEditable;
    let acc = editable ? el.textContent || "" : el.value || "";
    for (const ch of text) {
      el.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true }));
      acc += ch;
      if (editable) el.textContent = acc;
      else setNativeValue(el, acc);
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: ch }));
      el.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true }));
      if (perCharDelay) await sleep(perCharDelay);
    }
  }
  /** Fija el valor final del input y notifica input+change (frameworks incluidos). */
  function commitValue(el, value) {
    el.focus();
    if (el.isContentEditable) el.textContent = value;
    else setNativeValue(el, value);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: value }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // === Espera fiel a la navegacion/carga (1.2) ===============================
  /** Espera a que el documento cargue y a que cesen las mutaciones (quietud). */
  async function waitForStable(maxMs = 2500) {
    const t0 = Date.now();
    while (document.readyState !== "complete" && Date.now() - t0 < maxMs) await sleep(80);
    await new Promise((resolve) => {
      let quiet = setTimeout(done, 350);
      const obs = new MutationObserver(() => {
        clearTimeout(quiet);
        quiet = setTimeout(done, 350);
      });
      try {
        obs.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
      } catch {
        return resolve();
      }
      const hard = setTimeout(done, maxMs);
      function done() {
        clearTimeout(quiet);
        clearTimeout(hard);
        obs.disconnect();
        resolve();
      }
    });
  }

  // === Telemetria: observa las consecuencias de una interaccion (1.3) ========
  // Cuenta mutaciones del DOM y detecta cambio de URL tras la accion, para luego
  // contrastarlo con lo que ocurrio en la grabacion (busca inconsistencias).
  function observeConsequences(durationMs) {
    const urlBefore = location.href;
    let mutations = 0;
    const obs = new MutationObserver((list) => {
      mutations += list.length;
    });
    try {
      obs.observe(document.documentElement, { childList: true, subtree: true, attributes: true, characterData: true });
    } catch {
      /* sin DOM */
    }
    return new Promise((resolve) => {
      setTimeout(() => {
        obs.disconnect();
        resolve({ mutations, urlChanged: location.href !== urlBefore, urlAfter: location.href });
      }, durationMs);
    });
  }
  function replayBanner(text, onStop) {
    let el = document.getElementById("charly-replay-banner");
    if (!el) {
      el = document.createElement("div");
      el.id = "charly-replay-banner";
      el.style.cssText =
        "position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:2147483647;" +
        "background:#171a2e;color:#e7e9f5;border:1px solid #5b6cff;border-radius:10px;" +
        "padding:8px 12px;font:13px/1.3 system-ui,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.4);" +
        "display:flex;gap:10px;align-items:center";
      const span = document.createElement("span");
      span.id = "charly-replay-text";
      const btn = document.createElement("button");
      btn.textContent = "Detener";
      btn.style.cssText =
        "border:1px solid #ff6b5e;background:transparent;color:#ff6b5e;border-radius:6px;padding:3px 8px;cursor:pointer";
      btn.addEventListener("click", onStop);
      el.appendChild(span);
      el.appendChild(btn);
      document.documentElement.appendChild(el);
    }
    el.querySelector("#charly-replay-text").textContent = text;
    return el;
  }
  function removeBanner() {
    const el = document.getElementById("charly-replay-banner");
    if (el) el.remove();
  }

  function reportProgress(index, done) {
    try {
      chrome.runtime.sendMessage({ channel: "qa-control", action: "replayProgress", index, done }).catch(() => {});
    } catch {
      /* contexto invalidado */
    }
  }
  function reportTrace(entry) {
    try {
      chrome.runtime.sendMessage({ channel: "qa-control", action: "replayTrace", entry }).catch(() => {});
    } catch {
      /* contexto invalidado */
    }
  }

  async function runReplay(report, options = {}, startIndex = 0) {
    if (local.replaying) return;
    const speed = Math.max(0.25, Math.min(options.speed || 1, 8));
    const timeline = (report && report.timeline) || [];
    const steps = timeline.filter((e) =>
      ["click", "dblclick", "middleclick", "input", "key", "scroll", "dragdrop"].includes(e.type)
    );
    if (!steps.length || startIndex >= steps.length) return;

    // Efecto ESPERADO de cada interaccion segun la grabacion: lo que ocurrio
    // justo despues (navegacion, red, errores) hasta el siguiente paso (1.3).
    const expectedEffect = (stepEv, nextEv) => {
      const endTs = nextEv ? nextEv.ts : Infinity;
      const win = timeline.filter((ev) => ev.ts > stepEv.ts && ev.ts <= endTs);
      return {
        nav: win.some((ev) => ev.type === "route" || ev.type === "navigation"),
        net: win.filter((ev) => ev.type === "network").length,
        err: win.filter((ev) => ev.type === "error" || ev.type === "unhandledrejection").length,
      };
    };

    local.replaying = true;
    let misses = 0;
    let inconsist = 0;
    const stop = () => {
      local.replaying = false;
      try {
        chrome.runtime.sendMessage({ channel: "qa-control", action: "stopReplay" }).catch(() => {});
      } catch {
        /* noop */
      }
    };
    replayBanner(`Reproduciendo ${startIndex}/${steps.length}…`, stop);

    for (let i = startIndex; i < steps.length; i++) {
      if (!local.replaying) break;
      const e = steps[i];
      const d = e.data || {};
      const recordedDelay = e.delay || 0;

      // 1.2 — respeta el tiempo de espera del usuario y deja cargar la pagina.
      await sleep(Math.min(recordedDelay / speed, 8000));
      if (!local.replaying) break;
      await waitForStable(2500);
      if (!local.replaying) break;

      // Timeout de busqueda adaptativo: si el usuario espero mucho (carga lenta),
      // damos margen para que el elemento aparezca antes de marcar el paso.
      const elTimeout = Math.min(Math.max(4000, recordedDelay), 15000);
      const exp = expectedEffect(e, steps[i + 1]);
      let status = "ok";

      try {
        switch (e.type) {
          case "scroll":
            window.scrollTo(d.x || 0, d.y || 0);
            break;
          case "click":
          case "dblclick":
          case "middleclick": {
            const el = await waitForEl(d.selector, { timeout: elTimeout, anchor: d.anchor });
            if (!el) {
              status = "no-encontrado";
              misses++;
              break;
            }
            if (!isVisible(el)) status = "no-visible";
            el.scrollIntoView({ block: "center", behavior: "instant" });
            if (e.type === "dblclick") fireMouse(el, "dblclick");
            else if (e.type === "middleclick") fireMouse(el, "auxclick", 1);
            else {
              fireMouse(el, "mousedown");
              fireMouse(el, "mouseup");
              fireMouse(el, "click");
            }
            break;
          }
          case "input": {
            const el = await waitForEl(d.selector, { timeout: elTimeout, anchor: d.anchor });
            if (!el) {
              status = "no-encontrado";
              misses++;
              break;
            }
            if (d.masked) {
              status = "enmascarado";
              break;
            }
            commitValue(el, d.value); // 1.1 — fija el valor real del input
            break;
          }
          case "key": {
            if (d.masked) {
              status = "enmascarado";
              break;
            }
            const el =
              (await waitForEl(d.selector, { timeout: elTimeout, visible: false, anchor: d.anchor })) ||
              document.activeElement ||
              document.body;
            if (d.run && d.text) {
              // 1.1 — teclea de verdad sobre el input (valor + InputEvent por char).
              if ("value" in el || el.isContentEditable) await typeInto(el, d.text);
              else
                for (const ch of d.text) {
                  el.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true }));
                  el.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true }));
                }
              break;
            }
            const init = {
              key: d.key,
              code: d.code,
              ctrlKey: d.ctrl,
              altKey: d.alt,
              shiftKey: d.shift,
              metaKey: d.meta,
              bubbles: true,
            };
            el.dispatchEvent(new KeyboardEvent("keydown", init));
            el.dispatchEvent(new KeyboardEvent("keyup", init));
            break;
          }
          case "dragdrop": {
            const from = await waitForEl(d.from, { timeout: elTimeout });
            const to = await waitForEl(d.to, { timeout: elTimeout });
            if (from && to) {
              from.dispatchEvent(new DragEvent("dragstart", { bubbles: true }));
              to.dispatchEvent(new DragEvent("dragover", { bubbles: true }));
              to.dispatchEvent(new DragEvent("drop", { bubbles: true }));
              from.dispatchEvent(new DragEvent("dragend", { bubbles: true }));
            } else {
              status = "no-encontrado";
              misses++;
            }
            break;
          }
        }
      } catch {
        status = "error";
        misses++;
      }

      // Reporta el avance ANTES de observar/navegar, para reanudar sin repetir.
      reportProgress(i + 1, i + 1 >= steps.length);
      replayBanner(`Reproduciendo ${i + 1}/${steps.length}${inconsist ? ` · ${inconsist}\u26a0` : ""}…`, stop);

      // 1.3 — observa las consecuencias y contrasta con lo esperado (telemetria).
      if (e.type !== "scroll") {
        const obs = await observeConsequences(Math.min(Math.max(400, recordedDelay / 4), 1200));
        const incons = [];
        if (status === "no-encontrado") incons.push("elemento no encontrado");
        else if (status === "no-visible") incons.push("elemento no visible al actuar");
        else if (status === "error") incons.push("error al reproducir el paso");
        if (exp.nav && !obs.urlChanged) incons.push("no ocurrio la navegacion esperada");
        if (status === "ok" && !exp.nav && exp.net > 0 && obs.mutations === 0)
          incons.push("sin cambios en el DOM pese a actividad esperada");
        // Diff estructural: firma grabada (baseline) vs la observada ahora.
        let diff = null;
        if (d.baseline) {
          const now = domSignature();
          const dNodes = now.nodes - d.baseline.nodes;
          const tituloCambio = now.title !== d.baseline.title;
          // Umbral: variacion de nodos > 15% o cambio de titulo = divergencia.
          const relevante = Math.abs(dNodes) > Math.max(20, d.baseline.nodes * 0.15) || tituloCambio;
          if (relevante) {
            diff = { nodos: `${d.baseline.nodes}\u2192${now.nodes}`, tituloCambio };
            incons.push(`estructura divergente (${dNodes >= 0 ? "+" : ""}${dNodes} nodos${tituloCambio ? ", titulo cambio" : ""})`);
          }
        }
        if (incons.length) inconsist++;
        reportTrace({
          i,
          cid: e.cid || null, // id canonico del evento grabado: correlacion 1-a-1
          seq: e.seq,
          tipo: e.type,
          sel: d.selector || d.from || null,
          estado: status,
          mutaciones: obs.mutations,
          navego: obs.urlChanged,
          esperado: exp,
          diff,
          inconsistencias: incons,
        });
      }
    }

    if (local.replaying) {
      local.replaying = false;
      reportProgress(steps.length, true);
      const applied = steps.length - misses;
      // Estandar de calidad del replay: fidelidad = pasos sin inconsistencia.
      const fidelidad = Math.round(((steps.length - inconsist) / steps.length) * 100);
      const grado = inconsist === 0 && fidelidad >= 98 ? "premium" : fidelidad >= 90 ? "buena" : "revisar";
      replayBanner(
        `Replay finalizado · ${applied}/${steps.length} aplicados · fidelidad ${fidelidad}% (${grado})${inconsist ? ` · ${inconsist} inconsistencias — ver pestana Auditoria \u2192 Repeticion` : ""}`,
        removeBanner
      );
      setTimeout(removeBanner, 6000);
    }
  }

  // Consulta el job de replay al SW y reanuda/inicia si corresponde.
  async function maybeReplay() {
    try {
      const r = await chrome.runtime.sendMessage({ channel: "qa-control", action: "getReplayJob" });
      if (r && r.ok && r.resume && r.report) {
        runReplay(r.report, (r.job && r.job.options) || {}, (r.job && r.job.index) || 0);
      }
    } catch {
      /* SW dormido */
    }
  }

  // Canal de replay (lo dispara el SW contra la pestana).
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.channel !== "qa-replay") return false;
    if (msg.action === "play") {
      maybeReplay();
      sendResponse({ ok: true, started: true });
    } else if (msg.action === "stop") {
      local.replaying = false;
      removeBanner();
      sendResponse({ ok: true });
    }
    return false;
  });

  // Handshake: el SW confirma que el codigo de tracking esta inyectado y listo
  // ANTES de iniciar la grabacion (garantia de inyeccion, tarea 1.1).
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.channel !== "qa-control" || msg.action !== "ping") return false;
    sendResponse({ ready: true, injected: local.injectedReady === true, recording: local.recording === true });
    return false;
  });

  // Canal de bloque de codigo bajo demanda (Herramienta 4): SW -> content -> injected.
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.channel !== "qa-source") return false;
    const requestId = ++sourceReq;
    pendingSource.set(requestId, sendResponse);
    postToInjected("get-source", { url: msg.url, line: msg.line, ctx: msg.ctx, requestId });
    // Timeout de seguridad para no dejar la respuesta colgada.
    setTimeout(() => {
      if (pendingSource.has(requestId)) {
        pendingSource.delete(requestId);
        sendResponse({ ok: false, error: "timeout" });
      }
    }, 4000);
    return true; // respuesta asincrona
  });

  // === Persistencia del buffer (Herramienta 5) ===============================
  // beforeunload: respaldo sincrono en sessionStorage para no perder eventos si
  // el SW no alcanza a persistir antes de que la pagina se descargue.
  window.addEventListener("beforeunload", () => {
    if (!local.recording) return;
    capture("navigation", { reason: "beforeunload", url: location.href });
    try {
      sessionStorage.setItem(
        RESUME_KEY,
        JSON.stringify({ recording: true, buffer: local.pageBuffer.slice(-200) })
      );
    } catch {
      /* cuota agotada: el SW ya tiene la mayoria de eventos */
    }
  });

  // Al cargar: reanuda estado y reenvia el buffer respaldado (deduplicado en SW).
  async function bootstrap() {
    let resume = null;
    try {
      resume = JSON.parse(sessionStorage.getItem(RESUME_KEY) || "null");
      sessionStorage.removeItem(RESUME_KEY);
    } catch {
      /* ignore */
    }

    // Inyeccion 100% on-demand: si este content script se esta ejecutando es
    // porque el service worker decidio inyectarlo (grabacion o replay). La
    // decision de dominio permitido / auto-inicio la toma el SW, no el content.
    chrome.runtime
      .sendMessage({ channel: "qa-control", action: "getState" })
      .then((res) => {
        if (res && res.ok) applyState(res.isRecording, res.config);
        // Si seguimos grabando tras una navegacion, registra la carga y reenvia
        // el respaldo pendiente (persistencia entre paginas).
        if (res && res.isRecording) {
          capture("navigation", { reason: "load", url: location.href, ...navInfo() });
          if (resume && Array.isArray(resume.buffer)) for (const e of resume.buffer) sendToSW(e, true);
        }
      })
      .catch(() => {});

    // Reanuda un replay en curso si esta pestana lo tenia activo (tras navegar).
    maybeReplay();
  }
  bootstrap();
})();
