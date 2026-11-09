import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Mapping, Optional

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    telegram_token: str = ""
    admin_ids: List[int] = field(default_factory=list)
    vk_token: str = ""
    vk_api_version: str = "5.199"
    data_dir: Path = BASE_DIR / "data"
    texts_file: Path = BASE_DIR / "db_text.txt"
    log_level: str = "info"
    timezone: str = "Europe/Moscow"
    autofill_interval_min: int = 30

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def charts_dir(self) -> Path:
        return self.data_dir / "charts"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @classmethod
    def load(cls, base_dir: Path = BASE_DIR, env: Optional[Mapping[str, str]] = None) -> "Config":
        """Read .env from base_dir; real environment variables win over the file."""
        values = {k: v for k, v in dotenv_values(base_dir / ".env").items() if v is not None}
        values.update(os.environ if env is None else env)

        def get(name: str, default: str = "") -> str:
            return (values.get(name) or default).strip()

        return cls(
            telegram_token=get("TELEGRAM_BOT_TOKEN"),
            admin_ids=parse_ids(get("TELEGRAM_ADMIN_IDS")),
            vk_token=get("VK_TOKEN"),
            vk_api_version=get("VK_API_VERSION", "5.199"),
            data_dir=_path(base_dir, get("DATA_DIR", "data")),
            texts_file=_path(base_dir, get("TEXTS_FILE", "db_text.txt")),
            log_level=get("LOG_LEVEL", "info"),
            timezone=get("TIMEZONE", "Europe/Moscow"),
            autofill_interval_min=max(5, int(get("AUTOFILL_INTERVAL_MIN", "30"))),
        )


def parse_ids(raw: str) -> List[int]:
    return [int(chunk) for chunk in raw.replace(";", ",").split(",") if chunk.strip()]


def _path(base_dir: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else base_dir / path
