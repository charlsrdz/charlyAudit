/**
 * exporters.js — Formateadores de salida (Cypress / Playwright)
 * ============================================================
 * Traducen el reporte (metadata + timeline) en una PRUEBA AUTOMATIZADA ejecutable,
 * no en una simple secuencia de clicks. El valor frente al replay interno:
 *   - corre headless en CI, sin la extension ni un humano mirando;
 *   - corre en cada deploy como prueba de regresion;
 *   - AFIRMA (assert) lo que debe cumplirse, no solo "hace clicks".
 *
 * Por eso el script generado incluye:
 *   - aserciones de URL en cada navegacion/ruta que vivio el usuario;
 *   - asercion del valor real tras escribir en un input;
 *   - guard de errores JS: la prueba falla si la app lanza errores no controlados;
 *   - esperas correctas (auto-wait del framework) en vez de sleeps fragiles.
 * Modulo puro (sin estado, sin DOM).
 */

/** Escapa una cadena para incrustarla en comillas simples de JS. */
function q(str) {
  return String(str ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "\\'")
    .replace(/\r?\n/g, "\\n");
}
/** Escapa para usar dentro de un RegExp literal /.../ (incluida la barra). */
function qRe(str) {
  return String(str ?? "").replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
}
/** Parte identificativa de una URL (path + query) para aserciones robustas. */
function urlPart(u) {
  try {
    const url = new URL(u, "http://x");
    return (url.pathname || "/") + (url.search || "");
  } catch {
    return String(u || "");
  }
}

/** Ultimo snapshot de Web Vitals del reporte (presupuesto de performance). */
function lastVitals(report) {
  const v = report.timeline.filter((e) => e.type === "web-vitals");
  return v.length ? v[v.length - 1].data : null;
}
/** Hallazgos de seguridad agrupados por tipo. */
function securityFindings(report) {
  const map = {};
  for (const e of report.timeline.filter((x) => x.type === "security")) {
    const k = e.data.kind;
    (map[k] = map[k] || { kind: k, sev: e.data.severidad, n: 0 }).n++;
  }
  return Object.values(map);
}

/** URL inicial del timeline (primera navegacion/ruta o metadata.url). */
function initialUrl(report) {
  const nav = report.timeline.find((e) => e.type === "navigation" || e.type === "route");
  if (nav) return nav.data.url || nav.data.to || report.metadata.url;
  return report.metadata.url;
}

// Mapeo de teclas con nombre a la sintaxis de cada framework.
const CY_SPECIAL = {
  Enter: "{enter}", Tab: "{tab}", Escape: "{esc}", Backspace: "{backspace}",
  Delete: "{del}", ArrowUp: "{uparrow}", ArrowDown: "{downarrow}",
  ArrowLeft: "{leftarrow}", ArrowRight: "{rightarrow}",
};
function cyKeyCmd(d) {
  if (d.masked) return null;
  const tgt = d.selector ? `cy.get('${q(d.selector)}')` : "cy.focused()";
  if (d.run && d.text) return `${tgt}.type('${q(d.text)}');`;
  if (d.key && d.key.length === 1) return `${tgt}.type('${q(d.key)}');`;
  const sp = CY_SPECIAL[d.key];
  return sp ? `${tgt}.type('${sp}');` : null;
}
function pwKeyCmd(d) {
  if (d.masked) return null;
  if (d.run && d.text) {
    return d.selector ? `await page.type('${q(d.selector)}', '${q(d.text)}');` : `await page.keyboard.type('${q(d.text)}');`;
  }
  if (d.key && d.key.length === 1) {
    return d.selector ? `await page.type('${q(d.selector)}', '${q(d.key)}');` : `await page.keyboard.type('${q(d.key)}');`;
  }
  if (!d.key) return null;
  return d.selector ? `await page.press('${q(d.selector)}', '${q(d.key)}');` : `await page.keyboard.press('${q(d.key)}');`;
}

// ---------------------------------------------------------------------------
// CYPRESS
// ---------------------------------------------------------------------------

