"""Small security helpers for upload/file path validation."""

from pathlib import Path
import re


SENSITIVE_NAME_PATTERNS = [
    re.compile(r"(^|[._-])env($|[._-])", re.IGNORECASE),
    re.compile(r"id_(rsa|dsa|ed25519)", re.IGNORECASE),
    re.compile(r"credentials?", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"\.p12$", re.IGNORECASE),
    re.compile(r"\.jks$", re.IGNORECASE),
]


def is_sensitive_file_name(file_name: str) -> bool:
    normalized = Path(file_name).name
    return any(pattern.search(normalized) for pattern in SENSITIVE_NAME_PATTERNS)


def has_symlink_in_path(project_root: Path, candidate_path: Path) -> bool:
    """Return True if any existing parent path under project root is a symlink."""

    root = project_root.resolve()
    current = candidate_path.resolve().parent
    while True:
        if current.exists() and current.is_symlink():
            return True
        if current == root:
            return False
        if not current.is_relative_to(root):
            return False
        if current.parent == current:
            return False
        current = current.parent

