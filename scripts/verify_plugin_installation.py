#!/usr/bin/env python3
"""Verify a Mission Control plugin install without changing host state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn


class VerificationError(Exception):
    """A deterministic installation contract failure."""


def fail(message: str) -> NoReturn:
    raise VerificationError(message)


def verify(plugin_dir: Path, source_plugins_dir: Path) -> None:
    if not plugin_dir.is_dir():
        fail(f"plugin directory does not exist: {plugin_dir}")

    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.is_file():
        fail(f"missing manifest.json in plugin directory: {plugin_dir}")
    try:
        manifest: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"invalid manifest.json: {error}")

    if not isinstance(manifest, dict):
        fail("manifest.json must contain a JSON object")
    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or not plugin_id or Path(plugin_id).name != plugin_id:
        fail("manifest.json must contain a safe, non-empty string id")
    if plugin_id != plugin_dir.name:
        fail(f"manifest id {plugin_id!r} does not match directory name {plugin_dir.name!r}")

    ui_dir = plugin_dir / "ui"
    if not ui_dir.is_dir():
        fail(f"plugin has no ui/ directory: {plugin_dir}")
    for required in ("route.ts", "manifest.ts"):
        if not (ui_dir / required).is_file():
            fail(f"plugin UI is missing required file: ui/{required}")

    if not source_plugins_dir.is_dir():
        fail(f"Mission Control source plugin directory does not exist: {source_plugins_dir}")
    entries = sorted(source_plugins_dir.iterdir(), key=lambda path: path.name)
    unexpected = [entry.name for entry in entries if entry.name != plugin_id]
    if unexpected:
        fail(f"plugin discovery found unexpected entries: {', '.join(unexpected)}")

    link_path = source_plugins_dir / plugin_id
    if not link_path.is_symlink():
        fail(f"plugin UI link is missing: {link_path}")
    if not link_path.exists():
        fail(f"plugin UI link is dangling: {link_path}")
    if link_path.resolve() != ui_dir.resolve():
        fail(f"plugin UI link must point to {ui_dir}, got {link_path.resolve()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-dir", type=Path, required=True)
    parser.add_argument("--source-plugins-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args.plugin_dir.absolute(), args.source_plugins_dir.absolute())
    except VerificationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"{args.plugin_dir.name}: installation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
