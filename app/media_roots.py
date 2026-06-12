import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


DEFAULT_MEDIA_ROOTS_DIR = "/media"
FALLBACK_PICTURES_ROOT = "/pictures"


@dataclass(frozen=True)
class MediaRootConfig:
    roots: list[str]
    labels: list[str]
    real_paths: list[str]


def _split_csv(value: str | None, *, strip_trailing_slashes: bool = True) -> list[str]:
    result = []
    for part in (value or "").split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        if strip_trailing_slashes:
            cleaned = cleaned.rstrip("/\\")
        result.append(cleaned)
    return result


def _display_label(root: str) -> str:
    cleaned = root.rstrip("/\\")
    return os.path.basename(cleaned) or cleaned.strip("/\\") or cleaned


def _discover_media_roots(media_dir: str) -> list[str]:
    root = Path(media_dir)
    if not root.is_dir():
        return []

    dirs = [path for path in root.iterdir() if path.is_dir()]
    dirs.sort(key=lambda path: path.name.casefold())
    return [str(path).rstrip("/\\") for path in dirs]


def _unescape_mount_field(value: str) -> str:
    # Linux mountinfo escapes spaces and a few separators as octal sequences.
    return unquote(
        value
        .replace("\\040", "%20")
        .replace("\\011", "%09")
        .replace("\\012", "%0A")
        .replace("\\134", "%5C")
    )


def _mount_sources_from_text(mountinfo_text: str) -> dict[str, str]:
    sources: dict[str, str] = {}
    for line in mountinfo_text.splitlines():
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        left_parts = left.split()
        right_parts = right.split()
        if len(left_parts) < 5 or len(right_parts) < 2:
            continue
        root = _unescape_mount_field(left_parts[3]).rstrip("/\\")
        mount_point = _unescape_mount_field(left_parts[4]).rstrip("/\\")
        mount_source = _unescape_mount_field(right_parts[1]).rstrip("/\\")
        if root and root != "/" and root.startswith("/"):
            source = root
        elif mount_source and mount_source.startswith("/") and not mount_source.startswith("/dev/"):
            source = mount_source
        else:
            source = ""
        if mount_point and source:
            sources[mount_point] = source
    return sources


def _read_mountinfo() -> str:
    try:
        return Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError:
        return ""


def resolve_media_roots(
    *,
    env: dict[str, str] | None = None,
    auto_roots: list[str] | None = None,
    mountinfo_text: str | None = None,
) -> MediaRootConfig:
    env = env or os.environ
    explicit_roots = _split_csv(env.get("PICTURES_ROOT"))
    roots = explicit_roots
    if not roots:
        if auto_roots is None:
            roots = _discover_media_roots(env.get("MEDIA_ROOTS_DIR", DEFAULT_MEDIA_ROOTS_DIR))
        else:
            roots = [root.rstrip("/\\") for root in auto_roots if root.strip()]
    if not roots:
        roots = [FALLBACK_PICTURES_ROOT]

    explicit_labels = _split_csv(env.get("PICTURES_ROOT_LABELS"), strip_trailing_slashes=False)
    labels = [
        explicit_labels[index] if index < len(explicit_labels) else _display_label(root)
        for index, root in enumerate(roots)
    ]

    explicit_real_paths = _split_csv(env.get("PICTURES_ROOT_REAL_PATHS"))
    if mountinfo_text is None:
        mountinfo_text = _read_mountinfo()
    mount_sources = _mount_sources_from_text(mountinfo_text)
    real_paths = [
        explicit_real_paths[index]
        if index < len(explicit_real_paths)
        else mount_sources.get(root.rstrip("/\\"), root)
        for index, root in enumerate(roots)
    ]

    return MediaRootConfig(roots=roots, labels=labels, real_paths=real_paths)


def load_media_root_globals() -> tuple[list[str], list[str], list[str]]:
    config = resolve_media_roots()
    return config.roots, config.labels, config.real_paths
