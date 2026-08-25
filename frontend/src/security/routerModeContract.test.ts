import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

function productionSources(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return productionSources(fullPath);
    if (!/\.(ts|tsx)$/.test(entry.name) || entry.name.includes(".test.")) return [];
    return [readFileSync(fullPath, "utf8")];
  });
}

describe("React Router security mode contract", () => {
  it("stays in declarative SPA mode and does not mount vulnerable RSC/data APIs", () => {
    const sourceRoot = path.resolve(process.cwd(), "src");
    const mainSource = readFileSync(path.join(sourceRoot, "main.tsx"), "utf8");
    const allSource = productionSources(sourceRoot).join("\n");

    expect(mainSource).toContain("<BrowserRouter>");
    for (const forbidden of [
      "createBrowserRouter",
      "createStaticRouter",
      "RouterProvider",
      "unstable_RSC",
      "react-server",
    ]) {
      expect(allSource).not.toContain(forbidden);
    }
  });
});
