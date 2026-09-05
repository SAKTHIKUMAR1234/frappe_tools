"""Read literal adapter hooks for the frontend build, without booting a site.

Builds include bench apps; each site's server exposes only its installed adapters.
Both package hooks and the older package.plugin hooks resolve to ui/index.js.
"""

import ast
import json
from pathlib import Path
import sys


def discover(bench):
    entries = {}
    apps = (bench / "sites" / "apps.txt").read_text().splitlines()
    for app in apps:
        app_dir = bench / "apps" / app
        hooks = app_dir / app / "hooks.py"
        if not hooks.is_file():
            continue
        for node in ast.parse(hooks.read_text()).body:
            if not isinstance(node, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == "doc_extraction_plugins"
                for target in node.targets
            ):
                continue
            try:
                modules = ast.literal_eval(node.value)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{hooks}: doc_extraction_plugins must be a literal list for UI discovery") from exc
            if not isinstance(modules, (list, tuple)) or not all(isinstance(module, str) for module in modules):
                raise ValueError(f"{hooks}: doc_extraction_plugins must contain module paths")
            for module in modules:
                package = module.removesuffix(".plugin")
                if package.split(".")[0] != app:
                    raise ValueError(f"{hooks}: adapter {module} must belong to its registering app")
                entry = app_dir.joinpath(*package.split("."), "ui", "index.js")
                if entry.is_file():
                    entries[package] = str(entry.resolve())
    return entries


if __name__ == "__main__":
    print(json.dumps(discover(Path(sys.argv[1]).resolve())))
