/**
 * CharlyAPI
 * ---------
 * Clase utilitaria central de CharlyPlugin. Envuelve las APIs de Chrome
 * (chrome.*) y del entorno (navigator) en metodos asincronos, documentados
 * y con manejo de errores homogeneo.
 *
 * Contextos validos: service worker (background) y paginas de extension
 * (popup, options). NO esta pensada para inyectarse como content script:
 * los content scripts tienen acceso limitado a chrome.* y deben comunicarse
 * por mensajeria (ver src/content/content-script.js).
 *
 * Convenciones:
 *   - Todos los metodos publicos devuelven una Promise.
 *   - Los errores se propagan como Error con mensaje legible.
 *   - Los metodos que dependen de un permiso opcional lo verifican primero
 *     y lanzan un error claro indicando que permiso solicitar.
 *
 * @example
 *   import CharlyAPI from "../lib/CharlyAPI.js";
 *   const api = new CharlyAPI();
 *   const tab = await api.getActiveTab();
 *
 * @module CharlyAPI
 */
export default class CharlyAPI {
  constructor() {
    if (typeof chrome === "undefined" || !chrome.runtime) {
      throw new Error(
        "CharlyAPI requiere el contexto de una extension de Chrome (chrome.* no disponible)."
      );
    }
    /** @type {"service_worker"|"extension_page"|"unknown"} */
    this.context = CharlyAPI.detectContext();
  }

  // ===========================================================================
  // 0. INTERNOS / UTILIDADES
  // ===========================================================================

  /**
   * Detecta el contexto de ejecucion actual.
   * @returns {"service_worker"|"extension_page"|"unknown"}
   */
  static detectContext() {
    if (typeof window === "undefined" && typeof self !== "undefined") {
      // Sin window pero con self => service worker / worker.
      if (typeof ServiceWorkerGlobalScope !== "undefined") {
        return "service_worker";
      }
    }
    if (typeof window !== "undefined" && typeof document !== "undefined") {
      return "extension_page";
    }
    return "unknown";
  }

  /**
   * Convierte una API basada en callback (estilo chrome clasico) en Promise,
   * respetando chrome.runtime.lastError.
   * @param {Function} fn  Funcion a invocar; recibe (...args, callback).
   * @param {...*} args    Argumentos previos al callback.
   * @returns {Promise<*>}
   */
  static _fromCallback(fn, ...args) {
    return new Promise((resolve, reject) => {
      try {
        fn(...args, (result) => {
          const err = chrome.runtime.lastError;
          if (err) reject(new Error(err.message));
          else resolve(result);
        });
      } catch (e) {
        reject(e);
      }
    });
  }

  /**
   * Verifica que un permiso (opcional) este concedido; si no, lanza un error
   * con instrucciones precisas.
   * @param {string} permission  Nombre del permiso (p.ej. "bookmarks").
   * @returns {Promise<void>}
   */
  async _ensurePermission(permission) {
    const granted = await chrome.permissions.contains({ permissions: [permission] });
    if (!granted) {
      throw new Error(
        `Permiso "${permission}" no concedido. Solicitelo desde un gesto del usuario ` +
          `con api.requestPermissions(["${permission}"]) (p.ej. al pulsar un boton del popup).`
      );
    }
  }

  /**
   * Verifica acceso al host de una URL (necesario para cookies y captura).
   * @param {string} url
   * @returns {Promise<void>}
   */
  async _ensureHost(url) {
    const origin = new URL(url).origin + "/*";
    const granted = await chrome.permissions.contains({ origins: [origin] });
    if (!granted) {
      throw new Error(
        `Sin acceso al host "${origin}". Solicitelo con ` +
          `api.requestPermissions([], ["${origin}"]) desde un gesto del usuario.`
      );
    }
  }

  // ===========================================================================
  // 1. RUNTIME / EXTENSION
  // ===========================================================================

  /**
   * Devuelve metadatos de la extension (manifest, id, version, url base).
   * @returns {Promise<{id:string, version:string, name:string, manifest:object, baseUrl:string}>}
   */
  async getExtensionInfo() {
    const manifest = chrome.runtime.getManifest();
    return {
      id: chrome.runtime.id,
      name: manifest.name,
      version: manifest.version,
      manifest,
      baseUrl: chrome.runtime.getURL(""),
    };
  }

  /** Abre la pagina de opciones declarada (si existe). @returns {Promise<void>} */
  async openOptionsPage() {
    return chrome.runtime.openOptionsPage();
  }

  /**
   * Resuelve la URL absoluta de un recurso empaquetado en la extension.
   * @param {string} path  Ruta relativa, p.ej. "icons/icon48.png".
   * @returns {string}
   */
  getResourceUrl(path) {
    return chrome.runtime.getURL(path);
  }

  // ===========================================================================
  // 2. PERMISOS OPCIONALES
  // ===========================================================================

  /**
   * Indica si un conjunto de permisos esta concedido.
   * @param {string[]} [permissions=[]]
   * @param {string[]} [origins=[]]
   * @returns {Promise<boolean>}
   */
  async hasPermissions(permissions = [], origins = []) {
    return chrome.permissions.contains({ permissions, origins });
  }

