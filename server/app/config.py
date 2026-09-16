from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Where the Jellyfin-readable library lives. Shared with the Jellyfin container.
    media_root: Path = Path("/media/music-videos")
    # Sqlite db + download staging. Not exposed to Jellyfin.
    data_dir: Path = Path("/data")

    api_token: str = "change-me"
    download_concurrency: int = 1

    # Optional: when unset, jellyfin refreshes are skipped silently.
    jellyfin_url: str | None = None
    jellyfin_api_key: str | None = None

    log_level: str = "INFO"

    # Baked into release images from the git tag; "dev" everywhere else.
    app_version: str = "dev"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    def ensure_dirs(self) -> None:
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