export function toCypress(report) {
  const L = [];
  L.push("// Generado por CharlyPlugin QA Suite — prueba de regresion ejecutable.");
  L.push("// Ejecutar: npx cypress run   (headless, en CI o local; sin la extension).");
  L.push(`// Capturado: ${report.metadata.capturedAt} · ${report.metadata.eventCount} eventos`);
  L.push("");
  L.push("describe('Sesion QA reproducible', () => {");
  L.push("  it('reproduce la sesion y verifica el resultado', () => {");
  L.push("    const erroresJS = [];");
  L.push("    // Guard de errores: la prueba falla si la app lanza errores no controlados.");
  L.push("    cy.on('uncaught:exception', (err) => { erroresJS.push(err.message); return false; });");
  L.push("");

  const first = initialUrl(report);
  if (first) L.push(`    cy.visit('${q(first)}');`);

  for (const e of report.timeline) {
    const d = e.data || {};
    switch (e.type) {
      case "click":
        L.push(`    cy.get('${q(d.selector)}').should('be.visible').click();`);
        break;
      case "dblclick":
        L.push(`    cy.get('${q(d.selector)}').should('be.visible').dblclick();`);
        break;
      case "middleclick":
        L.push(`    cy.get('${q(d.selector)}').should('be.visible').click({ button: 'middle' });`);
        break;
      case "key": {
        const cmd = cyKeyCmd(d);
        L.push(cmd ? `    ${cmd}` : `    // [tecla] ${q(d.key)}${d.masked ? " (enmascarada)" : ""}`);
        break;
      }
      case "dragdrop":
        L.push(`    // [drag&drop] ${q(d.from)} -> ${q(d.to)} (requiere @4tw/cypress-drag-drop)`);
        break;
      case "input": {
        const sel = q(d.selector);
        if (d.tag === "select") {
          L.push(`    cy.get('${sel}').select('${q(d.value)}');`);
          if (!d.masked) L.push(`    cy.get('${sel}').should('have.value', '${q(d.value)}');`);
        } else if (d.masked) {
          L.push(`    cy.get('${sel}').clear().type('***'); // valor enmascarado`);
        } else {
          L.push(`    cy.get('${sel}').clear().type('${q(d.value)}');`);
          L.push(`    cy.get('${sel}').should('have.value', '${q(d.value)}'); // verifica el valor`);
        }
        break;
      }
      case "route":
        // Asercion real: el usuario llego a esta ruta.
        L.push(`    cy.url().should('include', '${q(urlPart(d.to))}'); // navego a ${q(d.to)}`);
        break;
      case "navigation":
        if (e.data.reason === "load" && d.url) {
          L.push(`    cy.location('pathname').should('include', '${q(urlPart(d.url))}'); // carga de pagina`);
        }
        break;
      case "network":
        if (d.ok === false || (d.status || 0) >= 400)
          L.push(`    // [red FALLIDA] ${d.method} ${d.status || d.error} ${q(d.url)} — revisar`);
        break;
      case "error":
        L.push(`    // [error grabado] ${q(d.message || d.reason)} (origen: ${q(d.ref || "?")})`);
        break;
      default:
        break; // console/fn/scroll/focus/etc. no se traducen a comandos
    }
  }

  L.push("");
  L.push("    // Verificacion final: la sesion no produjo errores JS no controlados.");
  L.push("    cy.then(() => { expect(erroresJS, 'errores JS durante la sesion').to.have.length(0); });");
  const vc = lastVitals(report);
  if (vc) {
    L.push(`    // Presupuesto de performance grabado: LCP ${vc.lcpMs}ms · CLS ${vc.cls} · INP ${vc.inpMs}ms · TBT ${vc.tbtMs}ms`);
    L.push(`    cy.window().then((w) => { const n = w.performance.getEntriesByType('navigation')[0]; if (n) expect(n.domContentLoadedEventEnd, 'DCL dentro de presupuesto').to.be.lessThan(${Math.max(3000, Math.round((vc.lcpMs || 0) * 1.5))}); });`);
  }
  const sc = securityFindings(report);
  if (sc.length) {
    L.push("    // Hallazgos de seguridad detectados al grabar (revisar / corregir):");
    for (const f of sc) L.push(`    //  [${f.sev}] ${f.kind} x${f.n}`);
  }
  L.push("  });");
  L.push("});");
  L.push("");
  return L.join("\n");
}

// ---------------------------------------------------------------------------
// PLAYWRIGHT
// ---------------------------------------------------------------------------

