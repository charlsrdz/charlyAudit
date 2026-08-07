/**
 * playwright-import.js — Interprete de un subconjunto de scripts Playwright.
 * ===========================================================================
 * IMPORTANTE (limite tecnico honesto): un script Playwright real usa la API
 * de Node.js (`require('playwright')`, `chromium.launch()`, `page.goto()`,
 * etc.) — nada de eso existe ni puede ejecutarse dentro de una extension de
 * Chrome (no hay Node, no hay proceso de automatizacion externo). Por lo
 * tanto este modulo NO ejecuta el script como Playwright lo haria.
 *
 * Lo que SI hace: reconoce, via un parser ligero basado en patrones (no un
 * interprete completo de JS), la secuencia de llamadas mas comunes de un
 * script `.spec.js` generado por esta misma herramienta o escrito a mano de
 * forma simple —
 *   page.goto(url)
 *   page.click(selector) / page.dblclick(selector)
 *   page.fill(selector, value)
 *   page.type(selector, value)
 *   page.press(selector, key)
 *   page.waitForTimeout(ms)
 *   page.waitForSelector(selector)
 * — y las traduce a la MISMA forma de "timeline" que ya usa el motor de
 * replay existente (runReplay en content.js), reutilizando por completo su
 * logica de espera, resolucion de selectores y escritura fiel. Es decir:
 * "reproducir un Playwright importado" = "interpretar sus pasos reconocidos
 * y reproducirlos con nuestro propio motor", no ejecutar Playwright real.
 *
 * Modulo puro (sin dependencias de chrome.* ni del DOM): parsea texto,
 * devuelve datos. Se usa desde el service worker.
 */

