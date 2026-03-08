"""設定読み込み: settings.yaml → dataclass マッピング."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from loguru import logger


@dataclass
class NotebookLMConfig:
    backend: str = "notebooklm-py"
    audio_language: str = "ja"
    audio_length: str = "short"
    generation_timeout_seconds: int = 600
    generation_poll_interval_seconds: int = 10
    prompt_presets: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        valid_backends = {"notebooklm-py", "playwright"}
        if self.backend not in valid_backends:
            msg = f"Invalid backend: {self.backend!r}. Must be one of {valid_backends}"
            raise ValueError(msg)
        valid_lengths = {"short", "default"}
        if self.audio_length not in valid_lengths:
            msg = (
                f"Invalid audio_length: {self.audio_length!r}."
                f" Must be one of {valid_lengths}"
            )
            raise ValueError(msg)


@dataclass
class YouTubeConfig:
    privacy_status: str = "public"
    category_id: str = "27"
    playlist_id: str | None = None
    title_prefix: str = "🎧"
    title_max_length: int = 95
    default_tags: list[str] = field(default_factory=list)
    daily_upload_limit: int = 5


@dataclass
class ThumbnailConfig:
    width: int = 1280
    height: int = 720
    overlay_opacity: float = 0.5
    font_name: str = "NotoSansJP-Bold"
    title_font_size_max: int = 80
    title_font_size_min: int = 44
    subtitle_font_size: int = 24
    text_color: str = "#FFFFFF"
    fallback_gradient: dict[str, str] = field(
        default_factory=lambda: {"start": "#1a1a2e", "end": "#16213e"}
    )


@dataclass
class CredentialsConfig:
    youtube_client_secret: str = "./credentials/youtube_client_secret.json"
    youtube_token: str = "./credentials/youtube_token.json"


@dataclass
class GeneralConfig:
    tmp_dir: str = "./tmp"
    state_file: str = "./data/state.json"
    max_retries: int = 3
    retry_backoff_base: int = 2


@dataclass
class Settings:
    notebooklm: NotebookLMConfig
    youtube: YouTubeConfig
    thumbnail: ThumbnailConfig
    credentials: CredentialsConfig
    general: GeneralConfig


def load_settings(config_path: Path | None = None) -> Settings:
    """settings.yaml を読み込み Settings dataclass を返す."""
    if config_path is None:
        config_path = Path("config/settings.yaml")

    if not config_path.exists():
        msg = f"Settings file not found: {config_path}"
        raise FileNotFoundError(msg)

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    logger.debug("Loaded settings from {}", config_path)

    return Settings(
        notebooklm=NotebookLMConfig(**raw.get("notebooklm", {})),
        youtube=YouTubeConfig(**raw.get("youtube", {})),
        thumbnail=ThumbnailConfig(**raw.get("thumbnail", {})),
        credentials=CredentialsConfig(**raw.get("credentials", {})),
        general=GeneralConfig(**raw.get("general", {})),
    )
