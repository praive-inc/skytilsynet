#!/usr/bin/env python3
"""Guard the deploy contract: deploy-local.sh must ship every top-level package
the FOI backend imports from the repo root (issue #110).

server/foi_intake.py adds ROOT (the repo root) to sys.path and imports the
top-level `shared` package. The compose deploy mounts the rsync'd app dir as
`/app` and runs `python3 server/foi_intake.py`, so ROOT resolves to `/app` at
runtime — any repo-root package the server imports must be rsync'd into the app
dir too, or the service crashes at import time on restart. This test derives that
set from the actual imports and asserts the deploy script syncs each one, so the
next new shared module can't silently drift out of the deploy again."""

import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEPLOY_SCRIPT = os.path.join(HERE, "deploy-local.sh")
SERVER_DIR = os.path.join(ROOT, "server")


def _repo_root_packages():
    """Every directory at the repo root that is an importable package (has
    __init__.py) — the set a repo-root sys.path import can resolve."""
    return {
        name for name in os.listdir(ROOT)
        if os.path.isfile(os.path.join(ROOT, name, "__init__.py"))
    }


def _server_root_imports():
    """Top-level package names the server code imports that resolve to a repo-root
    package (e.g. `from shared.csv_safe import csv_safe` -> {'shared'})."""
    packages = _repo_root_packages()
    imported = set()
    for fname in os.listdir(SERVER_DIR):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(SERVER_DIR, fname), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in packages:
                    imported.add(top)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in packages:
                        imported.add(top)
    return imported


def _rsynced_dirs():
    """Repo-root directories the deploy script rsyncs into the mounted app dir,
    parsed from the `rsync ... "$ROOT/<dir>/" ...` lines."""
    with open(DEPLOY_SCRIPT, encoding="utf-8") as fh:
        text = fh.read()
    return set(re.findall(r'rsync\b[^\n]*\$ROOT/([^/\s\'"]+)/', text))


class DeployContractTest(unittest.TestCase):
    def test_server_imports_shared_package(self):
        """Sanity: the server really does import the top-level shared package, so
        this guard is exercising a live dependency (not a no-op)."""
        self.assertIn("shared", _server_root_imports())

    def test_deploy_syncs_every_repo_root_package_the_server_imports(self):
        """The heart of #110: each repo-root package the server imports must be in
        the deploy's rsync set, or the compose service crashes at import on
        restart."""
        missing = _server_root_imports() - _rsynced_dirs()
        self.assertEqual(
            set(), missing,
            "deploy-local.sh does not rsync repo-root package(s) the FOI backend "
            "imports: %s — the service will crash at import time on restart" % sorted(missing))


if __name__ == "__main__":
    unittest.main()
