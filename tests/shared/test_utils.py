from pathlib import Path
import sys
from typing import Literal

import pytest
from pydantic import BaseModel

sys.path.append(str(Path(__file__).resolve().parents[2]))

from saddler.shared.utils import resolve_model_str_field_value


class _WithDefaultType(BaseModel):
    type: str = "local"


class _WithLiteralType(BaseModel):
    type: Literal["docker"]


class _MissingType(BaseModel):
    name: str


class _InvalidType(BaseModel):
    type: int = 1


def test_resolve_model_str_field_value_with_default() -> None:
    assert resolve_model_str_field_value(_WithDefaultType, "type") == "local"


def test_resolve_model_str_field_value_with_literal() -> None:
    assert resolve_model_str_field_value(_WithLiteralType, "type") == "docker"


def test_resolve_model_str_field_value_missing_field_raises() -> None:
    with pytest.raises(ValueError, match="must define"):
        resolve_model_str_field_value(_MissingType, "type")


def test_resolve_model_str_field_value_invalid_field_raises() -> None:
    with pytest.raises(ValueError, match="must provide"):
        resolve_model_str_field_value(_InvalidType, "type")
