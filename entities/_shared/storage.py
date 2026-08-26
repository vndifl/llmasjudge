"""Local artifact storage behind a small replaceable boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / "runs"


def run_dir(campaign_id: str) -> Path:
    path = RUNS_ROOT / campaign_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_once(campaign_id: str, filename: str, data: dict[str, Any]) -> None:
    with (run_dir(campaign_id) / filename).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)


def write_snapshot(campaign_id: str, filename: str, data: dict[str, Any]) -> None:
    (run_dir(campaign_id) / filename).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def write_text(campaign_id: str, filename: str, content: str) -> None:
    (run_dir(campaign_id) / filename).write_text(content, encoding="utf-8")
