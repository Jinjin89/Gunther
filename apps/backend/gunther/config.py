import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gunther.storage_budget import (
    DEFAULT_STORAGE_MIN_FREE_BYTES,
    DEFAULT_STORAGE_QUOTA_BYTES,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Gunther API"
    api_prefix: str = "/api"
    database_url: str = f"sqlite+pysqlite:///{PROJECT_ROOT / 'data' / 'gunther.sqlite'}"
    assets_dir: Path = PROJECT_ROOT / "data" / "assets"
    recordings_dir: Path = PROJECT_ROOT / "data" / "recordings"
    # Managed originals share one quota. The free-space floor protects SQLite,
    # the OS, and successful finalization from an otherwise full filesystem.
    storage_quota_bytes: int = DEFAULT_STORAGE_QUOTA_BYTES
    storage_min_free_bytes: int = DEFAULT_STORAGE_MIN_FREE_BYTES
    # Items rest in Trash this long before they are deleted for good.
    trash_retention_days: int = Field(default=30, ge=1, le=3650)
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    openai_api_key: str | None = None
    openai_web_search_model: str = "gpt-5.6"
    openai_transcription_model: str = "gpt-live-transcribe"
    openai_transcription_delay: Literal["low", "medium", "high"] = "medium"
    openai_transcription_languages: str = "en,zh-cn"
    openai_summary_model: str = "gpt-5.6"
    stt_provider: Literal["auto", "sensevoice", "openai"] = "auto"
    ocr_provider: Literal["auto", "vision", "tesseract", "disabled"] = "auto"
    ocr_tesseract_command: str = "tesseract"
    ocr_pdftoppm_command: str = "pdftoppm"
    ocr_tesseract_languages: str = "eng+chi_sim"
    sensevoice_url: str = "http://127.0.0.1:8765"
    sensevoice_segment_seconds: float = 3.2
    # The packaged desktop helper supplies a fresh, per-launch secret. Development
    # and native clients may leave it unset, but a server without a configured
    # secret deliberately rejects requests that unexpectedly present one.
    auth_token: str | None = None
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://tauri.localhost",
        "tauri://localhost",
    ]
    # Production and new local workspaces start as the user's own empty library.
    # Demo content remains opt-in through SEED_DEMO=true for product tours.
    seed_demo: bool = False
    processing_worker_enabled: bool = True
    embedding_model_path: Path | None = None
    embedding_model_version: str = "multilingual-e5-small-v1"
    docling_python: Path | None = None
    docling_artifacts_path: Path | None = None

    @field_validator("embedding_model_version")
    @classmethod
    def validate_embedding_version(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value):
            raise ValueError("Use a short model revision or content fingerprint")
        return value

    @field_validator("embedding_model_path", "docling_python", "docling_artifacts_path")
    @classmethod
    def validate_local_model_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Model/runtime paths must be absolute local paths")
        return value

    @field_validator("api_prefix")
    @classmethod
    def normalize_api_prefix(cls, value: str) -> str:
        prefix = value.strip()
        if prefix == "/":
            return ""
        normalized = prefix.rstrip("/")
        segments = normalized.split("/")[1:]
        if (
            re.fullmatch(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*/?", prefix)
            is None
            or any(segment in {".", ".."} for segment in segments)
        ):
            raise ValueError("api_prefix must be a simple absolute path prefix")
        return normalized

    @field_validator("storage_quota_bytes")
    @classmethod
    def validate_storage_quota(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("storage_quota_bytes must be greater than zero")
        return value

    @field_validator("storage_min_free_bytes")
    @classmethod
    def validate_storage_free_floor(cls, value: int) -> int:
        if value < 0:
            raise ValueError("storage_min_free_bytes cannot be negative")
        return value

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
