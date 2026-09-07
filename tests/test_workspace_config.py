from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.workspace_config import load_config, resolve


class WorkspaceConfigTests(unittest.TestCase):
    def test_relative_paths_resolve_from_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repository"
            config_dir = repository / "config"
            config_dir.mkdir(parents=True)
            config = config_dir / "workspace.json"
            paths = {key: key for key in ("python", "pip", "node_root", "tmp", "logs", "evaluation_results", "course_outputs", "archive", "media", "course_workspace", "textbook_images")}
            config.write_text(json.dumps({"version": 1, "workspace_root": "../runtime-workspace", "paths": paths}), encoding="utf-8")
            resolved = resolve(load_config(repository, config))
            self.assertEqual(resolved["DADA_WORKSPACE_ROOT"], str(repository / "runtime-workspace"))
            self.assertEqual(resolved["DADA_WORKSPACE_ARCHIVE"], str(repository / "runtime-workspace" / "archive"))

    def test_missing_local_config_uses_versioned_template(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        resolved = resolve(load_config(repository, repository / "config" / "does-not-exist.json"))
        self.assertTrue(resolved["DADA_WORKSPACE_ROOT"].endswith(".dada-workspace"))

    def test_missing_required_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            config = repository / "workspace.json"
            config.write_text(json.dumps({"version": 1, "workspace_root": ".", "paths": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(repository, config)


if __name__ == "__main__":
    unittest.main()
