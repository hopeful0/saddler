from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))

from saddler.storage.model import (
    LocalStorageSpec,
    NFSStorageSpec,
    STORAGE_SPEC_REGISTRY,
)


def test_storage_spec_registry_registers_local_spec() -> None:
    assert STORAGE_SPEC_REGISTRY.get("local") is LocalStorageSpec


def test_storage_spec_registry_registers_nfs_spec() -> None:
    assert STORAGE_SPEC_REGISTRY.get("nfs") is NFSStorageSpec
