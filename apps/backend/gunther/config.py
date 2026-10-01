import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
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
    # Where libraries live on disk: originals under ``.gunther/`` and a readable
    # folder per library beside them (see library_folders). Unset keeps every
    # original in the data folder. The database never moves here.
    library_root: Path | None = None
    # Where originals were before the library root; they move into it once, at
    # startup. Defaults to the folders above.
    previous_assets_dir: Path | None = None
    previous_recordings_dir: Path | None = None
    # Managed originals share one quota. The free-space floor protects SQLite,
    # the OS, and successful finalization from an otherwise full filesystem.
    storage_quota_bytes: int = DEFAULT_STORAGE_QUOTA_BYTES
    storage_min_free_bytes: int = DEFAULT_STORAGE_MIN_FREE_BYTES
    # Answers read aloud are kept here as audio (see tts_service); deleting it only
    # makes them be spoken again.
    speech_dir: Path = PROJECT_ROOT / "data" / "speech"
    # Items rest in Trash this long before they are deleted for good.
    trash_retention_days: int = Field(default=30, ge=1, le=3650)
    # The language model that reads captures, answers Ask and writes topic
    # overviews: any service speaking OpenAI's API. The provider only picks the
    # defaults people start from. DEEPSEEK_* names from earlier versions still work.
    llm_provider: Literal["deepseek", "kimi", "glm", "qwen", "openai", "compatible"] = "deepseek"
    llm_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("llm_api_key", "deepseek_api_key")
    )
    llm_model: str = Field(
        default="deepseek-flash",
        validation_alias=AliasChoices("llm_model", "deepseek_model"),
    )
    llm_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices("llm_base_url", "deepseek_base_url"),
    )
    # Web search for Ask and Home, by Tavily (tavily.com). Without a key the web
    # is simply not offered.
    tavily_api_key: str | None = None
    web_search_depth: Literal["basic", "advanced"] = "basic"
    web_search_max_results: int = Field(default=6, ge=1, le=10)
    # Summaries of every capture, written after it is read (see digest) by the
    # Analysis model. Without a model there are none.
    ai_summaries: Literal["auto", "off"] = "auto"
    # Show photos to the model as images (otherwise only their recognized text).
    ai_summary_images: bool = True
    # Transcription before it had providers in the app (see speech_registry): these
    # describe the providers and jobs used until some are saved in Settings.
    stt_provider: Literal["auto", "sensevoice", "qwen", "compatible"] = "auto"
    stt_base_url: str = ""
    stt_api_key: str | None = None
    stt_model: str = "whisper-1"
    # Empty lets the model detect the language.
    stt_language: str = ""
    # Qwen3-ASR on Alibaba Cloud Model Studio (DashScope): audio goes in a
    # chat-completions request rather than /audio/transcriptions.
    qwen_stt_base_url: str = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    qwen_stt_api_key: str | None = None
    qwen_stt_model: str = "qwen3-asr-flash"
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
    # Search by meaning as well as by keyword, with a local model (Chinese and
    # English); nothing leaves the device. The model ships with the desktop app;
    # a checkout fetches it once with `npm run models:fetch`.
    semantic_search: bool = True
    # A different ONNX export of E5 (model.onnx + tokenizer.json). Give it a new
    # version whenever the weights change: vectors are kept per version.
    embedding_model_path: Path | None = None
    embedding_model_version: str = "me5-small-q8-761b726"
    docling_python: Path | None = None
    docling_artifacts_path: Path | None = None
    # Service settings changed in the app (see service_settings). They win over
    # this file and the environment. Unset keeps changes in memory only.
    service_settings_file: Path | None = None

    @field_validator("stt_provider", mode="before")
    @classmethod
    def retire_openai_transcription(cls, value: object) -> object:
        # OpenAI's own realtime engine is gone; an old setting falls back to automatic.
        return "auto" if value == "openai" else value

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

    @model_validator(mode="after")
    def place_originals_in_library_root(self) -> "Settings":
        if self.library_root is None:
            return self
        # Managed storage refuses symlinked paths, so use the real location.
        root = self.library_root.expanduser()
        if not root.is_absolute():
            raise ValueError("library_root must be an absolute path")
        self.library_root = root.resolve()
        for name in ("assets", "recordings"):
            field = f"{name}_dir"
            if field in self.model_fields_set:
                continue  # placed elsewhere on purpose
            previous = f"previous_{field}"
            if getattr(self, previous) is None:
                setattr(self, previous, getattr(self, field))
            setattr(self, field, self.library_root / ".gunther" / name)
        return self

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
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
