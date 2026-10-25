"""Run with: uv run --frozen --project tools/document-parser scripts/build-document-parser.py

Build on the target OS/architecture. Models are downloaded by the release builder;
the installed helper is offline. The manifest records all bytes and package versions.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from importlib.metadata import distributions
from pathlib import Path

MODEL_PROFILE = {
    "layout": "docling-project/docling-layout-heron",
    "layoutEngine": "transformers",
    "table": "accurate",
    "ocrEngine": "onnxruntime",
}


def trim_model_variants(models: Path) -> None:
    """Keep the model variants selected by the pinned offline pipeline.

    Docling's downloader fetches every layout engine and both table modes.
    Removing alternatives does not change the retained weights or precision.
    """
    required = (
        models / "docling-project--docling-layout-heron" / "model.safetensors",
        models
        / "docling-project--docling-models"
        / "model_artifacts"
        / "tableformer"
        / "accurate"
        / "tableformer_accurate.safetensors",
    )
    if not all(path.is_file() for path in required) or not any(
        (models / "RapidOcr").glob("*.onnx")
    ):
        raise ValueError(
            "The active layout, accurate table and ONNX OCR models are required"
        )
    for directory in (
        models / "docling-project--docling-layout-heron-onnx",
        models
        / "docling-project--docling-models"
        / "model_artifacts"
        / "tableformer"
        / "fast",
    ):
        if directory.exists():
            shutil.rmtree(directory)
    for path in (models / "RapidOcr").glob("*.pth"):
        path.unlink()


def copy_runtime(source: Path, destination: Path) -> None:
    # PyInstaller uses relative links for native libraries. Dereferencing them
    # duplicates hundreds of MB and changes no functionality.
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)


def build() -> None:
    root = Path(__file__).resolve().parents[1]
    project = root / "tools" / "document-parser"
    directory = project / ".build"
    bundle = directory / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    models = bundle / "models"
    from docling.utils.model_downloader import download_models

    if "--offline" not in sys.argv:
        download_models(
            output_dir=models,
            with_layout=True,
            with_tableformer=True,
            with_rapidocr=True,
            rapidocr_models=["onnxruntime:ch"],
            with_easyocr=False,
            with_code_formula=False,
            with_picture_classifier=False,
        )
    elif not models.is_dir():
        raise SystemExit("Offline builds require previously fetched parser models.")
    trim_model_variants(models)
    if "--models-only" in sys.argv:
        print(f"Fetched offline parser models: {models}")
        return
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "gunther-docling",
        "--paths",
        str(root / "apps" / "backend"),
        "--distpath",
        str(directory / "dist"),
        "--workpath",
        str(directory / "work"),
        "--specpath",
        str(directory),
        "--collect-all",
        "docling",
        "--copy-metadata",
        "docling-slim",
        "--collect-all",
        "docling_core",
        "--collect-all",
        "docling_ibm_models",
        "--collect-all",
        "docling_parse",
        "--collect-all",
        "pypdfium2",
        "--collect-all",
        "rapidocr",
        "--collect-all",
        "onnxruntime",
        "--collect-all",
        "torch",
        "--collect-all",
        "torchvision",
        "--collect-all",
        "rtree",
        "--collect-all",
        "PIL",
    ]
    identity = os.environ.get("APPLE_SIGNING_IDENTITY", "").strip()
    if sys.platform == "darwin" and identity:
        command += [
            "--codesign-identity",
            identity,
            "--osx-entitlements-file",
            str(root / "apps" / "desktop" / "src-tauri" / "HelperEntitlements.plist"),
        ]
    subprocess.run(
        [*command, str(project / "helper.py")],
        check=True,
        cwd=root,
        env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(directory / "cache")},
    )
    runtime = bundle / "runtime"
    copy_runtime(directory / "dist" / "gunther-docling", runtime)
    files = {}
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        digest = hashlib.sha256()
        with path.open("rb") as content:
            for part in iter(lambda: content.read(8 * 1024 * 1024), b""):
                digest.update(part)
        files[path.relative_to(bundle).as_posix()] = digest.hexdigest()
    manifest = {
        "schema": 1,
        "platform": sys.platform,
        "architecture": platform.machine(),
        "device": "cpu",
        "modelProfile": MODEL_PROFILE,
        "python": platform.python_version(),
        "files": files,
        "lockHash": hashlib.sha256((project / "uv.lock").read_bytes()).hexdigest(),
        "sourceHashes": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "apps/backend/gunther/document_parser.py",
                "apps/backend/gunther/document_structure.py",
                "apps/backend/gunther/pdf_native.py",
                "tools/document-parser/helper.py",
                "scripts/build-document-parser.py",
            )
        },
        "packages": {
            dist.metadata["Name"]: dist.version
            for dist in distributions()
            if dist.metadata.get("Name")
        },
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
    )
    print(f"Built CPU parser: {bundle}")


if __name__ == "__main__":
    build()
