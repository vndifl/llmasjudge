"""Robust JSON extraction and one-retry model repair helpers."""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError


ModelT = TypeVar("ModelT", bound=BaseModel)


def extract_json(text: str) -> object:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = min((index for index in (cleaned.find("{"), cleaned.find("[")) if index >= 0), default=-1)
        if start < 0:
            raise
        opener = cleaned[start]
        closer = "}" if opener == "{" else "]"
        end = cleaned.rfind(closer)
        if end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def parse_model(text: str, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(extract_json(text))


def validation_message(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return json.dumps(error.errors(include_url=False), indent=2, default=str)
    return str(error)
