"""Frozen parser entry point, separate from Gunther's API and model gateway."""

import multiprocessing

from gunther.document_parser import _PROGRAM

if __name__ == "__main__":
    multiprocessing.freeze_support()
    exec(compile(_PROGRAM, "gunther-offline-parser", "exec"))