  /**
   * Solicita permisos opcionales. DEBE invocarse dentro de un gesto del
   * usuario (click) o Chrome lo rechaza.
   * @param {string[]} [permissions=[]]
   * @param {string[]} [origins=[]]
   * @returns {Promise<boolean>}  true si el usuario los concedio.
   */
  async requestPermissions(permissions = [], origins = []) {
    return chrome.permissions.request({ permissions, origins });
  }

  /**
   * Revoca permisos opcionales previamente concedidos.
   * @param {string[]} [permissions=[]]
   * @param {string[]} [origins=[]]
   * @returns {Promise<boolean>}
   */
  async removePermissions(permissions = [], origins = []) {
    return chrome.permissions.remove({ permissions, origins });
  }

  // ===========================================================================
  // 3. ALMACENAMIENTO (chrome.storage.local)
  // ===========================================================================

  /**
   * Guarda un valor.
   * @param {string} key
   * @param {*} value  Debe ser serializable a JSON.
   * @returns {Promise<void>}
   */
  async storageSet(key, value) {
    return chrome.storage.local.set({ [key]: value });
  }

  /**
   * Lee un valor (o undefined si no existe).
   * @param {string} key
   * @returns {Promise<*>}
   */
  async storageGet(key) {
    const data = await chrome.storage.local.get(key);
    return data[key];
  }

  /** Elimina una clave. @param {string} key @returns {Promise<void>} */
  async storageRemove(key) {
    return chrome.storage.local.remove(key);
  }

  /** Vacia todo el almacenamiento local. @returns {Promise<void>} */
  async storageClear() {
    return chrome.storage.local.clear();
  }

  // ===========================================================================
  // 4. PESTANAS (chrome.tabs)
  // ===========================================================================