/** Deshace el escapado basico de comillas dentro de un literal de string. */
function unescapeStr(s) {
  return s.replace(/\\(['"`\\])/g, "$1");
}

/**
 * Extrae el primer argumento de tipo string de una llamada `fn(...)` dado el
 * texto que empieza justo despues del nombre de la funcion. Soporta comillas
 * simples, dobles o template literals simples (sin interpolacion).
 */
function firstStringArg(argsText) {
  const m = argsText.match(/^\s*['"`]((?:\\.|[^'"`\\])*)['"`]/);
  return m ? unescapeStr(m[1]) : null;
}
/** Extrae el segundo argumento de tipo string (para fill/type/press). */
function secondStringArg(argsText) {
  const afterFirst = argsText.replace(/^\s*['"`](?:\\.|[^'"`\\])*['"`]\s*,/, "");
  return firstStringArg(afterFirst);
}
/** Extrae el primer argumento numerico (para waitForTimeout). */
function firstNumberArg(argsText) {
  const m = argsText.match(/^\s*(\d+)/);
  return m ? Number(m[1]) : null;
}

/**
 * Parsea un script y devuelve los pasos reconocidos + advertencias sobre lo
 * que no se pudo interpretar (transparencia: nunca se pretende soportar el
 * 100% de un script arbitrario).
 * @param {string} text
 * @returns {{url:string|null, steps:Array<object>, warnings:string[], totalCalls:number}}
 */
export function parsePlaywrightScript(text) {
  const warnings = [];
  const steps = [];
  let url = null;
  let pendingDelay = 0;
  let totalCalls = 0;

  const lines = String(text || "").split(/\r?\n/);
  const CALL_RE = /page\s*\.\s*(goto|click|dblclick|fill|type|press|waitForTimeout|waitForSelector|dragAndDrop|selectOption)\s*\(([^)]*)\)/g;
  // Estilo moderno (el que genera nuestro propio exportador toPlaywright):
  // page.locator('sel').click() / .dblclick() / .click({ button: 'middle' })
  const LOCATOR_RE = /page\s*\.\s*locator\s*\(\s*['"`]((?:\\.|[^'"`\\])*)['"`]\s*\)\s*\.\s*(click|dblclick)\s*\(([^)]*)\)/g;

  for (const line of lines) {
    let m;

    LOCATOR_RE.lastIndex = 0;
    while ((m = LOCATOR_RE.exec(line))) {
      totalCalls++;
      const [, selRaw, action, actionArgs] = m;
      const sel = unescapeStr(selRaw);
      const isMiddle = action === "click" && /button\s*:\s*['"`]middle['"`]/.test(actionArgs);
      steps.push({ type: isMiddle ? "middleclick" : action, data: { selector: sel }, delay: pendingDelay });
      pendingDelay = 0;
    }

    CALL_RE.lastIndex = 0;
    while ((m = CALL_RE.exec(line))) {
      totalCalls++;
      const [, fn, argsText] = m;
      switch (fn) {
        case "goto": {
          const u = firstStringArg(argsText);
          if (u) {
            if (!url) url = u; // primera navegacion = punto de partida de la repeticion
            steps.push({ type: "navigation", data: { url: u }, delay: pendingDelay });
            pendingDelay = 0;
          } else {
            warnings.push(`goto(...) en linea sin URL reconocible: ${line.trim().slice(0, 80)}`);
          }
          break;
        }
        case "click":
        case "dblclick": {
          const sel = firstStringArg(argsText);
          if (sel) {
            steps.push({ type: fn === "dblclick" ? "dblclick" : "click", data: { selector: sel }, delay: pendingDelay });
            pendingDelay = 0;
          } else {
            warnings.push(`${fn}(...) sin selector reconocible: ${line.trim().slice(0, 80)}`);
          }
          break;
        }
        case "fill":
        case "type": {
          const sel = firstStringArg(argsText);
          const val = secondStringArg(argsText);
          if (sel) {
            steps.push({ type: "input", data: { selector: sel, value: val || "" }, delay: pendingDelay });
            pendingDelay = 0;
          } else {
            warnings.push(`${fn}(...) sin selector reconocible: ${line.trim().slice(0, 80)}`);
          }
          break;
        }
        case "press": {
          const sel = firstStringArg(argsText);
          const key = secondStringArg(argsText);
          if (sel && key) {
            steps.push({ type: "key", data: { selector: sel, key, run: false }, delay: pendingDelay });
            pendingDelay = 0;
          } else {
            warnings.push(`press(...) incompleto: ${line.trim().slice(0, 80)}`);
          }
          break;
        }
        case "waitForTimeout": {
          const ms = firstNumberArg(argsText);
          if (ms != null) pendingDelay += ms; // se suma como espera del SIGUIENTE paso reproducible
          break;
        }
        case "waitForSelector":
          // No se traduce a un paso propio: el motor de replay ya espera a
          // que el selector del siguiente paso aparezca (waitForEl).
          break;
        case "dragAndDrop": {
          const from = firstStringArg(argsText);
          const to = secondStringArg(argsText);
          if (from && to) {
            steps.push({ type: "dragdrop", data: { from, to }, delay: pendingDelay });
            pendingDelay = 0;
          } else {
            warnings.push(`dragAndDrop(...) incompleto: ${line.trim().slice(0, 80)}`);
          }
          break;
        }
        case "selectOption": {
          const sel = firstStringArg(argsText);
          const val = secondStringArg(argsText);
          if (sel) {
            steps.push({ type: "input", data: { selector: sel, value: val || "" }, delay: pendingDelay });
            pendingDelay = 0;
          } else {
            warnings.push(`selectOption(...) sin selector reconocible: ${line.trim().slice(0, 80)}`);
          }
          break;
        }
      }
    }
  }

  if (!steps.length) warnings.push("No se reconocio ningun paso reproducible (goto/click/fill/type/press) en el script.");
  return { url, steps, warnings, totalCalls };
}

/**
 * Construye un "reporte" con la MISMA forma que usa el motor de replay
 * existente (metadata.url + timeline con cid/seq/tRel), a partir de los
 * pasos parseados — para poder reproducirlo con la infraestructura ya
 * probada (runReplay, waitForEl, typeInto...) en vez de duplicar logica.
 */
export function playwrightStepsToReport(parsed, recordingId) {
  const epoch = Date.now();
  let t = 0;
  const timeline = parsed.steps.map((s, i) => {
    t += Math.max(1, s.delay || 0);
    return {
      type: s.type,
      ts: epoch + t,
      tRel: t,
      delay: s.delay || 0,
      seq: i + 1,
      cid: `${recordingId}#${i + 1}`,
      data: s.data,
    };
  });
  return {
    metadata: {
      tool: "CharlyAudit",
      url: parsed.url || null,
      capturedAt: new Date().toISOString(),
      startedAt: new Date(epoch).toISOString(),
      endedAt: new Date(epoch + t).toISOString(),
      durationMs: t,
      eventCount: timeline.length,
      counts: timeline.reduce((c, e) => ((c[e.type] = (c[e.type] || 0) + 1), c), {}),
      recording: { recordingId, startUrl: parsed.url || null, source: "playwright-import" },
    },
    timeline,
  };
}
