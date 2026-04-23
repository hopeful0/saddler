import subprocess
from pathlib import Path

from saddler.resource import get_fetcher_cls, parse_source
from saddler.infra.fetcher.git import GitFetcherSpec
from saddler.resource.model import ResourceSpec

# Ensure infra fetchers are registered.
from saddler import infra as _infra  # noqa: F401


def _init_git_repo(path: Path) -> str:
    subprocess.run(
        ["git", "init"], cwd=path, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test-user"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "add", "."], cwd=path, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "branch", "release"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return "release"


def test_infra_registers_local_and_git_fetchers() -> None:
    assert get_fetcher_cls("local").__name__ == "LocalFetcher"
    assert get_fetcher_cls("git").__name__ == "GitFetcher"


def test_parse_source_prefers_git_over_local_for_git_like_uri() -> None:
    fetcher_cls, spec = parse_source("https://example.com/repo.git")
    assert fetcher_cls.__name__ == "GitFetcher"
    assert spec.kind == "git"
    assert isinstance(spec.fetcher_spec, GitFetcherSpec)


def test_parse_source_prefers_local_when_path_exists_even_if_git_like(
    tmp_path: Path,
) -> None:
    local_git_like = tmp_path / "repo.git"
    local_git_like.mkdir()
    fetcher_cls, spec = parse_source(str(local_git_like))
    assert fetcher_cls.__name__ == "LocalFetcher"
    assert spec.kind == "local"


def test_local_fetcher_resolves_existing_directory(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    fetcher_cls, spec = parse_source(str(source))
    fetcher = fetcher_cls()
    with fetcher.fetch_source(spec) as resolved:
        assert resolved == source.resolve()


def test_git_fetcher_clones_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("hello", encoding="utf-8")
    ref = _init_git_repo(repo)

    fetcher_cls, spec = parse_source(f"{repo}/.git#{ref}")
    fetcher = fetcher_cls()
    assert isinstance(spec.fetcher_spec, GitFetcherSpec)
    assert spec.fetcher_spec.ref == ref
    with fetcher.fetch_source(spec) as resolved:
        assert (resolved / "README.md").read_text(encoding="utf-8") == "hello"


def test_local_fetcher_fetch_resource_resolves_from_spec_when_root_missing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "rules").mkdir()
    (source / "rules" / "role.mdc").write_text("rule", encoding="utf-8")
    local_fetcher_cls = get_fetcher_cls("local")
    fetcher = local_fetcher_cls()

    resource = ResourceSpec(
        kind="rule",
        name="role",
        source=str(source),
        path="rules/role.mdc",
    )
    with fetcher.fetch_resource(resource, source_root=None) as resource_path:
        assert resource_path.read_text(encoding="utf-8") == "rule"


def test_git_fetcher_fetch_resource_resolves_from_spec_when_root_missing(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "skills").mkdir()
    (repo / "skills" / "demo").mkdir()
    (repo / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\nhello\n",
        encoding="utf-8",
    )
    ref = _init_git_repo(repo)
    git_fetcher_cls = get_fetcher_cls("git")
    fetcher = git_fetcher_cls()

    resource = ResourceSpec(
        kind="skill",
        name="demo",
        source=f"{repo}/.git#{ref}",
        path="skills/demo/SKILL.md",
    )
    with fetcher.fetch_resource(resource, source_root=None) as resource_path:
        assert "hello" in resource_path.read_text(encoding="utf-8")
