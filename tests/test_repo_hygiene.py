"""Repository-wide rules: no em dash anywhere, LF line endings in Python sources."""

from helpers import REPO

EM_DASH = chr(0x2014)
SKIP_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "recorded",
    "build",
    "dist",
    "node_modules",
}
EXTENSIONS = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".cfg", ""}


def iter_text_files():
    for path in REPO.rglob("*"):
        parts = path.relative_to(REPO).parts
        if any(part in SKIP_PARTS or part.endswith(".egg-info") for part in parts):
            continue
        if path.is_file() and path.suffix in EXTENSIONS:
            yield path


def test_no_em_dash_anywhere():
    offenders = []
    for path in iter_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if EM_DASH in text:
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_python_sources_use_lf():
    offenders = [
        str(path.relative_to(REPO))
        for path in iter_text_files()
        if path.suffix == ".py" and b"\r\n" in path.read_bytes()
    ]
    assert offenders == []
