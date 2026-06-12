import os


def normalize_slashes(path: str) -> str:
    return (path or "").replace("\\", "/")


def normalize_folder(folder: str | None) -> str:
    value = normalize_slashes(folder or "").strip("/")
    parts = [part for part in value.split("/") if part and part != "."]
    return "/".join(parts)


def relative_folder_path(artist_path: str, file_path: str) -> str:
    artist = normalize_slashes(os.path.abspath(artist_path)).rstrip("/")
    file_dir = normalize_slashes(os.path.abspath(os.path.dirname(file_path))).rstrip("/")
    if file_dir == artist:
        return ""
    prefix = artist + "/"
    if file_dir.startswith(prefix):
        return normalize_folder(file_dir[len(prefix):])
    return normalize_folder(os.path.basename(file_dir))


def folder_path_prefix(artist_path: str, folder: str | None) -> str:
    artist = normalize_slashes(os.path.abspath(artist_path)).rstrip("/")
    folder = normalize_folder(folder)
    if not folder:
        return artist + "/"
    return f"{artist}/{folder}/"
