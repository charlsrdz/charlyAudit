/**
 * Service worker de CharlyPlugin (background, MV3).
 *
 * Responsabilidades:
 *   - Ciclo de vida (onInstalled): configuracion por defecto + menu contextual.
 *   - Atajos de teclado (chrome.commands).
 *   - Router de mensajeria: traduce {action, payload} de content scripts / popup
 *     a metodos de CharlyAPI mediante una lista blanca.
 *
 * Se carga como modulo ES (manifest: "type": "module").
 */
import CharlyAPI from "../lib/CharlyAPI.js";
// Modulo de grabacion de QA: registra sus propios listeners al importarse.
import "../qa/background.js";

const api = new CharlyAPI();

// --- Ciclo de vida -----------------------------------------------------------
chrome.runtime.onInstalled.addListener(async (details) => {
  console.info(`[CharlyPlugin] Instalado/actualizado: ${details.reason}`);

  // Configuracion estandar por defecto (idempotente).
  const existing = await api.storageGet("config");
  if (!existing) {
    await api.storageSet("config", {
      initializedAt: new Date().toISOString(),
      telemetry: false, // privacidad primero
      theme: "system",
    });
  }

  // Menus contextuales (se recrean limpios en cada instalacion).
  await api.clearContextMenus();
  await api.createContextMenu({
    id: "charly-inspect",
    title: 'CharlyPlugin: inspeccionar "%s"',
    contexts: ["selection"],
  });
  await api.createContextMenu({
    id: "charly-assistant",
    title: "CharlyPlugin: abrir asistente IA",
    contexts: ["page", "selection", "action"],
  });
});

// --- Menu contextual ---------------------------------------------------------
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "charly-inspect") {
    await api.notify("Seleccion capturada", (info.selectionText || "").slice(0, 120));
  } else if (info.menuItemId === "charly-assistant") {
    // Abre el panel lateral del asistente (el clic de menu es un gesto valido).
    try {
      if (tab && tab.id != null) await chrome.sidePanel.open({ tabId: tab.id });
      else if (tab && tab.windowId != null) await chrome.sidePanel.open({ windowId: tab.windowId });
    } catch (err) {
      console.warn("[CharlyPlugin] No se pudo abrir el panel:", err.message);
    }
  }
});

// --- Atajos de teclado: deshabilitados a proposito ----------------------------
// El plugin se abre SOLO manualmente (icono o menu contextual). No se registran
// atajos para no interferir con la captura/replay de teclas de la suite de QA.

// --- Router de mensajeria -----------------------------------------------------
/**
 * Lista blanca de acciones invocables por mensaje. Solo lo declarado aqui es
 * accesible para content scripts u otras paginas, evitando exponer toda la API.
 * @type {Object<string, (payload:*) => Promise<*>>}
 */
const ACTIONS = {
  ping: async () => ({ pong: true, at: Date.now() }),
  getActiveTab: () => api.getActiveTab(),
  getPageInfo: () => api.getPageInfo(),
  getPageText: () => api.getPageText(),
  getPageLinks: () => api.getPageLinks(),
  getSystemSummary: () => api.getSystemSummary(),
  getDeviceProfile: () => api.getDeviceProfile(),
  getExtensionInfo: () => api.getExtensionInfo(),
  getTabs: (payload) => api.getTabs(payload || {}),
  captureScreenshot: () => api.captureScreenshot(),
  notify: ({ title, message }) => api.notify(title, message),
};

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  // Los mensajes con "channel" pertenecen a otros modulos (p.ej. QA): ignorar.
  if (request && request.channel) return false;

  const handler = ACTIONS[request?.action];
  if (!handler) {
    sendResponse({ ok: false, error: `Accion desconocida: ${request?.action}` });
    return false;
  }
  Promise.resolve(handler(request.payload))
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: err.message }));
  return true; // respuesta asincrona
});

console.info("[CharlyPlugin] Service worker activo.");
