import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const virtualId = "virtual:document-adapters";

export function documentAdapters() {
  return {
    name: "frappe-document-adapters",
    resolveId(id) {
      if (id === virtualId) return `\0${id}`;
      if (id === "@frappe-tools/adapter-ui")
        return path.join(root, "src/adapters/sdk.js");
    },
    load(id) {
      if (id !== `\0${virtualId}`) return;
      const entries = JSON.parse(
        execFileSync(
          "python3",
          [
            path.join(root, "../scripts/discover_adapter_ui.py"),
            path.resolve(root, "../../.."),
          ],
          { encoding: "utf8" },
        ),
      );
      return `export default {${Object.entries(entries)
        .map(
          ([key, entry]) =>
            `${JSON.stringify(key)}: () => import(${JSON.stringify(entry)})`,
        )
        .join(",")}};`;
    },
  };
}
