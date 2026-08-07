/**
 * reactive-store.js — Estándar de UI reactiva de CharlyAudit (v2.5.8a).
 * =======================================================================
 * Módulo puro (sin dependencias de chrome.*), usado por igual en el popup y
 * el panel lateral. Reemplaza el patrón anterior — cada superficie con su
 * propio `setInterval` ad-hoc que releía todo y reemplazaba `innerHTML` sin
 * condición — por un estándar único: un estado observable (`Store`) del que
 * la UI se SUSCRIBE, y un temporizador centralizado (`Poller`) que solo
 * empuja datos nuevos al estado. Si un valor no cambió, nadie se entera y
 * el DOM no se toca — que es precisamente lo que evita que un `<details>`
 * abierto por el usuario se cierre solo en mitad de una repetición activa.
 *
 * Motivación concreta (issue reportado): la pestaña "Repetición" reconstruía
 * `tl-list` completo cada 2.5s sin comparar si había pasos nuevos, así que
 * abrir el detalle de un paso y esperar un ciclo lo cerraba de golpe. El fix
 * real no es "no tocar el DOM nunca" — es tocar el DOM SOLO cuando algo
 * cambió, y cuando se toca, preservar explícitamente qué filas estaban
 * abiertas (`captureOpenRows`/`restoreOpenRows`).
 */

/** Igualdad estructural superficial vía JSON — suficiente para los objetos
 *  planos (conteos, resúmenes, listas de eventos) que maneja esta UI. */
function sameValue(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a && b && typeof a === "object") {
    try {
      return JSON.stringify(a) === JSON.stringify(b);
    } catch {
      return false;
    }
  }
  return false;
}

/**
 * Estado observable minimalista. `set()` es un no-op silencioso si el valor
 * no cambió — ningún suscriptor se dispara, ningún render ocurre. Esa es la
 * garantía central del estándar: renders solo ante cambios reales.
 */
export class Store {
  constructor(initial = {}) {
    this._state = { ...initial };
    this._subs = new Map(); // key -> Set<fn(value, prev)>
  }
  get(key) {
    return this._state[key];
  }
  getAll() {
    return { ...this._state };
  }
  /** @returns {boolean} true si el valor cambió y se notificó a los suscriptores. */
  set(key, value) {
    const prev = this._state[key];
    if (sameValue(prev, value)) return false;
    this._state[key] = value;
    const subs = this._subs.get(key);
    if (subs) for (const fn of subs) fn(value, prev);
    return true;
  }
  /** Aplica varios cambios de una vez (cada uno respeta la misma regla de no-op). */
  patch(partial) {
    for (const [k, v] of Object.entries(partial)) this.set(k, v);
  }
  /** Se suscribe a una clave; devuelve una función para desuscribirse. */
  subscribe(key, fn) {
    if (!this._subs.has(key)) this._subs.set(key, new Set());
    this._subs.get(key).add(fn);
    return () => this._subs.get(key)?.delete(fn);
  }
}

/**
 * Temporizador único y centralizado: pausa sola cuando la pestaña/panel no
 * es visible (`document.visibilityState`), evitando trabajo innecesario en
 * segundo plano. Sustituye a los `setInterval(() => { if (visible) ... })`
 * repetidos de forma idéntica en cada superficie.
 */
export class Poller {
  constructor(fn, ms) {
    this.fn = fn;
    this.ms = ms;
    this._timer = null;
  }
  start() {
    this.stop();
    this._timer = setInterval(() => {
      if (document.visibilityState === "visible") this.fn();
    }, this.ms);
    return this;
  }
  stop() {
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
  }
}

/**
 * Captura las claves (`data-rk`) de las filas actualmente expandidas
 * (`.is-open`) dentro de un contenedor, antes de reemplazar su `innerHTML`.
 */
export function captureOpenRows(container, keyAttr = "data-rk") {
  const open = new Set();
  if (!container) return open;
  container.querySelectorAll(`.tl-row.is-open[${keyAttr}]`).forEach((el) => {
    const k = el.getAttribute(keyAttr);
    if (k) open.add(k);
  });
  return open;
}

/** Reaplica `.is-open` a las filas cuya clave estaba en el set capturado. */
export function restoreOpenRows(container, openSet, keyAttr = "data-rk") {
  if (!container || !openSet || !openSet.size) return;
  container.querySelectorAll(`.tl-row[${keyAttr}]`).forEach((el) => {
    if (openSet.has(el.getAttribute(keyAttr))) el.classList.add("is-open");
  });
}