  /**
   * Devuelve la pestana activa de la ventana actual.
   * @returns {Promise<chrome.tabs.Tab>}
   */
  async getActiveTab() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) throw new Error("No se encontro pestana activa.");
    return tab;
  }

  /**
   * Lista pestanas segun un filtro de consulta.
   * @param {chrome.tabs.QueryInfo} [query={}]
   * @returns {Promise<chrome.tabs.Tab[]>}
   */
  async getTabs(query = {}) {
    return chrome.tabs.query(query);
  }

  /** Crea una pestana. @param {chrome.tabs.CreateProperties} props @returns {Promise<chrome.tabs.Tab>} */
  async createTab(props) {
    return chrome.tabs.create(props);
  }

  /** Actualiza una pestana. @param {number} tabId @param {object} props @returns {Promise<chrome.tabs.Tab>} */
  async updateTab(tabId, props) {
    return chrome.tabs.update(tabId, props);
  }

  /** Recarga una pestana. @param {number} [tabId] @returns {Promise<void>} */
  async reloadTab(tabId) {
    return chrome.tabs.reload(tabId);
  }

  /** Duplica una pestana. @param {number} tabId @returns {Promise<chrome.tabs.Tab>} */
  async duplicateTab(tabId) {
    return chrome.tabs.duplicate(tabId);
  }

  /** Cierra una o varias pestanas. @param {number|number[]} tabIds @returns {Promise<void>} */
  async closeTabs(tabIds) {
    return chrome.tabs.remove(tabIds);
  }

  // ===========================================================================
  // 5. VENTANAS DEL NAVEGADOR (chrome.windows)
  // ===========================================================================

  /** Devuelve la ventana actual. @param {object} [opts] @returns {Promise<chrome.windows.Window>} */
  async getCurrentWindow(opts = { populate: true }) {
    return chrome.windows.getCurrent(opts);
  }

  /** Lista todas las ventanas. @param {object} [opts] @returns {Promise<chrome.windows.Window[]>} */
  async getAllWindows(opts = { populate: true }) {
    return chrome.windows.getAll(opts);
  }

  /** Crea una ventana. @param {chrome.windows.CreateData} [data] @returns {Promise<chrome.windows.Window>} */
  async createWindow(data) {
    return chrome.windows.create(data);
  }

  /** Enfoca una ventana. @param {number} windowId @returns {Promise<chrome.windows.Window>} */
  async focusWindow(windowId) {
    return chrome.windows.update(windowId, { focused: true });
  }

  /** Cierra una ventana. @param {number} windowId @returns {Promise<void>} */
  async closeWindow(windowId) {
    return chrome.windows.remove(windowId);
  }

  // ===========================================================================
  // 6. VENTANA / PAGINA ACTUAL (chrome.scripting sobre la pestana activa)
  //    Requiere "scripting" + acceso al host (activeTab basta tras un click).
  // ===========================================================================

  /**
   * Ejecuta una funcion en el contexto de la pestana activa y devuelve su
   * resultado (debe ser serializable).
   * @param {Function} func           Funcion a inyectar.
   * @param {Array} [args=[]]          Argumentos serializables.
   * @param {"ISOLATED"|"MAIN"} [world="ISOLATED"]
   * @returns {Promise<*>}
   */
  async executeInActiveTab(func, args = [], world = "ISOLATED") {
    const tab = await this.getActiveTab();
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world,
      func,
      args,
    });
    return injection?.result;
  }

  /**
   * Devuelve informacion descriptiva de la pagina activa.
   * @returns {Promise<{title:string,url:string,referrer:string,charset:string,
   *   viewport:{width:number,height:number},scroll:{x:number,y:number},
   *   doctype:string|null}>}
   */
  async getPageInfo() {
    return this.executeInActiveTab(() => ({
      title: document.title,
      url: location.href,
      referrer: document.referrer,
      charset: document.characterSet,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      scroll: { x: window.scrollX, y: window.scrollY },
      doctype: document.doctype ? document.doctype.name : null,
    }));
  }

  /**
   * Devuelve el texto seleccionado por el usuario en la pagina activa.
   * @returns {Promise<string>}
   */
  async getSelectedText() {
    return this.executeInActiveTab(() => window.getSelection().toString());
  }

  /**
   * Consulta elementos de la pagina activa y devuelve una representacion
   * serializable (los nodos DOM no cruzan el limite de contexto).
   * @param {string} selector
   * @param {number} [limit=50]
   * @returns {Promise<Array<{tag:string,id:string,classes:string,text:string,
   *   attributes:Object<string,string>}>>}
   */
  async querySelectorAll(selector, limit = 50) {
    return this.executeInActiveTab(
      (sel, max) =>
        Array.from(document.querySelectorAll(sel))
          .slice(0, max)
          .map((el) => ({
            tag: el.tagName.toLowerCase(),
            id: el.id,
            classes: el.className,
            text: (el.textContent || "").trim().slice(0, 300),
            attributes: Object.fromEntries(
              Array.from(el.attributes).map((a) => [a.name, a.value])
            ),
          })),
      [selector, limit]
    );
  }

  /**
   * Hace click programatico en el primer elemento que coincida.
   * @param {string} selector
   * @returns {Promise<boolean>}  true si encontro y pulso el elemento.
   */
  async clickElement(selector) {
    return this.executeInActiveTab(
      (sel) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        el.click();
        return true;
      },
      [selector]
    );
  }

  /**
   * Inyecta CSS en la pestana activa.
   * @param {string} css
   * @returns {Promise<void>}
   */
  async injectCSS(css) {
    const tab = await this.getActiveTab();
    return chrome.scripting.insertCSS({ target: { tabId: tab.id }, css });
  }

  /**
   * Captura la pestana visible como dataURL (PNG/JPEG).
   * Requiere activeTab (concedido tras click) o acceso al host.
   * @param {object} [options={format:"png"}]
   * @returns {Promise<string>}  dataURL de la imagen.
   */
  async captureScreenshot(options = { format: "png" }) {
    return chrome.tabs.captureVisibleTab(options);
  }

  // ===========================================================================
  // 7. MARCADORES (chrome.bookmarks)  [permiso opcional: "bookmarks"]
  // ===========================================================================

  /** Devuelve el arbol completo de marcadores. @returns {Promise<chrome.bookmarks.BookmarkTreeNode[]>} */
  async getBookmarkTree() {
    await this._ensurePermission("bookmarks");
    return chrome.bookmarks.getTree();
  }

  /** Busca marcadores. @param {string|object} query @returns {Promise<chrome.bookmarks.BookmarkTreeNode[]>} */
  async searchBookmarks(query) {
    await this._ensurePermission("bookmarks");
    return chrome.bookmarks.search(query);
  }

  /** Crea un marcador. @param {chrome.bookmarks.CreateDetails} details @returns {Promise<chrome.bookmarks.BookmarkTreeNode>} */
  async createBookmark(details) {
    await this._ensurePermission("bookmarks");
    return chrome.bookmarks.create(details);
  }

  // ===========================================================================
  // 8. HISTORIAL (chrome.history)  [permiso opcional: "history"]
  // ===========================================================================

  /**
   * Busca en el historial de navegacion.
   * @param {object} [query]  p.ej. { text:"", maxResults:20, startTime:0 }
   * @returns {Promise<chrome.history.HistoryItem[]>}
   */
  async searchHistory(query = { text: "", maxResults: 50 }) {
    await this._ensurePermission("history");
    return chrome.history.search(query);
  }

  // ===========================================================================
  // 9. COOKIES (chrome.cookies)  [permiso opcional: "cookies" + host]
  // ===========================================================================

  /**
   * Lista las cookies de una URL. Requiere "cookies" y acceso al host.
   * @param {string} url
   * @returns {Promise<chrome.cookies.Cookie[]>}
   */
  async getCookies(url) {
    await this._ensurePermission("cookies");
    await this._ensureHost(url);
    return chrome.cookies.getAll({ url });
  }

  // ===========================================================================
  // 10. DESCARGAS (chrome.downloads)  [permiso opcional: "downloads"]
  // ===========================================================================

  /** Inicia una descarga. @param {chrome.downloads.DownloadOptions} options @returns {Promise<number>} downloadId */
  async download(options) {
    await this._ensurePermission("downloads");
    return chrome.downloads.download(options);
  }

  /** Busca descargas. @param {chrome.downloads.DownloadQuery} [query={}] @returns {Promise<chrome.downloads.DownloadItem[]>} */
  async searchDownloads(query = {}) {
    await this._ensurePermission("downloads");
    return chrome.downloads.search(query);
  }

  // ===========================================================================
  // 11. SISTEMA / PC (chrome.system.*)  [permisos requeridos en el manifest]
  //     Estas APIs usan callbacks: se promisifican explicitamente.
  // ===========================================================================

  /** Informacion de CPU (arquitectura, nucleos, modelo). @returns {Promise<object>} */
  async getCpuInfo() {
    return CharlyAPI._fromCallback(chrome.system.cpu.getInfo.bind(chrome.system.cpu));
  }

  /** Memoria total y disponible (bytes). @returns {Promise<{capacity:number,availableCapacity:number}>} */
  async getMemoryInfo() {
    return CharlyAPI._fromCallback(chrome.system.memory.getInfo.bind(chrome.system.memory));
  }

  /** Unidades de almacenamiento del equipo. @returns {Promise<object[]>} */
  async getStorageInfo() {
    return CharlyAPI._fromCallback(chrome.system.storage.getInfo.bind(chrome.system.storage));
  }

  /** Pantallas conectadas y su geometria. @returns {Promise<object[]>} */
  async getDisplayInfo() {
    return CharlyAPI._fromCallback(chrome.system.display.getInfo.bind(chrome.system.display));
  }

  /**
   * Resumen compacto del sistema (CPU + memoria + plataforma).
   * @returns {Promise<{platform:object, cpuArch:string, cpuCores:number,
   *   memoryTotalGB:number, memoryFreeGB:number}>}
   */
  async getSystemSummary() {
    const [cpu, mem, platform] = await Promise.all([
      this.getCpuInfo(),
      this.getMemoryInfo(),
      this.getPlatformInfo(),
    ]);
    return {
      platform,
      cpuArch: cpu.archName,
      cpuCores: cpu.numOfProcessors,
      memoryTotalGB: +(mem.capacity / 1024 ** 3).toFixed(2),
      memoryFreeGB: +(mem.availableCapacity / 1024 ** 3).toFixed(2),
    };
  }

  // ===========================================================================
  // 12. DISPOSITIVO (navigator)  -- solo en paginas de extension salvo donde
  //     se indique. Se valida la disponibilidad de cada API.
  // ===========================================================================

  /** Plataforma del SO (os + arquitectura). @returns {Promise<chrome.runtime.PlatformInfo>} */
  async getPlatformInfo() {
    return chrome.runtime.getPlatformInfo();
  }

  /** User-Agent del navegador. @returns {string} */
  getUserAgent() {
    return navigator.userAgent;
  }

  /** Idiomas preferidos del usuario. @returns {string[]} */
  getLanguages() {
    return Array.from(navigator.languages || [navigator.language]);
  }

  /**
   * Estado de la red (tipo efectivo, ancho de banda estimado, latencia).
   * @returns {{online:boolean, effectiveType?:string, downlinkMbps?:number, rttMs?:number}}
   */
  getNetworkInfo() {
    const c = navigator.connection || navigator.webkitConnection;
    return {
      online: navigator.onLine,
      effectiveType: c?.effectiveType,
      downlinkMbps: c?.downlink,
      rttMs: c?.rtt,
    };
  }

  /**
   * Estado de la bateria. Solo disponible en paginas de extension
   * (no en el service worker).
   * @returns {Promise<{level:number, charging:boolean, chargingTime:number, dischargingTime:number}>}
   */
  async getBatteryInfo() {
    if (typeof navigator.getBattery !== "function") {
      throw new Error(
        "navigator.getBattery no esta disponible en este contexto " +
          "(use el popup u options, no el service worker)."
      );
    }
    const b = await navigator.getBattery();
    return {
      level: b.level,
      charging: b.charging,
      chargingTime: b.chargingTime,
      dischargingTime: b.dischargingTime,
    };
  }

  /**
   * Geolocalizacion actual. Requiere permiso "geolocation" y un contexto con
   * documento (popup/options). [permiso opcional]
   * @param {PositionOptions} [options]
   * @returns {Promise<{latitude:number, longitude:number, accuracy:number, timestamp:number}>}
   */
  async getGeolocation(options = { enableHighAccuracy: false, timeout: 10000 }) {
    await this._ensurePermission("geolocation");
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      throw new Error("navigator.geolocation no disponible en este contexto.");
    }
    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(
        (pos) =>
          resolve({
            latitude: pos.coords.latitude,
            longitude: pos.coords.longitude,
            accuracy: pos.coords.accuracy,
            timestamp: pos.timestamp,
          }),
        (err) => reject(new Error(err.message)),
        options
      );
    });
  }

  // ===========================================================================
  // 13. PORTAPAPELES (navigator.clipboard)  [permisos opcionales]
  // ===========================================================================

  /**
   * Escribe texto en el portapapeles. Requiere "clipboardWrite" y un
   * documento enfocado (popup/options).
   * @param {string} text
   * @returns {Promise<void>}
   */
  async clipboardWrite(text) {
    await this._ensurePermission("clipboardWrite");
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      throw new Error("navigator.clipboard no disponible en este contexto.");
    }
    return navigator.clipboard.writeText(text);
  }

  /**
   * Lee texto del portapapeles. Requiere "clipboardRead".
   * @returns {Promise<string>}
   */
  async clipboardRead() {
    await this._ensurePermission("clipboardRead");
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      throw new Error("navigator.clipboard no disponible en este contexto.");
    }
    return navigator.clipboard.readText();
  }

  // ===========================================================================
  // 14. NOTIFICACIONES (chrome.notifications)
  // ===========================================================================

  /**
   * Muestra una notificacion del sistema.
   * @param {string} title
   * @param {string} message
   * @param {Partial<chrome.notifications.NotificationOptions>} [extra]
   * @returns {Promise<string>}  id de la notificacion.
   */
  async notify(title, message, extra = {}) {
    return chrome.notifications.create({
      type: "basic",
      iconUrl: chrome.runtime.getURL("icons/icon128.png"),
      title,
      message,
      ...extra,
    });
  }

  // ===========================================================================
  // 15. INACTIVIDAD (chrome.idle)
  // ===========================================================================

  /**
   * Estado de actividad del usuario.
   * @param {number} [detectionIntervalSeconds=60]
   * @returns {Promise<"active"|"idle"|"locked">}
   */
  async getIdleState(detectionIntervalSeconds = 60) {
    return CharlyAPI._fromCallback(
      chrome.idle.queryState.bind(chrome.idle),
      detectionIntervalSeconds
    );
  }

  // ===========================================================================
  // 16. MENSAJERIA (chrome.runtime / chrome.tabs)
  // ===========================================================================

  /**
   * Envia un mensaje al service worker (o a otras paginas de la extension).
   * @param {*} message
   * @returns {Promise<*>}  respuesta del receptor.
   */
  async sendRuntimeMessage(message) {
    return chrome.runtime.sendMessage(message);
  }

  /**
   * Envia un mensaje al content script de una pestana.
   * @param {number} tabId
   * @param {*} message
   * @returns {Promise<*>}
   */
  async sendTabMessage(tabId, message) {
    return chrome.tabs.sendMessage(tabId, message);
  }

  // ===========================================================================
  // 17. PESTANA ACTUAL — ACCESO PROFUNDO (chrome.scripting + chrome.tabs)
  // ===========================================================================

  /** HTML completo de la pagina activa. @returns {Promise<string>} */
  async getPageHTML() {
    return this.executeInActiveTab(() => document.documentElement.outerHTML);
  }

  /** Texto visible de la pagina activa. @returns {Promise<string>} */
  async getPageText() {
    return this.executeInActiveTab(() => document.body?.innerText || "");
  }

  /** Todos los enlaces (href + texto) de la pagina activa. @returns {Promise<Array<{href:string,text:string}>>} */
  async getPageLinks() {
    return this.executeInActiveTab(() =>
      Array.from(document.links).map((a) => ({
        href: a.href,
        text: (a.textContent || "").trim().slice(0, 120),
      }))
    );
  }

  /** Todas las imagenes (src + alt + dimensiones). @returns {Promise<Array>} */
  async getPageImages() {
    return this.executeInActiveTab(() =>
      Array.from(document.images).map((img) => ({
        src: img.currentSrc || img.src,
        alt: img.alt,
        width: img.naturalWidth,
        height: img.naturalHeight,
      }))
    );
  }

  /** Etiquetas meta de la pagina activa como objeto. @returns {Promise<Object<string,string>>} */
  async getPageMeta() {
    return this.executeInActiveTab(() =>
      Object.fromEntries(
        Array.from(document.querySelectorAll("meta")).map((m) => [
          m.getAttribute("name") || m.getAttribute("property") || m.getAttribute("http-equiv") || "",
          m.getAttribute("content") || "",
        ])
      )
    );
  }

  /**
   * Asigna un valor a un campo (input/textarea/select) y dispara eventos
   * input/change para frameworks reactivos.
   * @param {string} selector
   * @param {string} value
   * @returns {Promise<boolean>}
   */
  async setFieldValue(selector, value) {
    return this.executeInActiveTab(
      (sel, val) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        el.value = val;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      },
      [selector, value]
    );
  }

  /** Envia un formulario. @param {string} selector @returns {Promise<boolean>} */
  async submitForm(selector) {
    return this.executeInActiveTab(
      (sel) => {
        const form = document.querySelector(sel);
        if (!form) return false;
        form.submit();
        return true;
      },
      [selector]
    );
  }

  /**
   * Desplaza la pagina. Sin selector hace scroll al fondo.
   * @param {string|null} [selector=null]
   * @returns {Promise<boolean>}
   */
  async scrollPage(selector = null) {
    return this.executeInActiveTab(
      (sel) => {
        if (sel) {
          const el = document.querySelector(sel);
          if (!el) return false;
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          return true;
        }
        window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
        return true;
      },
      [selector]
    );
  }

  /**
   * Ejecuta una funcion en el MUNDO PRINCIPAL de la pagina (acceso a variables
   * y librerias del sitio, p.ej. window.jQuery). Maximo acceso al contexto JS.
   * @param {Function} func
   * @param {Array} [args=[]]
   * @returns {Promise<*>}
   */
  async executeInMainWorld(func, args = []) {
    return this.executeInActiveTab(func, args, "MAIN");
  }

  /** Idioma detectado de la pestana. @param {number} [tabId] @returns {Promise<string>} */
  async detectLanguage(tabId) {
    return chrome.tabs.detectLanguage(tabId);
  }

  /** Nivel de zoom de la pestana. @param {number} [tabId] @returns {Promise<number>} */
  async getZoom(tabId) {
    return chrome.tabs.getZoom(tabId);
  }

  /** Fija el zoom (1 = 100%). @param {number} factor @param {number} [tabId] @returns {Promise<void>} */
  async setZoom(factor, tabId) {
    return chrome.tabs.setZoom(tabId, factor);
  }

  // --- Content scripts dinamicos (inyeccion persistente bajo demanda) --------

  /**
   * Registra un content script dinamico (persiste hasta desregistrarlo).
   * @param {chrome.scripting.RegisteredContentScript} script
   * @returns {Promise<void>}
   */
  async registerContentScript(script) {
    return chrome.scripting.registerContentScripts([script]);
  }

  /** Lista los content scripts dinamicos registrados. @returns {Promise<object[]>} */
  async getRegisteredContentScripts() {
    return chrome.scripting.getRegisteredContentScripts();
  }

  /** Desregistra content scripts dinamicos. @param {string[]} [ids] @returns {Promise<void>} */
  async unregisterContentScripts(ids) {
    return chrome.scripting.unregisterContentScripts(ids ? { ids } : undefined);
  }

  // ===========================================================================
  // 18. PESTANAS ABIERTAS — CONTROL TOTAL
  // ===========================================================================

  /** Mueve pestanas a una posicion. @param {number|number[]} tabIds @param {object} props @returns {Promise<*>} */
  async moveTabs(tabIds, props) {
    return chrome.tabs.move(tabIds, props);
  }

  /** Fija/desfija una pestana. @param {number} tabId @param {boolean} pinned @returns {Promise<chrome.tabs.Tab>} */
  async pinTab(tabId, pinned = true) {
    return chrome.tabs.update(tabId, { pinned });
  }

  /** Silencia/activa el audio de una pestana. @param {number} tabId @param {boolean} muted @returns {Promise<chrome.tabs.Tab>} */
  async muteTab(tabId, muted = true) {
    return chrome.tabs.update(tabId, { muted });
  }

  /** Descarta una pestana de memoria (la "duerme"). @param {number} [tabId] @returns {Promise<chrome.tabs.Tab>} */
  async discardTab(tabId) {
    return chrome.tabs.discard(tabId);
  }

  /** Navega atras en el historial de la pestana. @param {number} [tabId] @returns {Promise<void>} */
  async goBack(tabId) {
    return chrome.tabs.goBack(tabId);
  }

  /** Navega adelante en el historial de la pestana. @param {number} [tabId] @returns {Promise<void>} */
  async goForward(tabId) {
    return chrome.tabs.goForward(tabId);
  }

  /** Busca pestanas cuya URL coincida con un patron. @param {string} urlPattern @returns {Promise<chrome.tabs.Tab[]>} */
  async findTabsByUrl(urlPattern) {
    return chrome.tabs.query({ url: urlPattern });
  }

  // --- Grupos de pestanas (chrome.tabGroups) --------------------------------

  /**
   * Agrupa pestanas. @param {number[]} tabIds
   * @param {object} [createProperties]  p.ej. { windowId }
   * @returns {Promise<number>}  groupId.
   */
  async groupTabs(tabIds, createProperties) {
    return chrome.tabs.group({ tabIds, createProperties });
  }

  /** Desagrupa pestanas. @param {number|number[]} tabIds @returns {Promise<void>} */
  async ungroupTabs(tabIds) {
    return chrome.tabs.ungroup(tabIds);
  }

  /** Lista grupos de pestanas. @param {chrome.tabGroups.QueryInfo} [query={}] @returns {Promise<object[]>} */
  async getTabGroups(query = {}) {
    return chrome.tabGroups.query(query);
  }

  /** Actualiza un grupo (titulo, color, colapsado). @param {number} groupId @param {object} props @returns {Promise<object>} */
  async updateTabGroup(groupId, props) {
    return chrome.tabGroups.update(groupId, props);
  }

  // --- Sesiones (chrome.sessions) -------------------------------------------

  /** Pestanas/ventanas cerradas recientemente. @param {object} [filter] @returns {Promise<object[]>} */
  async getRecentlyClosed(filter) {
    return chrome.sessions.getRecentlyClosed(filter);
  }

  /** Restaura la ultima sesion cerrada (o una concreta). @param {string} [sessionId] @returns {Promise<object>} */
  async restoreSession(sessionId) {
    return chrome.sessions.restore(sessionId);
  }

  // ===========================================================================
  // 19. NAVEGADOR — CAPACIDADES AMPLIAS
  // ===========================================================================

  /** Sitios mas visitados. [opcional: "topSites"] @returns {Promise<object[]>} */
  async getTopSites() {
    await this._ensurePermission("topSites");
    return chrome.topSites.get();
  }

  /** Frames de una pestana (webNavigation). [opcional: "webNavigation"] @param {number} tabId @returns {Promise<object[]>} */
  async getAllFrames(tabId) {
    await this._ensurePermission("webNavigation");
    return chrome.webNavigation.getAllFrames({ tabId });
  }

  // --- Gestion de extensiones (chrome.management) [opcional: "management"] ---

  /** Lista extensiones/apps instaladas. @returns {Promise<object[]>} */
  async getInstalledExtensions() {
    await this._ensurePermission("management");
    return chrome.management.getAll();
  }

  /** Activa/desactiva otra extension. @param {string} id @param {boolean} enabled @returns {Promise<void>} */
  async setExtensionEnabled(id, enabled) {
    await this._ensurePermission("management");
    return chrome.management.setEnabled(id, enabled);
  }

  // --- Limpieza de datos (chrome.browsingData) [opcional: "browsingData"] ----

  /**
   * Borra datos de navegacion.
   * @param {chrome.browsingData.DataTypeSet} dataToRemove  p.ej. { cache:true, cookies:true }
   * @param {object} [options={ since:0 }]
   * @returns {Promise<void>}
   */
  async clearBrowsingData(dataToRemove, options = { since: 0 }) {
    await this._ensurePermission("browsingData");
    return chrome.browsingData.remove(options, dataToRemove);
  }

  /** Vacia solo la cache. @returns {Promise<void>} */
  async clearCache() {
    await this._ensurePermission("browsingData");
    return chrome.browsingData.removeCache({ since: 0 });
  }

  // --- Menus contextuales (chrome.contextMenus) ------------------------------

  /** Crea un item de menu contextual. @param {chrome.contextMenus.CreateProperties} props @returns {Promise<number|string>} */
  async createContextMenu(props) {
    return new Promise((resolve) => {
      const id = chrome.contextMenus.create(props, () => resolve(id));
    });
  }

  /** Elimina un item de menu contextual. @param {number|string} id @returns {Promise<void>} */
  async removeContextMenu(id) {
    return chrome.contextMenus.remove(id);
  }

  /** Elimina todos los items de menu contextual. @returns {Promise<void>} */
  async clearContextMenus() {
    return chrome.contextMenus.removeAll();
  }

  // --- Comandos / atajos (chrome.commands) -----------------------------------

  /** Atajos de teclado declarados. @returns {Promise<chrome.commands.Command[]>} */
  async getCommands() {
    return chrome.commands.getAll();
  }

  // --- Alarmas / tareas programadas (chrome.alarms) --------------------------

  /** Crea una alarma. @param {string} name @param {chrome.alarms.AlarmCreateInfo} info @returns {Promise<void>} */
  async createAlarm(name, info) {
    return chrome.alarms.create(name, info);
  }

  /** Lista alarmas activas. @returns {Promise<chrome.alarms.Alarm[]>} */
  async getAlarms() {
    return chrome.alarms.getAll();
  }

  /** Cancela una alarma. @param {string} name @returns {Promise<boolean>} */
  async clearAlarm(name) {
    return chrome.alarms.clear(name);
  }

  // --- Energia (chrome.power) ------------------------------------------------

  /** Impide que el sistema/pantalla se suspenda. @param {"system"|"display"} [level="system"] @returns {void} */
  keepAwake(level = "system") {
    chrome.power.requestKeepAwake(level);
  }

  /** Libera el bloqueo de suspension. @returns {void} */
  releaseKeepAwake() {
    chrome.power.releaseKeepAwake();
  }

  // --- Panel lateral (chrome.sidePanel) --------------------------------------

  /**
   * Abre el panel lateral en la ventana actual. Debe invocarse desde un gesto
   * del usuario. Requiere el permiso "sidePanel" y la clave "side_panel".
   * @returns {Promise<void>}
   */
  async openSidePanel() {
    const win = await chrome.windows.getCurrent();
    return chrome.sidePanel.open({ windowId: win.id });
  }

  // ===========================================================================
  // 20. PC / SISTEMA — PERFIL PROFUNDO
  // ===========================================================================

  /**
   * Perfil del dispositivo combinando navigator + chrome.runtime.
   * @returns {Promise<object>}
   */
  async getDeviceProfile() {
    const platform = await this.getPlatformInfo();
    const profile = {
      platform,
      cores: navigator.hardwareConcurrency,
      deviceMemoryGB: navigator.deviceMemory ?? null,
      language: navigator.language,
      languages: this.getLanguages(),
      online: navigator.onLine,
      userAgent: navigator.userAgent,
      network: this.getNetworkInfo(),
    };
    // Datos de alta entropia del User-Agent (si estan disponibles).
    if (navigator.userAgentData?.getHighEntropyValues) {
      try {
        profile.uaData = await navigator.userAgentData.getHighEntropyValues([
          "platform",
          "platformVersion",
          "architecture",
          "model",
          "uaFullVersion",
        ]);
      } catch {
        /* opcional */
      }
    }
    return profile;
  }

  /** Estimacion de cuota/uso de almacenamiento del origen. @returns {Promise<{quota:number,usage:number}>} */
  async getStorageEstimate() {
    if (!navigator.storage?.estimate) {
      throw new Error("navigator.storage.estimate no disponible en este contexto.");
    }
    return navigator.storage.estimate();
  }

  /**
   * Enumera dispositivos de medios (camaras, microfonos, salidas). Las
   * etiquetas solo aparecen tras conceder acceso a la camara/microfono.
   * @returns {Promise<MediaDeviceInfo[]>}
   */
  async enumerateMediaDevices() {
    if (!navigator.mediaDevices?.enumerateDevices) {
      throw new Error("navigator.mediaDevices no disponible en este contexto.");
    }
    return navigator.mediaDevices.enumerateDevices();
  }

  /**
   * Solicita capturar pantalla/ventana del equipo (desktopCapture). Devuelve
   * un streamId para usar con getUserMedia. Requiere gesto del usuario y el
   * permiso "desktopCapture". [opcional]
   * @param {string[]} [sources=["screen","window","tab"]]
   * @returns {Promise<string>} streamId
   */
  async chooseDesktopMedia(sources = ["screen", "window", "tab"]) {
    await this._ensurePermission("desktopCapture");
    return new Promise((resolve, reject) => {
      chrome.desktopCapture.chooseDesktopMedia(sources, (streamId) => {
        if (!streamId) reject(new Error("El usuario cancelo la captura."));
        else resolve(streamId);
      });
    });
  }

  // ===========================================================================
  // 21. DEPURADOR / CDP — MAXIMO ACCESO A LA PESTANA
  //     Requiere el permiso "debugger" en el manifest (no opcional en Chrome).
  //     Muestra una barra de aviso mientras esta adjunto. Uso avanzado.
  // ===========================================================================

  /** Verifica que chrome.debugger este disponible (permiso "debugger"). */
  _requireDebugger() {
    if (!chrome.debugger) {
      throw new Error(
        'API debugger no disponible. Anada "debugger" al array "permissions" ' +
          "del manifest.json (no admite ser opcional en Chrome) y recargue la extension."
      );
    }
  }

  /** Adjunta el depurador a una pestana. @param {number} tabId @param {string} [version="1.3"] @returns {Promise<void>} */
  async attachDebugger(tabId, version = "1.3") {
    this._requireDebugger();
    return chrome.debugger.attach({ tabId }, version);
  }

  /**
   * Envia un comando del Chrome DevTools Protocol a la pestana adjunta.
   * @param {number} tabId
   * @param {string} method   p.ej. "Page.captureScreenshot", "Network.enable"
   * @param {object} [params={}]
   * @returns {Promise<object>}
   */
  async sendDebuggerCommand(tabId, method, params = {}) {
    this._requireDebugger();
    return chrome.debugger.sendCommand({ tabId }, method, params);
  }

  /** Desadjunta el depurador. @param {number} tabId @returns {Promise<void>} */
  async detachDebugger(tabId) {
    this._requireDebugger();
    return chrome.debugger.detach({ tabId });
  }

  /**
   * Captura un PNG de la PAGINA COMPLETA (no solo lo visible) via CDP.
   * Adjunta, captura y desadjunta automaticamente.
   * @param {number} [tabId]  por defecto, la pestana activa.
   * @returns {Promise<string>}  dataURL (image/png).
   */
  async captureFullPageScreenshot(tabId) {
    this._requireDebugger();
    const id = tabId ?? (await this.getActiveTab()).id;
    await this.attachDebugger(id);
    try {
      const { data } = await this.sendDebuggerCommand(id, "Page.captureScreenshot", {
        captureBeyondViewport: true,
        fromSurface: true,
      });
      return `data:image/png;base64,${data}`;
    } finally {
      await this.detachDebugger(id).catch(() => {});
    }
  }

  // ===========================================================================
  // 22. RED DECLARATIVA / PROXY / TTS  — capacidades que requieren que el
  //     desarrollador anada el permiso al manifest (no opcionales en Chrome).
  // ===========================================================================

  /**
   * Actualiza reglas dinamicas de declarativeNetRequest (bloquear/redirigir/
   * modificar cabeceras). Requiere "declarativeNetRequest" en "permissions".
   * @param {object} opts  { addRules?:object[], removeRuleIds?:number[] }
   * @returns {Promise<void>}
   */
  async updateNetRequestRules(opts) {
    if (!chrome.declarativeNetRequest) {
      throw new Error(
        'API declarativeNetRequest no disponible. Anada "declarativeNetRequest" ' +
          'al array "permissions" del manifest.json (no admite ser opcional).'
      );
    }
    return chrome.declarativeNetRequest.updateDynamicRules(opts);
  }

  /**
   * Configura el proxy del navegador. Requiere "proxy" en "permissions".
   * @param {chrome.types.ChromeSettingSetDetails} config
   * @returns {Promise<void>}
   */
  async setProxy(config) {
    if (!chrome.proxy) {
      throw new Error(
        'API proxy no disponible. Anada "proxy" al array "permissions" del manifest.json.'
      );
    }
    return chrome.proxy.settings.set(config);
  }

  /** Restablece el proxy a su valor por defecto. @returns {Promise<void>} */
  async clearProxy() {
    if (!chrome.proxy) {
      throw new Error('API proxy no disponible. Anada "proxy" al manifest.json.');
    }
    return chrome.proxy.settings.clear({});
  }

  /**
   * Lee texto en voz alta (text-to-speech). Requiere "tts" en "permissions".
   * @param {string} text
   * @param {chrome.tts.TtsOptions} [options]
   * @returns {void}
   */
  speak(text, options = {}) {
    if (!chrome.tts) {
      throw new Error('API tts no disponible. Anada "tts" al array "permissions" del manifest.json.');
    }
    chrome.tts.speak(text, options);
  }

  /** Detiene la sintesis de voz en curso. @returns {void} */
  stopSpeaking() {
    if (chrome.tts) chrome.tts.stop();
  }
}
