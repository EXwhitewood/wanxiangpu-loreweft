const ALLOWED_TAGS = new Set([
  "A", "BLOCKQUOTE", "BR", "CODE", "DEL", "EM", "H1", "H2", "H3",
  "H4", "H5", "H6", "HR", "LI", "OL", "P", "PRE", "STRONG", "TABLE",
  "TBODY", "TD", "TH", "THEAD", "TR", "UL",
]);

const DROP_WITH_CONTENT = new Set([
  "SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "SVG", "MATH", "FORM",
  "INPUT", "BUTTON", "TEXTAREA", "SELECT", "IMG", "VIDEO", "AUDIO",
]);

function safeHref(value: string): string | null {
  const normalized = value.trim().replace(/[\u0000-\u001f\u007f]/g, "");
  const lower = normalized.toLowerCase();
  if (
    normalized.startsWith("#")
    || (normalized.startsWith("/") && !normalized.startsWith("//"))
    || lower.startsWith("https://")
    || lower.startsWith("http://")
    || lower.startsWith("mailto:")
  ) {
    return normalized;
  }
  return null;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Strict allow-list sanitizer for model/import supplied Markdown HTML. */
export function sanitizeMarkdownHtml(html: string): string {
  if (typeof document === "undefined") return escapeHtml(html);
  const template = document.createElement("template");
  template.innerHTML = html;
  const elements = Array.from(template.content.querySelectorAll("*"));
  for (const element of elements) {
    if (DROP_WITH_CONTENT.has(element.tagName)) {
      element.remove();
      continue;
    }
    if (!ALLOWED_TAGS.has(element.tagName)) {
      element.replaceWith(document.createTextNode(element.textContent || ""));
      continue;
    }
    const originalHref = element.tagName === "A" ? element.getAttribute("href") : null;
    for (const attribute of Array.from(element.attributes)) {
      element.removeAttribute(attribute.name);
    }
    if (element.tagName === "A") {
      const href = originalHref ? safeHref(originalHref) : null;
      if (href) {
        element.setAttribute("href", href);
        element.setAttribute("rel", "noopener noreferrer nofollow");
      }
    }
  }
  return template.innerHTML;
}
