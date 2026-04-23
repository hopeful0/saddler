from pathlib import PurePosixPath
from typing import Annotated

from pydantic import AfterValidator


def _check_posix_absolute_str(v: str) -> str:
    s = v.strip()
    if not s:
        raise ValueError("Path must be non-empty")
    pp = PurePosixPath(s)
    if not pp.is_absolute():
        raise ValueError("Path must be an absolute POSIX path")
    return str(pp)


PosixAbsolutePath = Annotated[str, AfterValidator(_check_posix_absolute_str)]
