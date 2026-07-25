/**
 * Content script de CharlyPlugin.
 *
 * Se ejecuta en el contexto de cada pagina (aislado). Tiene acceso al DOM de
 * la pagina pero acceso muy limitado a chrome.* (basicamente runtime y storage).
 * Por eso delega las capacidades del navegador/sistema al service worker
 * mediante mensajeria.
 *
 * Mantiene una huella minima: no modifica el DOM ni observa contenido del
 * usuario; solo registra un listener para peticiones internas de la extension.
 */
(() => {
  "use strict";

  // Evita doble inyeccion si el script se vuelve a cargar.
  if (window.__charlyPluginInjected) return;
  window.__charlyPluginInjected = true;

  console.debug("[CharlyPlugin] Content script cargado en", location.host);

  /**
   * Atiende peticiones provenientes del popup o del service worker que
   * necesiten leer el DOM de esta pagina.
   */
  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    switch (request?.action) {
      case "page:getMetrics":
        sendResponse({
          ok: true,
          data: {
            title: document.title,
            url: location.href,
            links: document.links.length,
            images: document.images.length,
            forms: document.forms.length,
            wordCount: (document.body?.innerText || "").trim().split(/\s+/).filter(Boolean)
              .length,
          },
        });
        return false; // respuesta sincrona
      default:
        return false;
    }
  });
})();
