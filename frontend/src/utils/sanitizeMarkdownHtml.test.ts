import { describe, expect, it } from "vitest";
import { sanitizeMarkdownHtml } from "./sanitizeMarkdownHtml";

describe("sanitizeMarkdownHtml", () => {
  it("drops executable markup and unsafe attributes while preserving safe formatting", () => {
    const result = sanitizeMarkdownHtml(`
      <p onclick="window.attacked = true">正文<strong>重点</strong></p>
      <script>window.attacked = true</script>
      <img src=x onerror="window.attacked = true">
      <a href="javascript:alert(1)" target="_blank">危险链接</a>
      <a href="https://example.com/path" onclick="alert(1)">安全链接</a>
    `);

    const template = document.createElement("template");
    template.innerHTML = result;

    expect(template.content.querySelector("script, img")).toBeNull();
    expect(template.content.querySelector("[onclick], [onerror], [target]")).toBeNull();
    expect(template.content.querySelector("a")?.hasAttribute("href")).toBe(false);
    const safeLink = template.content.querySelectorAll("a")[1];
    expect(safeLink?.getAttribute("href")).toBe("https://example.com/path");
    expect(safeLink?.getAttribute("rel")).toBe("noopener noreferrer nofollow");
    expect(template.content.querySelector("strong")?.textContent).toBe("重点");
  });
});
