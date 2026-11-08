"""Runtime configuration. All paths and credentials come from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    first_season: int
    last_season: int
    http_timeout: float
    max_retries: int

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def lake_dir(self) -> Path:
        return self.data_dir / "lake"

    @property
    def warehouse(self) -> Path:
        return self.data_dir / "warehouse.sqlite"

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "models"


def settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("NFLQ_DATA_DIR", ROOT / "data")).resolve(),
        first_season=int(os.environ.get("NFLQ_FIRST_SEASON", "2006")),
        last_season=int(os.environ.get("NFLQ_LAST_SEASON", "2026")),
        http_timeout=float(os.environ.get("NFLQ_HTTP_TIMEOUT", "120")),
        max_retries=int(os.environ.get("NFLQ_MAX_RETRIES", "5")),
    )
