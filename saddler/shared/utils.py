import secrets
from typing import Literal, get_args, get_origin

from pydantic import BaseModel


def generate_id() -> str:
    return secrets.token_hex(16)


def resolve_model_str_field_value(model_cls: type[BaseModel], field_name: str) -> str:
    field_info = model_cls.model_fields.get(field_name)
    if field_info is None:
        raise ValueError(f"{model_cls.__name__} must define a `{field_name}` field")

    if isinstance(field_info.default, str) and field_info.default:
        return field_info.default

    annotation = field_info.annotation
    if get_origin(annotation) is Literal:
        literal_values = get_args(annotation)
        if len(literal_values) == 1 and isinstance(literal_values[0], str):
            return literal_values[0]

    raise ValueError(
        f"{model_cls.__name__}.{field_name} must provide a non-empty default string or single string Literal"
    )
