"""Verify offline packaging keeps model contents and native-library links."""

import importlib.util
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
