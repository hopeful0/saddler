from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))

from saddler.runtime.backend import RUNTIME_BACKEND_REGISTRY, get_runtime_backend_cls


def test_runtime_backend_registry_registers_docker_backend() -> None:
    backend_cls = RUNTIME_BACKEND_REGISTRY.get("docker")
    assert backend_cls is get_runtime_backend_cls("docker")
