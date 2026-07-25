/**
 * chat-cache.js — Cache de conversacion (CharlyPlugin · IA)
 * ========================================================
 * Persiste la conversacion en el localStorage del panel (origen de la
 * extension). Espacio de nombres propio del plugin y controles para
 * mantener / guardar / liberar la cache.
 *
 *   - current : conversacion en curso (autoguardado).
 *   - keep    : si el usuario quiere conservar la cache entre sesiones.
 *   - saved   : lista de sesiones guardadas manualmente.
 */
const NS = "charlyplugin:assistant:";
const K_CURRENT = NS + "current";
const K_KEEP = NS + "keep";
const K_SAVED = NS + "saved";

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}
function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false; // cuota agotada
  }
}

export const ChatCache = {
  // --- Conversacion en curso ---
  loadCurrent() {
    return read(K_CURRENT, []);
  },
  saveCurrent(history) {
    return write(K_CURRENT, history || []);
  },
  clearCurrent() {
    localStorage.removeItem(K_CURRENT);
  },

  // --- Preferencia de conservacion ---
  getKeep() {
    return read(K_KEEP, true) === true;
  },
  setKeep(value) {
    write(K_KEEP, value === true);
  },

  // --- Sesiones guardadas ---
  listSaved() {
    return read(K_SAVED, []);
  },
  saveSession(name, history) {
    const list = this.listSaved();
    const entry = {
      id: "s-" + Date.now().toString(36),
      name: (name || "Sesion").slice(0, 60),
      at: new Date().toISOString(),
      turns: history.length,
      history,
    };
    list.unshift(entry);
    // Conserva como mucho 20 sesiones para no saturar el almacenamiento.
    write(K_SAVED, list.slice(0, 20));
    return entry;
  },
  loadSaved(id) {
    return this.listSaved().find((s) => s.id === id) || null;
  },
  deleteSaved(id) {
    write(
      K_SAVED,
      this.listSaved().filter((s) => s.id !== id)
    );
  },

  // --- Utilidades ---
  /** Tamano aproximado en bytes que ocupa la cache del asistente. */
  sizeBytes() {
    let total = 0;
    for (const key of [K_CURRENT, K_KEEP, K_SAVED]) {
      const raw = localStorage.getItem(key);
      if (raw) total += key.length + raw.length;
    }
    return total * 2; // UTF-16 aprox.
  },
  /** Libera TODO el espacio del asistente (current + saved + keep). */
  freeAll() {
    [K_CURRENT, K_KEEP, K_SAVED].forEach((k) => localStorage.removeItem(k));
  },
};
