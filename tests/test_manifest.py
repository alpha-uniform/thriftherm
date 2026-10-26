"""The manifest names every integration whose code Thriftherm imports, as hassfest demands."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from homeassistant.const import Platform

PACKAGE = Path(__file__).parent.parent / "custom_components" / "thriftherm"


def _imported_components() -> dict[str, set[str]]:
    """Every `homeassistant.components.<domain>` imported anywhere in the integration, with the files that do it."""
    found: dict[str, set[str]] = {}
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                parts = name.split(".")
                if parts[:2] == ["homeassistant", "components"] and len(parts) > 2:
                    found.setdefault(parts[2], set()).add(path.name)
    return found


def test_every_integration_whose_code_is_imported_is_named_in_the_manifest():
    """Seen 2026-10-26: ZHA's helper was imported for issue #2, and hassfest failed until the manifest named zha."""
    manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    named = set(manifest.get("dependencies", [])) | set(manifest.get("after_dependencies", []))
    platforms = {platform.value for platform in Platform}  # entity platforms need no entry
    missing = {domain: files for domain, files in _imported_components().items() if domain not in named | platforms}
    assert missing == {}
