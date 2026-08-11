/**
 * Service worker de CharlyPlugin (background, MV3).
 *
 * Responsabilidades:
 *   - Ciclo de vida (onInstalled): configuracion por defecto + menu contextual.
 *   - Menu contextual: notificacion de seleccion + atajo para abrir el panel.
 *   - Importa el modulo de grabacion de QA (src/qa/background.js), que es
 *     quien realmente atiende toda la mensajeria de la extension (canal
 *     "qa-control", usado por el popup y el panel lateral).
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

console.info("[CharlyPlugin] Service worker activo.");
