"""Infrastructure adapters for saddler."""

# Import side-effects to register adapters.
from .fetcher import GitFetcher, LocalFetcher
from .harness.claude_code import ClaudeCodeHarness
from .harness.codex import CodexHarness
from .harness.cursor import CursorHarness
from .harness.gemini import GeminiHarness
from .harness.openclaw import OpenClawHarness
from .harness.opencode import OpenCodeHarness
from .runtime.docker import (
    DockerRuntimeBackend,
    DockerRuntimeSpec,
    DockerRuntimeState,
)
from .runtime.local import LocalRuntimeBackend

__all__ = [
    "DockerRuntimeBackend",
    "DockerRuntimeSpec",
    "DockerRuntimeState",
    "LocalRuntimeBackend",
    "CursorHarness",
    "ClaudeCodeHarness",
    "CodexHarness",
    "GeminiHarness",
    "OpenClawHarness",
    "OpenCodeHarness",
    "GitFetcher",
    "LocalFetcher",
]
