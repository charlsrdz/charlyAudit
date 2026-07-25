/**
 * markdown.js — Markdown seguro (CharlyPlugin · IA)
 * =================================================
 * Renderiza un subconjunto de Markdown a HTML SEGURO. Estrategia "escape-first":
 * primero se escapa TODO el HTML del modelo y luego se aplican las
 * transformaciones de Markdown sobre el texto ya escapado. Asi ninguna etiqueta
 * proveniente del modelo sobrevive: no hay superficie de XSS.
 *
 * Nunca se ejecuta nada de la respuesta del modelo (sin <script>, sin acciones).
 */

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/** Solo se permiten enlaces http(s) y mailto; cualquier otro esquema se ignora. */
function safeHref(escapedUrl) {
  return /^(https?:|mailto:)/i.test(escapedUrl) ? escapedUrl : null;
}

function inline(text) {
  // 1) Protege los spans de codigo en linea: su contenido NO se vuelve a
  //    transformar (asi `a_b` o `a*b` dentro de codigo no se vuelven cursiva).
  const codes = [];
  let t = text.replace(/`([^`]+)`/g, (_m, code) => {
    const i = codes.length;
    codes.push(`<code>${code}</code>`);
    return `\u0002${i}\u0002`;
  });
  // 2) Negrita, luego cursiva. La cursiva exige limites de palabra para no
  //    romper identificadores (snake_case) ni multiplicaciones (a * b).
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[\s(>])\*(\S[^*]*?)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  t = t.replace(/(^|[\s(>])_(\S[^_]*?)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  // 3) Enlaces [texto](url) con esquema validado.
  t = t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
    const href = safeHref(url);
    return href
      ? `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
      : label;
  });
  // 4) Restaura los spans de codigo protegidos.
  t = t.replace(/\u0002(\d+)\u0002/g, (_m, i) => codes[Number(i)]);
  return t;
}

/**
 * Renderiza un bloque <details><summary>…</summary>…</details> (el "flujo de
 * proceso" que emite el modelo) como un desplegable estilizado y SEGURO: el
 * contenido se parsea a pasos (tiempo + texto), todo escapado.
 */
function renderProcess(summaryRaw, bodyRaw) {
  const summary = escapeHtml(String(summaryRaw).replace(/<[^>]*>/g, "").trim());
  const lines = String(bodyRaw).split(/\r?\n/);
  const steps = [];
  for (const raw of lines) {
    const item = raw.match(/^\s*[-*]\s+(.*)$/);
    if (!item) continue;
    let rest = item[1];
    let time = "";
    const tm = rest.match(/^`([^`]+)`\s*(.*)$/);
    if (tm) {
      time = escapeHtml(tm[1]);
      rest = tm[2];
    }
    steps.push(
      `<li>${time ? `<span class="proc__t">${time}</span>` : ""}<span class="proc__s">${escapeHtml(rest)}</span></li>`
    );
  }
  const body = steps.length
    ? `<ol class="proc__steps">${steps.join("")}</ol>`
    : `<div class="proc__body">${escapeHtml(bodyRaw.trim())}</div>`;
  return `<details class="proc"><summary>${summary}</summary>${body}</details>`;
}

/**
 * Convierte Markdown (texto del modelo) en HTML seguro.
 * @param {string} md
 * @returns {string}
 */
export function renderMarkdown(md) {
  const source = String(md == null ? "" : md);

  const blocks = [];
  // 0) Extrae bloques de "proceso" <details><summary>…</summary>…</details>.
  let work = source.replace(
    /<details>\s*<summary>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/gi,
    (_m, summary, body) => {
      const idx = blocks.length;
      blocks.push(renderProcess(summary, body));
      return `\n\u0001${idx}\u0001\n`;
    }
  );

  // 1) Extrae bloques de codigo cercados ```...``` y los reserva escapados.
  work = work.replace(/```(\w+)?\n?([\s\S]*?)```/g, (_m, lang, code) => {
    const idx = blocks.length;
    const cls = lang ? ` class="lang-${escapeHtml(lang)}"` : "";
    blocks.push(`<pre><code${cls}>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`);
    return `\n\u0001${idx}\u0001\n`;
  });
  // 1b) Bloque de codigo ABIERTO sin cierre (respuesta truncada): lo cerramos
  //     hasta el final para no dejar ``` sueltos rompiendo el globo.
  work = work.replace(/```(\w+)?\n?([\s\S]*)$/, (_m, lang, code) => {
    const idx = blocks.length;
    const cls = lang ? ` class="lang-${escapeHtml(lang)}"` : "";
    blocks.push(`<pre><code${cls}>${escapeHtml(code)}</code></pre>`);
    return `\n\u0001${idx}\u0001\n`;
  });

  // 2) Escapa el resto del texto.
  work = escapeHtml(work);

  // 3) Procesa por lineas (encabezados, listas, citas, parrafos).
  const lines = work.split(/\r?\n/);
  const out = [];
  let listType = null; // "ul" | "ol"
  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`);
      listType = null;
    }
  };

  for (const raw of lines) {
    const line = raw;
    const placeholder = line.match(/^\u0001(\d+)\u0001$/);
    if (placeholder) {
      closeList();
      out.push(blocks[Number(placeholder[1])]);
      continue;
    }
    if (!line.trim()) {
      closeList();
      continue;
    }
    let m;
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      closeList();
      const lvl = m[1].length;
      out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (listType !== "ul") {
        closeList();
        out.push("<ul>");
        listType = "ul";
      }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
      if (listType !== "ol") {
        closeList();
        out.push("<ol>");
        listType = "ol";
      }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*&gt;\s?(.*)$/))) {
      closeList();
      out.push(`<blockquote>${inline(m[1])}</blockquote>`);
    } else {
      closeList();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();

  return out.join("\n");
}

export const Markdown = { escapeHtml, renderMarkdown };
