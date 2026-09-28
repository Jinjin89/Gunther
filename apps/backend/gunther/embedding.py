"""Local text embeddings for semantic search.

multilingual-e5-small, quantized, runs on ONNX Runtime with the Hugging Face
``tokenizers`` library: small enough to ship inside the desktop app, and it
handles Chinese and English in one vector space. The model is read from disk
only; nothing is downloaded at run time and no text leaves the device.

E5 expects ``query: `` before questions and ``passage: `` before stored text.
Token embeddings are mean-pooled over the attention mask and L2-normalized. A
text longer than one window is encoded window by window and averaged, so no
part of a long block is silently dropped.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from threading import Lock
from typing import Any

MODEL_NAME = "multilingual-e5-small"
MODEL_FILE = "model.onnx"
TOKENIZER_FILE = "tokenizer.json"
# 512 positions, minus the start and end tokens around every window.
WINDOW_TOKENS = 510
BATCH_SIZE = 16


def model_is_installed(directory: Path) -> bool:
    return (directory / MODEL_FILE).is_file() and (directory / TOKENIZER_FILE).is_file()


def default_model_directory(project_root: Path) -> Path | None:
    """Where the bundled or fetched model is, if it is anywhere."""

    candidates = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:  # a frozen desktop helper carries the model as data
        candidates.append(Path(bundle) / "models" / MODEL_NAME)
    candidates.append(project_root / "apps" / "backend" / "models" / MODEL_NAME)
    return next((path for path in candidates if model_is_installed(path)), None)


class OnnxEmbedder:
    """E5 sentence embeddings on ONNX Runtime; loads lazily and is thread-safe."""

    # Cosine similarity below this is unrelated text for this model: E5 packs
    # scores into roughly 0.7-0.95, so the usual 0.5-style cut-offs keep noise.
    min_similarity = 0.80

    def __init__(self, model_dir: Path, version: str) -> None:
        self.model_id = f"e5-onnx:{version}"
        self.model_dir = model_dir
        self._lock = Lock()
        self._session: Any = None
        self._tokenizer: Any = None
        self._inputs: set[str] = set()
        self._output = ""

    def _load(self) -> None:
        import onnxruntime
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(self.model_dir / TOKENIZER_FILE))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        session = onnxruntime.InferenceSession(
            str(self.model_dir / MODEL_FILE), options, providers=["CPUExecutionProvider"]
        )
        outputs = [item.name for item in session.get_outputs()]
        self._output = "last_hidden_state" if "last_hidden_state" in outputs else outputs[0]
        self._inputs = {item.name for item in session.get_inputs()}
        self._start = tokenizer.token_to_id("<s>")
        self._end = tokenizer.token_to_id("</s>")
        self._pad = tokenizer.token_to_id("<pad>")
        if None in (self._start, self._end, self._pad):
            raise ValueError("The tokenizer does not have E5's special tokens")
        self._tokenizer, self._session = tokenizer, session

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        import numpy

        with self._lock:
            if self._session is None:
                self._load()
            prefix = self._tokenizer.encode(
                "query: " if query else "passage: ", add_special_tokens=False
            ).ids
            budget = WINDOW_TOKENS - len(prefix)
            windows: list[list[int]] = []
            spans: list[tuple[int, int]] = []
            for text in texts:
                ids = self._tokenizer.encode(text, add_special_tokens=False).ids
                start = len(windows)
                for offset in range(0, max(1, len(ids)), budget):
                    windows.append(
                        [self._start, *prefix, *ids[offset : offset + budget], self._end]
                    )
                spans.append((start, len(windows)))

            pooled: list[Any] = []
            for offset in range(0, len(windows), BATCH_SIZE):
                batch = windows[offset : offset + BATCH_SIZE]
                width = max(len(window) for window in batch)
                input_ids = numpy.full((len(batch), width), self._pad, dtype=numpy.int64)
                mask = numpy.zeros((len(batch), width), dtype=numpy.int64)
                for row, window in enumerate(batch):
                    input_ids[row, : len(window)] = window
                    mask[row, : len(window)] = 1
                feeds = {"input_ids": input_ids, "attention_mask": mask}
                if "token_type_ids" in self._inputs:
                    feeds["token_type_ids"] = numpy.zeros_like(input_ids)
                hidden = self._session.run([self._output], feeds)[0]
                weights = mask[..., None].astype(hidden.dtype)
                pooled.extend((hidden * weights).sum(axis=1) / weights.sum(axis=1))

            vectors = []
            for start, end in spans:
                vector = numpy.mean(pooled[start:end], axis=0)
                norm = float(numpy.linalg.norm(vector)) or 1.0
                vectors.append((vector / norm).astype(float).tolist())
            return vectors
