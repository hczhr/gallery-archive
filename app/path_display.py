import os

from app.media_roots import load_media_root_globals


PICTURES_ROOTS, PICTURES_ROOT_LABELS, PICTURES_ROOT_REAL_PATHS = load_media_root_globals()


def _label_for_root(root: str, index: int) -> str:
    if index < len(PICTURES_ROOT_LABELS):
        return PICTURES_ROOT_LABELS[index]
    return os.path.basename(root) or root.strip("/") or root


def _match_root(path: str):
    normalized = path.replace("\\", "/")
    roots = sorted(enumerate(PICTURES_ROOTS), key=lambda item: len(item[1]), reverse=True)
    for index, root in roots:
        root_normalized = root.replace("\\", "/").rstrip("/")
        if normalized == root_normalized or normalized.startswith(root_normalized + "/"):
            rel = normalized[len(root_normalized):].lstrip("/")
            return index, root_normalized, rel
    return None


def display_path(path: str) -> str:
    match = _match_root(path)
    if match:
        index, root_normalized, rel = match
        label = _label_for_root(root_normalized, index)
        return f"{label}/{rel}" if rel else label
    return path


def real_path(path: str) -> str:
    match = _match_root(path)
    if not match:
        return path

    index, _root_normalized, rel = match
    if index >= len(PICTURES_ROOT_REAL_PATHS):
        return path

    real_root = PICTURES_ROOT_REAL_PATHS[index].rstrip("/\\")
    if not real_root:
        return path
    if not rel:
        return real_root

    sep = "\\" if "\\" in real_root else "/"
    return real_root + sep + rel.replace("/", sep).replace("\\", sep)