export function toPlaywright(report) {
  const L = [];
  L.push("// Generado por CharlyPlugin QA Suite — prueba de regresion ejecutable.");
  L.push("// Ejecutar: npx playwright test   (headless, multi-navegador, en CI).");
  L.push(`// Capturado: ${report.metadata.capturedAt} · ${report.metadata.eventCount} eventos`);
  L.push("import { test, expect } from '@playwright/test';");
  L.push("");
  L.push("test('reproduce la sesion y verifica el resultado', async ({ page }) => {");
  L.push("  const erroresJS = [];");
  L.push("  // Guard de errores: la prueba falla si la app lanza errores no controlados.");
  L.push("  page.on('pageerror', (e) => erroresJS.push(String(e)));");
  L.push("  page.on('console', (m) => { if (m.type() === 'error') erroresJS.push(m.text()); });");
  L.push("");

  const first = initialUrl(report);
  if (first) L.push(`  await page.goto('${q(first)}');`);

  for (const e of report.timeline) {
    const d = e.data || {};
    switch (e.type) {
      case "click":
        L.push(`  await page.locator('${q(d.selector)}').click(); // auto-espera visible+habilitado`);
        break;
      case "dblclick":
        L.push(`  await page.locator('${q(d.selector)}').dblclick();`);
        break;
      case "middleclick":
        L.push(`  await page.locator('${q(d.selector)}').click({ button: 'middle' });`);
        break;
      case "key": {
        const cmd = pwKeyCmd(d);
        L.push(cmd ? `  ${cmd}` : `  // [tecla] ${q(d.key)}${d.masked ? " (enmascarada)" : ""}`);
        break;
      }
      case "dragdrop":
        L.push(`  await page.dragAndDrop('${q(d.from)}', '${q(d.to)}');`);
        break;
      case "input": {
        const sel = q(d.selector);
        if (d.tag === "select") {
          L.push(`  await page.selectOption('${sel}', '${q(d.value)}');`);
        } else if (d.masked) {
          L.push(`  await page.fill('${sel}', '***'); // valor enmascarado`);
        } else {
          L.push(`  await page.fill('${sel}', '${q(d.value)}');`);
          L.push(`  await expect(page.locator('${sel}')).toHaveValue('${q(d.value)}'); // verifica el valor`);
        }
        break;
      }
      case "route":
        L.push(`  await expect(page).toHaveURL(/${qRe(urlPart(d.to))}/); // navego a ${q(d.to)}`);
        break;
      case "navigation":
        if (e.data.reason === "load" && d.url) {
          L.push(`  await page.waitForLoadState('load');`);
          L.push(`  await expect(page).toHaveURL(/${qRe(urlPart(d.url))}/);`);
        }
        break;
      case "network":
        if (d.ok === false || (d.status || 0) >= 400)
          L.push(`  // [red FALLIDA] ${d.method} ${d.status || d.error} ${q(d.url)} — revisar`);
        break;
      case "error":
        L.push(`  // [error grabado] ${q(d.message || d.reason)} (origen: ${q(d.ref || "?")})`);
        break;
      default:
        break;
    }
  }

  L.push("");
  L.push("  // Verificacion final: la sesion no produjo errores JS no controlados.");
  L.push("  expect(erroresJS, 'errores JS durante la sesion').toEqual([]);");

  // Presupuesto de performance: mide en vivo y compara con lo grabado (falla en CI).
  const v = lastVitals(report);
  if (v) {
    L.push("");
    L.push("  // Presupuesto de performance (umbrales = lo observado al grabar).");
    L.push("  const vitals = await page.evaluate(() => new Promise((res) => {");
    L.push("    let lcp = 0, cls = 0;");
    L.push("    new PerformanceObserver((l) => { for (const e of l.getEntries()) lcp = e.renderTime || e.loadTime || e.startTime; }).observe({ type: 'largest-contentful-paint', buffered: true });");
    L.push("    new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) cls += e.value; }).observe({ type: 'layout-shift', buffered: true });");
    L.push("    setTimeout(() => res({ lcp: Math.round(lcp), cls: +cls.toFixed(3) }), 1500);");
    L.push("  }));");
    L.push(`  expect(vitals.lcp, 'LCP no debe empeorar').toBeLessThanOrEqual(${Math.max(2500, Math.round((v.lcpMs || 0) * 1.2))});`);
    L.push(`  expect(vitals.cls, 'CLS no debe empeorar').toBeLessThanOrEqual(${Math.max(0.1, +(((v.cls || 0) * 1.2).toFixed(3)))});`);
    L.push(`  // Referencia grabada: LCP ${v.lcpMs}ms · CLS ${v.cls} · INP ${v.inpMs}ms · TBT ${v.tbtMs}ms · ${v.longTasks} long tasks`);
  }
  const sec = securityFindings(report);
  if (sec.length) {
    L.push("");
    L.push("  // Hallazgos de seguridad detectados al grabar (revisar / corregir):");
    for (const f of sec) L.push(`  //  [${f.sev}] ${f.kind} x${f.n}`);
  }

  L.push("});");
  L.push("");
  return L.join("\n");
}
