"""Verify offline packaging keeps model contents and native-library links."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "parser_build", ROOT / "scripts/build-document-parser.py"
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ParserPackagingTests(unittest.TestCase):
    def test_sidecar_preflight_rejects_a_parser_built_with_a_different_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "scripts/build-backend-sidecar.mjs"
            script.parent.mkdir()
            shutil.copyfile(ROOT / "scripts" / script.name, script)
            # Exercise the release preflight with a cached helper and no external build.
            script.with_name("build-slide-renderer.mjs").write_text("", encoding="utf-8")
            lock = root / "tools/document-parser/uv.lock"
            bundle = lock.parent / ".build/bundle"
            bundle.mkdir(parents=True)
            lock.write_text("version = 1\n", encoding="utf-8")
            sources = (
                "apps/backend/gunther/document_parser.py",
                "apps/backend/gunther/document_structure.py",
                "apps/backend/gunther/pdf_native.py",
                "apps/backend/gunther/pdf_glyphs.py",
                "tools/document-parser/helper.py",
                "scripts/build-document-parser.py",
            )
            source_hashes = {}
            for name in sources:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
                source_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            (bundle / "manifest.json").write_text(json.dumps({
                "lockHash": hashlib.sha256(lock.read_bytes()).hexdigest(),
                "sourceHashes": source_hashes,
            }), encoding="utf-8")
            build = root / "apps/backend/.pyinstaller"
            output = "GuntherBackend.app" if sys.platform == "darwin" else "gunther-backend"
            (build / "dist" / output).mkdir(parents=True)
            (build / "build-variant.json").write_text(
                '{"withParser":true,"withSlideBrowser":false}', encoding="utf-8"
            )

            def preflight():
                return subprocess.run(
                    ["node", str(script), "--without-slide-browser"],
                    env={**os.environ, "APPLE_SIGNING_IDENTITY": ""},
                    check=False, capture_output=True, text=True, timeout=15,
                )

            current = preflight()
            self.assertEqual(current.returncode, 0, current.stderr)
            self.assertIn("Gunther backend helper is current", current.stdout)

            # Preserve the timestamp to exclude the later build-cache freshness check.
            stat = lock.stat()
            lock.write_text("version = 2\n", encoding="utf-8")
            os.utime(lock, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            stale = preflight()
            self.assertEqual(stale.returncode, 1, stale.stderr)
            self.assertIn("The parser runtime is stale", stale.stderr)

    def test_copy_retains_relative_library_links_and_replaces_stale_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "source", root / "runtime"
            library = source / "_internal" / "torch" / "lib" / "engine.dylib"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"compiled native library")
            alias = source / "_internal" / "engine.dylib"
            try:
                alias.symlink_to("torch/lib/engine.dylib")
            except OSError:
                self.skipTest("Creating symlinks is unavailable on this host")
            destination.mkdir()
            (destination / "stale").write_text("old runtime")
            builder.copy_runtime(source, destination)
            copied_alias = destination / "_internal" / "engine.dylib"
            self.assertTrue(copied_alias.is_symlink())
            self.assertEqual(copied_alias.read_bytes(), library.read_bytes())
            self.assertFalse((destination / "stale").exists())
            stored = [
                path
                for path in destination.rglob("*")
                if path.is_file() and not path.is_symlink()
            ]
            self.assertEqual(len(stored), 1)

    def test_trimming_preserves_active_weights_configs_and_ocr_vocabulary(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory)
            active = {
                "docling-project--docling-layout-heron/model.safetensors": b"layout weights",
                "docling-project--docling-layout-heron/config.json": b"layout configuration",
                "docling-project--docling-models/model_artifacts/tableformer/accurate/tableformer_accurate.safetensors": b"accurate table weights",
                "docling-project--docling-models/model_artifacts/tableformer/accurate/tm_config.json": b"table configuration",
                "RapidOcr/recognition.onnx": b"OCR weights",
                "RapidOcr/vocabulary.txt": b"all supported characters",
            }
            spare = (
                "docling-project--docling-layout-heron-onnx/model.onnx",
                "docling-project--docling-models/model_artifacts/tableformer/fast/tableformer_fast.safetensors",
                "RapidOcr/recognition.pth",
            )
            for relative, data in {
                **active,
                **dict.fromkeys(spare, b"unused alternative"),
            }.items():
                path = models / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            builder.trim_model_variants(models)
            builder.trim_model_variants(models)
            self.assertEqual(
                {
                    str(path.relative_to(models)): path.read_bytes()
                    for path in models.rglob("*")
                    if path.is_file()
                },
                active,
            )

    def test_missing_active_models_fail_before_removing_alternatives(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory)
            spare = models / "docling-project--docling-layout-heron-onnx" / "model.onnx"
            spare.parent.mkdir()
            spare.write_bytes(b"existing model")
            with self.assertRaises(ValueError):
                builder.trim_model_variants(models)
            self.assertEqual(spare.read_bytes(), b"existing model")

    def test_pinned_pipeline_keeps_the_previous_models_and_settings(self):
        from docling.datamodel.pipeline_options import (
            LayoutObjectDetectionOptions,
            PdfPipelineOptions,
            TableFormerMode,
            TableStructureOptions,
        )

        defaults = PdfPipelineOptions()
        layout = LayoutObjectDetectionOptions.from_preset("layout_heron_default")
        table = TableStructureOptions(mode=TableFormerMode.ACCURATE)
        self.assertEqual(layout.model_dump(), defaults.layout_options.model_dump())
        self.assertEqual(
            table.model_dump(), defaults.table_structure_options.model_dump()
        )
        self.assertEqual(layout.model_spec.repo_id, builder.MODEL_PROFILE["layout"])
        self.assertEqual(
            layout.engine_options.engine_type.value,
            builder.MODEL_PROFILE["layoutEngine"],
        )
        self.assertEqual(table.mode.value, builder.MODEL_PROFILE["table"])


if __name__ == "__main__":
    unittest.main()
