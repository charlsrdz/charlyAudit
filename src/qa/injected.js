/**
 * injected.js — Page Script (mundo MAIN)
 * ======================================
 * Corre en el contexto JS real de la pagina (salta el entorno aislado), por lo
 * que puede observar `window`, la consola, la red y las funciones de la app.
 * No tiene acceso a chrome.*; toda salida va por window.postMessage al content
 * script con la marca { __charly:true, source:"charly-injected" }.
 *
 * Cubre: Herramienta 2 (errores + consola), 3 (red), 4 (estado global),
 * 5 (ruteo SPA) y 7 (patching de funciones a eleccion).
 *
 * Se inyecta on-demand (via SW) al iniciar grabacion/replay. Usa PerformanceObserver
 * con buffered:true para recuperar metricas previas a la inyeccion. Solo EMITE
 * cuando `state.recording` es true (los parches quedan siempre instalados, lo
 * que es barato y evita reinstalarlos).
 */
(() => {
  "use strict";

  // Guard de re-entrada: evita reparchear console/fetch/XHR si por cualquier
  // razon el script se evalua dos veces en el mismo contexto de pagina.
  if (window.__charlyQAInjected) return;
  window.__charlyQAInjected = true;

  const SOURCE = "charly-injected";
  const FROM_CONTENT = "charly-content";

  const state = {
    recording: false,
    watchedGlobals: [], // p.ej. ["store", "user.profile"]
    sensitiveParams: ["token", "access_token", "auth", "key", "apikey", "password", "secret"],
    patched: new Map(), // path -> original fn (para poder revertir)
    lastUserAction: null, // {type, selector, ts} recibido del content script
  };

  const ACTION_WINDOW_MS = 1500; // ventana para atribuir un error a una accion

  /**
   * Contexto de correlacion: indica si un error/peticion ocurre tras una accion
   * del usuario (Herramienta 4) o de forma "automatica" (timers, navegacion...).
   */
  function actionContext() {
    const la = state.lastUserAction;
    if (la && Date.now() - la.ts <= ACTION_WINDOW_MS) {
      return { trigger: "user-action", lastAction: la, sinceMs: Date.now() - la.ts };
    }
    return { trigger: "automatic", lastAction: la || null, sinceMs: la ? Date.now() - la.ts : null };
  }

  // --- Utilidades -------------------------------------------------------------

  function emit(type, data) {
    if (!state.recording) return;
    window.postMessage({ __charly: true, source: SOURCE, type, data, ts: Date.now() }, "*");
  }

  function safeSerialize(value, depth = 2, seen = new WeakSet()) {
    if (value === null || typeof value === "undefined") return value ?? null;
    const t = typeof value;
    if (t === "string") return value.length > 2000 ? value.slice(0, 2000) + "\u2026" : value;
    if (t === "number" || t === "boolean") return value;
    if (t === "function") return `[Function ${value.name || "anonymous"}]`;
    if (t === "symbol" || t === "bigint") return value.toString();
    if (value instanceof Element) return `[Element <${value.tagName.toLowerCase()}>]`;
    if (value instanceof Node) return `[Node ${value.nodeName}]`;
    if (value instanceof Error) return { name: value.name, message: value.message, stack: value.stack };
    if (depth <= 0) return Array.isArray(value) ? "[Array]" : "[Object]";
    if (seen.has(value)) return "[Circular]";
    seen.add(value);
    try {
      if (Array.isArray(value)) return value.slice(0, 50).map((v) => safeSerialize(v, depth - 1, seen));
      const out = {};
      for (const key of Object.keys(value).slice(0, 50)) out[key] = safeSerialize(value[key], depth - 1, seen);
      return out;
    } catch (e) {
      return `[Unserializable: ${e.message}]`;
    }
  }

  /** Redacta valores de parametros sensibles en una URL (no captura tokens). */
  function redactUrl(rawUrl) {
    try {
      const u = new URL(rawUrl, location.href);
      for (const p of state.sensitiveParams) {
        if (u.searchParams.has(p)) u.searchParams.set(p, "***");
      }
      return u.toString();
    } catch {
      return String(rawUrl);
    }
  }

  function resolvePath(path) {
    const keys = String(path).split(".");
    const key = keys.pop();
    const parent = keys.reduce((acc, k) => (acc == null ? acc : acc[k]), window);
    return { parent, key };
  }

  // --- Herramienta 4: origen del error + recuperacion de bloques de codigo ----
  /** Parsea un stack (Chrome) en frames {fn,url,line,column}. */
  function parseStack(stack) {
    if (!stack) return [];
    const frames = [];
    for (const ln of String(stack).split("\n").slice(0, 12)) {
      let m = ln.match(/at\s+(.+?)\s+\((.+?):(\d+):(\d+)\)/);
      if (m) {
        frames.push({ fn: m[1], url: m[2], line: +m[3], column: +m[4] });
        continue;
      }
      m = ln.match(/at\s+(.+?):(\d+):(\d+)/) || ln.match(/(.+?):(\d+):(\d+)/);
      if (m) frames.push({ fn: "", url: m[1].replace(/^.*@/, ""), line: +m[2], column: +m[3] });
    }
    return frames;
  }
  const refOf = (f) => (f ? `${f.url}:${f.line}:${f.column}` : null);

  // Cache LRU generica (acota memoria: ~12 archivos).
  function lru(cap) {
    const m = new Map();
    return {
      has: (k) => m.has(k),
      get: (k) => {
        if (!m.has(k)) return undefined;
        const v = m.get(k);
        m.delete(k);
        m.set(k, v); // marca como reciente
        return v;
      },
      set: (k, v) => {
        if (m.has(k)) m.delete(k);
        m.set(k, v);
        if (m.size > cap) m.delete(m.keys().next().value); // evicciona el mas antiguo
        return v;
      },
    };
  }

  // Caches con tope (R-2): texto fuente y source maps parseados.
  const sourceTextCache = lru(12); // url -> Promise<string|null>
  const mapCache = lru(12); // url -> Promise<map|null>

  function getSourceText(url) {
    if (sourceTextCache.has(url)) return sourceTextCache.get(url);
    const p = (async () => {
      try {
        const res = await fetch(url);
        if (!res.ok) return null;
        return await res.text();
      } catch {
        return null; // cross-origin sin CORS, data:, etc.
      }
    })();
    sourceTextCache.set(url, p);
    return p;
  }
  async function getSourceLines(url) {
    const text = await getSourceText(url);
    return text == null ? null : text.split("\n");
  }

  // --- Source maps (R-1): decodifica VLQ y resuelve la posicion original ------
  const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const B64MAP = {};
  for (let i = 0; i < B64.length; i++) B64MAP[B64[i]] = i;
  function vlqDecode(str) {
    const out = [];
    let shift = 0;
    let value = 0;
    for (const ch of str) {
      const d = B64MAP[ch];
      if (d === undefined) continue;
      const cont = d & 32;
      value += (d & 31) << shift;
      if (cont) {
        shift += 5;
      } else {
        const neg = value & 1;
        value >>= 1;
        out.push(neg ? -value : value);
        value = 0;
        shift = 0;
      }
    }
    return out;
  }
  function parseMappings(mappings) {
    const lines = [];
    let srcIdx = 0;
    let srcLine = 0;
    let srcCol = 0; // persisten entre lineas; genCol se reinicia por linea
    for (const lineStr of String(mappings || "").split(";")) {
      const segs = [];
      let genCol = 0;
      for (const segStr of lineStr.split(",")) {
        if (!segStr) continue;
        const v = vlqDecode(segStr);
        genCol += v[0] || 0;
        if (v.length >= 4) {
          srcIdx += v[1];
          srcLine += v[2];
          srcCol += v[3];
          segs.push([genCol, srcIdx, srcLine, srcCol]);
        }
      }
      segs.sort((a, b) => a[0] - b[0]);
      lines.push(segs);
    }
    return lines;
  }
  function originalPositionFor(parsed, genLine1, genCol0) {
    const segs = parsed[genLine1 - 1] || [];
    let best = null;
    for (const s of segs) {
      if (s[0] <= genCol0) best = s;
      else break;
    }
    if (!best) best = segs[0] || null;
    return best ? { srcIdx: best[1], line: best[2] + 1, column: best[3] } : null;
  }
  function findSourceMapUrl(text) {
    const m = text.match(/\/\/[#@]\s*sourceMappingURL=([^\s'"]+)\s*$/m);
    return m ? m[1] : null;
  }
  function getSourceMap(srcUrl) {
    if (mapCache.has(srcUrl)) return mapCache.get(srcUrl);
    const p = (async () => {
      const text = await getSourceText(srcUrl);
      if (!text) return null;
      const smUrl = findSourceMapUrl(text);
      if (!smUrl) return null;
      try {
        let json;
        if (smUrl.startsWith("data:")) {
          const comma = smUrl.indexOf(",");
          const payload = smUrl.slice(comma + 1);
          json = JSON.parse(smUrl.slice(0, comma).includes(";base64") ? atob(payload) : decodeURIComponent(payload));
        } else {
          const abs = new URL(smUrl, srcUrl).href;
          const res = await fetch(abs);
          if (!res.ok) return null;
          json = await res.json();
        }
        return {
          parsed: parseMappings(json.mappings),
          sources: json.sources || [],
          sourcesContent: json.sourcesContent || null,
          sourceRoot: json.sourceRoot || "",
          base: srcUrl,
        };
      } catch {
        return null;
      }
    })();
    mapCache.set(srcUrl, p);
    return p;
  }

  function buildSnippet(lines, line, ctx) {
    const start = Math.max(1, line - ctx);
    const end = Math.min(lines.length, line + ctx);
    const snippet = [];
    for (let n = start; n <= end; n++) snippet.push({ n, code: (lines[n - 1] || "").slice(0, 400), hit: n === line });
    return snippet;
  }

  /**
   * Bloque de codigo alrededor de (line,column). Si el archivo tiene source map,
   * resuelve y devuelve el CODIGO ORIGINAL (no el bundle minificado) (R-1).
   */
  async function fetchSourceBlock(url, line, column = 0, ctx = 6) {
    if (!url || !line || /^(chrome-extension|extensions):/.test(url)) return null;
    // 1) Intenta resolver via source map -> codigo original legible.
    try {
      const map = await getSourceMap(url);
      if (map && map.parsed) {
        const pos = originalPositionFor(map.parsed, line, column || 0);
        if (pos) {
          let origLines = null;
          const name = map.sources[pos.srcIdx] || "origen";
          if (map.sourcesContent && map.sourcesContent[pos.srcIdx] != null) {
            origLines = String(map.sourcesContent[pos.srcIdx]).split("\n");
          } else {
            try {
              origLines = await getSourceLines(new URL((map.sourceRoot || "") + name, map.base).href);
            } catch {
              origLines = null;
            }
          }
          if (origLines) return { url: name, line: pos.line, snippet: buildSnippet(origLines, pos.line, ctx), mapped: true };
        }
      }
    } catch {
      /* cae al snippet del archivo servido */
    }
    // 2) Sin source map: snippet del archivo tal cual (puede venir minificado).
    const lines = await getSourceLines(url);
    if (!lines) return null;
    return { url, line, snippet: buildSnippet(lines, line, ctx), mapped: false };
  }

  // Emite el bloque de codigo del frame superior (deduplicado por referencia).
  const emittedRefs = new Set();
  function maybeEmitCodeBlock(frame) {
    const ref = refOf(frame);
    if (!ref || emittedRefs.has(ref)) return;
    emittedRefs.add(ref);
    fetchSourceBlock(frame.url, frame.line, frame.column).then((block) => {
      if (block) emit("code-block", { ref, fn: frame.fn || null, mapped: block.mapped, ...block });
    });
  }

  // --- Herramienta 2a/4: errores globales (correlacionados con la accion) ----
  const prevOnError = window.onerror;
  window.onerror = function (message, source, line, column, error) {
    const frames = error?.stack ? parseStack(error.stack) : source ? [{ fn: "", url: source, line, column }] : [];
    const top = frames[0];
    emit("error", {
      message: String(message),
      source,
      line,
      column,
      stack: error?.stack || null,
      frames,
      ref: refOf(top),
      ...actionContext(),
    });
    if (top) maybeEmitCodeBlock(top); // recupera el bloque de codigo del origen
    return typeof prevOnError === "function" ? prevOnError.apply(this, arguments) : false;
  };
  window.addEventListener(
    "error",
    (event) => {
      const tgt = event.target;
      if (tgt && tgt !== window && (tgt.src || tgt.href)) {
        emit("error", {
          kind: "resource",
          tag: tgt.tagName?.toLowerCase?.(),
          url: tgt.src || tgt.href,
          ...actionContext(),
        });
      }
    },
    true
  );
  window.addEventListener("unhandledrejection", (event) => {
    const r = event.reason;
    const frames = r?.stack ? parseStack(r.stack) : [];
    const top = frames[0];
    emit("unhandledrejection", {
      reason: r?.message || String(r),
      stack: r?.stack || null,
      frames,
      ref: refOf(top),
      ...actionContext(),
    });
    if (top) maybeEmitCodeBlock(top);
  });

  // --- Herramienta 2b: monkey patching de la consola -------------------------
  ["log", "warn", "error"].forEach((level) => {
    const original = console[level].bind(console);
    console[level] = (...args) => {
      const data = { level, args: args.map((a) => safeSerialize(a, 2)) };
      // El origen del log (Herramienta 4): de donde se emitio.
      if (level === "error" || level === "warn") {
        const frames = parseStack(new Error().stack).slice(1); // quita este frame
        data.frames = frames;
        data.ref = refOf(frames[0]);
        if (frames[0]) maybeEmitCodeBlock(frames[0]); // recupera el bloque de codigo (dedup por ref)
      }
      emit("console", data);
      return original(...args); // preserva el comportamiento original
    };
  });

  // --- Herramienta 3: interceptor de red (fetch + XHR) -----------------------
  // Nunca se capturan cabeceras (Authorization, Cookie...) ni cuerpos: solo
  // metodo, URL redactada, status y tiempo de respuesta.
  /**
   * Waterfall completo de una peticion: busca la entrada de Resource Timing que
   * corresponde (misma URL, startTime mas cercano) para anexar tamano/fases/
   * protocolo. Puede no encontrar nada (ej. peticion fallida sin entrada) — se
   * degrada con normalidad, sin bloquear la emision del evento base.
   */
  function waterfallFor(url, startPerfMs) {
    try {
      const entries = performance.getEntriesByName(url, "resource");
      let best = null, bestDiff = Infinity;
      for (const e of entries) {
        const diff = Math.abs(e.startTime - startPerfMs);
        if (diff < bestDiff && diff < 3000) {
          best = e;
          bestDiff = diff;
        }
      }
      if (!best) return null;
      return {
        transferSize: best.transferSize || 0,
        kb: Math.round((best.transferSize || 0) / 1024),
        protocolo: best.nextHopProtocol || null,
        cache: best.transferSize === 0 && best.decodedBodySize > 0,
        fases: {
          dnsMs: Math.round(Math.max(0, best.domainLookupEnd - best.domainLookupStart)),
          conexionMs: Math.round(Math.max(0, best.connectEnd - best.connectStart)),
          ttfbMs: Math.round(Math.max(0, (best.responseStart || 0) - (best.requestStart || best.startTime))),
          descargaMs: Math.round(Math.max(0, (best.responseEnd || 0) - (best.responseStart || 0))),
        },
      };
    } catch {
      return null;
    }
  }
  /** Emite el evento "network" completo (waterfall) tras un breve respiro para
   *  que el navegador termine de publicar la entrada de Resource Timing. */
  function emitNetworkFull(base, startPerfMs) {
    setTimeout(() => {
      const wf = waterfallFor(base.url, startPerfMs) || {};
      emit("network", { ...base, ...wf, inicioMs: Math.round(startPerfMs), finMs: Math.round(startPerfMs + (base.durationMs || 0)) });
    }, 60);
  }
  const originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = function (input, init) {
      const start = performance.now();
      const requestId = "req-" + netSeq++;
      const method = (init && init.method) || (input && input.method) || "GET";
      const url = typeof input === "string" ? input : input && input.url ? input.url : String(input);
      return originalFetch.apply(this, arguments).then(
        (response) => {
          emitNetworkFull(
            {
              requestId,
              type: "fetch",
              method: method.toUpperCase(),
              url: redactUrl(url),
              status: response.status,
              ok: response.ok,
              durationMs: Math.round(performance.now() - start),
            },
            start
          );
          securityScanUrl(url, "red");
          return response;
        },
        (err) => {
          emitNetworkFull(
            {
              requestId,
              type: "fetch",
              method: method.toUpperCase(),
              url: redactUrl(url),
              status: 0,
              ok: false,
              error: err?.message || String(err),
              durationMs: Math.round(performance.now() - start),
              ...actionContext(),
            },
            start
          );
          throw err;
        }
      );
    };
  }

  const XHR = window.XMLHttpRequest;
  if (XHR && XHR.prototype) {
    const open = XHR.prototype.open;
    const send = XHR.prototype.send;
    XHR.prototype.open = function (method, url) {
      this.__charly = { method: String(method || "GET").toUpperCase(), url: String(url) };
      return open.apply(this, arguments);
    };
    XHR.prototype.send = function () {
      const meta = this.__charly;
      if (meta) {
        const start = performance.now();
        const requestId = "req-" + netSeq++;
        this.addEventListener("loadend", () => {
          const ok = this.status >= 200 && this.status < 400;
          emitNetworkFull(
            {
              requestId,
              type: "xhr",
              method: meta.method,
              url: redactUrl(meta.url),
              status: this.status,
              ok,
              durationMs: Math.round(performance.now() - start),
              ...(ok ? {} : actionContext()),
            },
            start
          );
          securityScanUrl(meta.url, "red");
        });
      }
      return send.apply(this, arguments);
    };
  }

  // --- Herramienta 5: ruteo en SPA -------------------------------------------
  function emitRoute(method, from, to) {
    // Cambio de ruta sin recarga = navegacion SPA (pushState/replaceState/popstate).
    // Se adjunta el TBT/long tasks acumulados en el segmento que TERMINA aqui —
    // TBT real por navegacion, no solo el acumulado total de la sesion.
    const seg = perf.started ? flushTbtSegment() : { tbtSegmentMs: 0, longTasksSegment: 0 };
    emit("route", { method, from, to, tipo: "spa", referrer: from || document.referrer || null, ...seg });
    securityScanPage();
    snapshotGlobals("route"); // estado en cada cambio de pagina (Herramienta 4)
  }
  ["pushState", "replaceState"].forEach((method) => {
    const original = history[method];
    history[method] = function (st, title, url) {
      const from = location.href;
      const result = original.apply(this, arguments);
      emitRoute(method, from, location.href);
      return result;
    };
  });
  window.addEventListener("popstate", () => emitRoute("popstate", document.referrer, location.href));
  window.addEventListener("hashchange", (e) => emitRoute("hashchange", e.oldURL, e.newURL));

  // --- Herramienta 4: extractor de estado y variables globales ---------------
  function readGlobals(paths) {
    const values = {};
    for (const path of paths) {
      try {
        const v = String(path)
          .split(".")
          .reduce((acc, k) => (acc == null ? acc : acc[k]), window);
        values[path] = safeSerialize(v, 3);
      } catch (e) {
        values[path] = { __error: e.message };
      }
    }
    return values;
  }
  let lastGlobals = {};
  function snapshotGlobals(trigger) {
    if (!state.recording || !state.watchedGlobals.length) return;
    const values = readGlobals(state.watchedGlobals);
    // Detecta que variables cambiaron respecto al snapshot anterior (mutaciones).
    const changed = [];
    for (const k of Object.keys(values)) {
      if (JSON.stringify(values[k]) !== JSON.stringify(lastGlobals[k])) changed.push(k);
    }
    lastGlobals = values;
    emit("global-state", { trigger, values, changed });
  }

  // --- Herramienta 7: patching dinamico de funciones -------------------------
  function patchFunction(path) {
    if (state.patched.has(path)) return true;
    const { parent, key } = resolvePath(path);
    if (!parent || typeof parent[key] !== "function") return false;

    const original = parent[key];
    state.patched.set(path, original);

    parent[key] = function (...args) {
      const start = performance.now();
      const baseRecord = { path, args: args.map((a) => safeSerialize(a, 2)) };
      try {
        const result = original.apply(this, args);
        // Soporte para funciones asincronas (Promesas).
        if (result && typeof result.then === "function") {
          return result.then(
            (value) => {
              emit("function-call", {
                ...baseRecord,
                async: true,
                durationMs: Math.round(performance.now() - start),
                returned: safeSerialize(value, 2),
              });
              return value;
            },
            (err) => {
              emit("function-call", {
                ...baseRecord,
                async: true,
                durationMs: Math.round(performance.now() - start),
                error: err?.message || String(err),
              });
              throw err;
            }
          );
        }
        emit("function-call", {
          ...baseRecord,
          async: false,
          durationMs: Math.round(performance.now() - start),
          returned: safeSerialize(result, 2),
        });
        return result;
      } catch (err) {
        emit("function-call", {
          ...baseRecord,
          async: false,
          durationMs: Math.round(performance.now() - start),
          error: err?.message || String(err),
        });
        throw err;
      }
    };
    // Conserva el nombre visible para no romper introspeccion.
    try {
      Object.defineProperty(parent[key], "name", { value: original.name });
    } catch {
      /* no critico */
    }
    return true;
  }

  /** Intenta parchear; si la funcion aun no existe, reintenta unos segundos. */
  function patchWithRetry(path, attemptsLeft = 12) {
    if (patchFunction(path)) return;
    if (attemptsLeft > 0) setTimeout(() => patchWithRetry(path, attemptsLeft - 1), 500);
  }

  // --- Configuracion desde el content script ---------------------------------
  function applyConfig(config = {}) {
    if (typeof config.recording === "boolean") {
      state.recording = config.recording;
      if (config.recording) startPerf();
    }
    if (Array.isArray(config.watchedGlobals)) state.watchedGlobals = config.watchedGlobals;
    if (Array.isArray(config.sensitiveParams)) state.sensitiveParams = config.sensitiveParams;
    if (Array.isArray(config.patchedFunctions)) config.patchedFunctions.forEach((p) => patchWithRetry(p));
  }

  // === Performance: Web Vitals + Long Tasks + recursos (tarea 2) ==============
  // Presupuesto de rendimiento por sesion. Se emite un snapshot "web-vitals"
  // periodico y uno final al ocultar la pestana. No captura contenido, solo metricas.
  // perf.tbt / perf.longTasks = acumulado de SESION (para el web-vitals global).
  // perf.tbtSegment / perf.longTasksSegment = acumulado desde la ULTIMA navegacion
  // (se adjunta al evento de ruta/navegacion saliente y se reinicia) — TBT real
  // por navegacion, no solo el total de la sesion.
  const perf = { started: false, lcp: 0, cls: 0, inp: 0, tbt: 0, longTasks: 0, tbtSegment: 0, longTasksSegment: 0, observers: [] };
  let resSeq = 0; // requestId de recursos pasivos (img/script/css/...)
  let netSeq = 0; // requestId de peticiones activas (fetch/xhr)
  function obs(type, cb, extra) {
    try {
      const o = new PerformanceObserver((list) => cb(list.getEntries()));
      o.observe({ type, buffered: true, ...(extra || {}) });
      perf.observers.push(o);
    } catch {
      /* tipo no soportado en este navegador */
    }
  }
  function emitVitals(reason) {
    if (!state.recording) return;
    emit("web-vitals", {
      reason,
      lcpMs: Math.round(perf.lcp),
      cls: Number(perf.cls.toFixed(3)),
      inpMs: Math.round(perf.inp),
      tbtMs: Math.round(perf.tbt),
      longTasks: perf.longTasks,
      tbtSegmentMs: Math.round(perf.tbtSegment), // TBT de la navegacion/ruta actual
      longTasksSegment: perf.longTasksSegment,
    });
  }
  /** Cierra el segmento de TBT actual (llamar justo antes de una navegacion/ruta
   *  nueva) y devuelve lo acumulado desde la navegacion anterior. Reinicia. */
  function flushTbtSegment() {
    const out = { tbtSegmentMs: Math.round(perf.tbtSegment), longTasksSegment: perf.longTasksSegment };
    perf.tbtSegment = 0;
    perf.longTasksSegment = 0;
    return out;
  }
  // Deteccion de Workers / Service Workers. Como la grabacion puede empezar con
  // la pagina ya cargada, primero enumera los EXISTENTES y luego intercepta los
  // nuevos, para no omitir el (service)worker que usa la pestana.
  let workersHooked = false;
  function detectWorkers() {
    // 1) Service Workers ya registrados al iniciar la grabacion.
    try {
      if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
        navigator.serviceWorker
          .getRegistrations()
          .then((regs) => {
            for (const r of regs) {
              const w = r.active || r.waiting || r.installing;
              emit("worker", { clase: "serviceworker", scope: r.scope, script: w && w.scriptURL, estado: w && w.state, existente: true });
            }
          })
          .catch(() => {});
        if (navigator.serviceWorker.controller)
          emit("worker", { clase: "serviceworker", script: navigator.serviceWorker.controller.scriptURL, estado: "controlando", existente: true });
      }
    } catch {
      /* sin SW API */
    }
    if (workersHooked) return;
    workersHooked = true;
    // 2) Intercepta nuevos Web/Shared Workers.
    try {
      for (const K of ["Worker", "SharedWorker"]) {
        const Orig = window[K];
        if (!Orig || Orig.__charly) continue;
        const Wrapped = function (url, opts) {
          try {
            emit("worker", { clase: K.toLowerCase(), script: String(url) });
          } catch {
            /* noop */
          }
          return new Orig(url, opts);
        };
        Wrapped.prototype = Orig.prototype;
        Wrapped.__charly = true;
        window[K] = Wrapped;
      }
    } catch {
      /* noop */
    }
    // 3) Intercepta nuevos registros de Service Worker.
    try {
      const sw = navigator.serviceWorker;
      if (sw && sw.register && !sw.register.__charly) {
        const orig = sw.register.bind(sw);
        const patched = function (url, opts) {
          try {
            emit("worker", { clase: "serviceworker", script: String(url), nuevo: true });
          } catch {
            /* noop */
          }
          return orig(url, opts);
        };
        patched.__charly = true;
        sw.register = patched;
      }
    } catch {
      /* noop */
    }
  }

  function startPerf() {
    if (perf.started) return;
    perf.started = true;
    detectWorkers();
    obs("largest-contentful-paint", (es) => {
      const last = es[es.length - 1];
      if (last) perf.lcp = last.renderTime || last.loadTime || last.startTime;
    });
    obs("layout-shift", (es) => {
      for (const e of es) if (!e.hadRecentInput) perf.cls += e.value;
    });
    obs("event", (es) => {
      for (const e of es) {
        perf.inp = Math.max(perf.inp, e.duration); // agregado de sesion (Web Vitals)
        // INP REAL POR INTERACCION: cada entrada resuelta se emite con su propio
        // reloj epoch (performance.timeOrigin + startTime), que es EL MISMO reloj
        // que usa content.js (Date.now()) para el evento nativo que disparo esta
        // interaccion (Event.timeStamp esta en la misma base). Esto permite
        // correlacionar, en report-engine.js, esta latencia con el click/input/
        // tecla EXACTOS que la originaron — no una aproximacion ni un maximo
        // global, sino la medicion real de ESA interaccion especifica.
        // Dedup: si el navegador reporta multiples entradas con el mismo
        // interactionId (pointerdown + pointerup + click), solo emitimos una
        // (la de mayor duracion) para no duplicar la correlacion.
        const iid = e.interactionId;
        if (iid != null) {
          const prev = perf._inpSeen && perf._inpSeen.get(iid);
          if (prev && prev >= e.duration) continue;
          if (!perf._inpSeen) perf._inpSeen = new Map();
          perf._inpSeen.set(iid, e.duration);
          if (perf._inpSeen.size > 200) perf._inpSeen.delete(perf._inpSeen.keys().next().value);
        }
        emit("interaction-timing", {
          tipo: e.name, // 'click' | 'pointerdown' | 'keydown' | ...
          inpMs: Math.round(e.duration),
          tsEvent: Math.round(performance.timeOrigin + e.startTime),
          interactionId: iid != null ? iid : null,
        });
      }
    }, { durationThreshold: 40 });
    obs("longtask", (es) => {
      for (const e of es) {
        perf.longTasks++;
        perf.longTasksSegment++;
        const blocking = Math.max(0, e.duration - 50); // Total Blocking Time
        perf.tbt += blocking;
        perf.tbtSegment += blocking;
      }
    });
    // Waterfall de red COMPLETO por requestId: todo recurso pasivo del navegador
    // (no fetch/xhr, que ya se cubren abajo con su propio waterfall), sin filtrar
    // por tamano/duracion — inicio, fin, tamano y fases para cada uno.
    // Throttle de recursos de baja señal (imagenes/fonts/css ya cargados): se
    // emiten siempre pero se compactan para no inflar el timeline con cientos de
    // entradas de recursos de terceros que no aportan datos de auditoria.
    const _resSeen = new Set();
    obs("resource", (es) => {
      if (!state.recording) return;
      for (const e of es) {
        securityScanUrl(e.name, "recurso"); // mixed content en subrecursos
        if (e.initiatorType === "fetch" || e.initiatorType === "xmlhttprequest") continue; // ya cubiertos
        // Dedup por URL+inicio: buffered:true puede reportar el mismo recurso
        // varias veces si startPerf se llama despues de la carga inicial de la pagina.
        // Limite de tamaño ademas del temporal (30s, ver el intervalo mas abajo):
        // una rafaga extrema (p.ej. un mapa con cientos de tiles en pocos
        // segundos) no debe esperar al ciclo periodico para acotarse.
        if (_resSeen.size > 2000) _resSeen.clear();
        const key = `${e.name}|${Math.round(e.startTime)}`;
        if (_resSeen.has(key)) continue;
        _resSeen.add(key);
        emit("resource-timing", {
          requestId: "res-" + resSeq++,
          url: redactUrl(e.name),
          tipo: e.initiatorType,
          inicioMs: Math.round(e.startTime),
          finMs: Math.round(e.responseEnd || e.startTime + e.duration),
          ms: Math.round(e.duration),
          kb: Math.round((e.transferSize || 0) / 1024),
          transferSize: e.transferSize || 0,
          protocolo: e.nextHopProtocol || null,
          cache: e.transferSize === 0 && e.decodedBodySize > 0,
          fases: {
            dnsMs: Math.round(Math.max(0, e.domainLookupEnd - e.domainLookupStart)),
            conexionMs: Math.round(Math.max(0, e.connectEnd - e.connectStart)),
            ttfbMs: Math.round(Math.max(0, (e.responseStart || 0) - (e.requestStart || e.startTime))),
            descargaMs: Math.round(Math.max(0, (e.responseEnd || 0) - (e.responseStart || 0))),
          },
        });
      }
    });
    // Snapshot periodico y final.
    perf._timer = setInterval(() => emitVitals("interval"), 5000);
    addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") emitVitals("hidden");
    });

    // Gestion del buffer nativo de Resource Timing (v2.5.9 — fix de rendimiento,
    // prioridad alta). Sin esto, el navegador acumula una entrada por cada
    // recurso cargado durante TODA la sesion de grabacion sin limite propio —
    // memoria de proceso, no solo nuestro heap de JS. Se amplia el buffer para
    // no perder entradas entre limpiezas, y se limpia cada 30s (margen amplio
    // sobre los 60ms que usa emitNetworkFull()/waterfallFor() para correlacionar
    // un fetch/XHR con su entrada de Resource Timing, asi que nunca se limpia
    // algo que todavia se necesita leer). _resSeen se vacia en el mismo
    // momento: las entradas que ya limpiamos del navegador no van a reaparecer,
    // asi que es seguro olvidarlas — mantiene ambas estructuras acotadas juntas.
    try {
      performance.setResourceTimingBufferSize(1000);
    } catch {
      /* API no disponible en este navegador */
    }
    perf._clearTimer = setInterval(() => {
      if (!state.recording) return;
      try {
        performance.clearResourceTimings();
      } catch {
        /* no critico */
      }
      _resSeen.clear();
    }, 30000);
  }

  // === Security: scanner pasivo ofensivo-controlado (tarea 3) =================
  // Reusa la captura de red/origen para detectar fugas y malas practicas.
  // Siempre redacta la evidencia; nunca emite el valor sensible en claro.
  const secSeen = new Set(); // dedup por (kind|clave)
  const SEC_PATTERNS = [
    { kind: "jwt", sev: "alta", re: /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}/ },
    { kind: "email", sev: "media", re: /[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/i },
    { kind: "api-key", sev: "alta", re: /\b(?:api[_-]?key|apikey|secret|token|access[_-]?token)\b/i },
    { kind: "bearer", sev: "alta", re: /bearer\s+[a-z0-9._-]{12,}/i },
    { kind: "tarjeta", sev: "critica", re: /\b(?:\d[ -]?){13,16}\b/ },
  ];
  function secFinding(kind, sev, detalle, donde) {
    const key = kind + "|" + donde + "|" + detalle;
    if (secSeen.has(key)) return;
    secSeen.add(key);
    emit("security", { kind, severidad: sev, detalle, donde });
  }
  function securityScanUrl(rawUrl, donde) {
    if (!state.recording || !rawUrl) return;
    try {
      const u = new URL(rawUrl, location.href);
      // Mixed content: subrecurso http en pagina https.
      if (location.protocol === "https:" && u.protocol === "http:")
        secFinding("mixed-content", "alta", u.origin + u.pathname, donde);
      // Fugas en la query (se reporta la CLAVE, no el valor).
      for (const [k, v] of u.searchParams) {
        for (const p of SEC_PATTERNS) {
          if (p.re.test(k) || p.re.test(v)) {
            secFinding(p.kind, p.sev, `parametro "${k}" en ${u.pathname}`, donde);
            break;
          }
        }
      }
    } catch {
      /* url invalida */
    }
  }
  function securityScanPage() {
    if (!state.recording) return;
    // CSP declarada por meta (la de cabecera se valida en servidor).
    if (!document.querySelector('meta[http-equiv="Content-Security-Policy" i]'))
      secFinding("csp-meta-ausente", "media", location.hostname, "documento");
    // Cookies legibles por JS (sin HttpOnly) con nombre sensible.
    for (const c of String(document.cookie || "").split(";")) {
      const name = c.split("=")[0].trim();
      if (/sess|token|auth|sid|jwt/i.test(name)) secFinding("cookie-sin-httponly", "alta", name, "cookies");
    }
    if (location.protocol === "http:") secFinding("sin-https", "alta", location.hostname, "documento");
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const d = event.data;
    if (!d || d.__charly !== true || d.source !== FROM_CONTENT) return;

    switch (d.type) {
      case "config":
        applyConfig(d.data);
        break;
      case "set-recording":
        state.recording = !!d.data.recording;
        if (state.recording) {
          startPerf();
          securityScanPage();
        }
        break;
      case "last-action":
        state.lastUserAction = d.data || null;
        break;
      case "get-source":
        // Respuesta directa (no es un evento de timeline, no se gatea por recording).
        fetchSourceBlock(d.data.url, d.data.line, d.data.column || 0, d.data.ctx || 6).then((block) => {
          window.postMessage(
            { __charly: true, source: SOURCE, type: "source-block", data: { requestId: d.data.requestId, block } },
            "*"
          );
        });
        break;
      case "snapshot-globals":
        snapshotGlobals(d.data?.trigger || "manual");
        break;
      case "patch-function":
        patchWithRetry(d.data.path);
        break;
      default:
        break;
    }
  });

  // --- API publica para depuracion manual ------------------------------------
  window.__charlyQA = Object.freeze({
    version: "2.0.0",
    readGlobals,
    patch: patchWithRetry,
    snapshot: () => snapshotGlobals("manual"),
    emit: (name, detail) => window.dispatchEvent(new CustomEvent(name, { detail })),
  });

  // Handshake: avisa al content script de que ya esta listo.
  window.postMessage(
    { __charly: true, source: SOURCE, type: "injected-ready", data: { url: location.href }, ts: Date.now() },
    "*"
  );
})();
