import re

ARCHIVE_EXTENSIONS = {".rar", ".zip", ".7z", ".tar", ".gz", ".bz2", ".xz"}
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".gif", ".webp", ".bmp",
    ".tiff", ".tif", ".avif", ".heic", ".heif",
}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".wmv", ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".flv", ".3gp"}
SOURCE_EXTENSIONS = {".psd", ".psb", ".clip", ".tga", ".dds"}
TEXT_EXTENSIONS = {".txt", ".md"}


def _extension(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return f".{ext}" if ext else ""


def is_image_file(filename: str) -> bool:
    return _extension(filename) in IMAGE_EXTENSIONS


def is_archive_file(filename: str) -> bool:
    return _extension(filename) in ARCHIVE_EXTENSIONS


def is_video_file(filename: str) -> bool:
    return _extension(filename) in VIDEO_EXTENSIONS


def is_source_file(filename: str) -> bool:
    return _extension(filename) in SOURCE_EXTENSIONS


def media_type_for_file(filename: str) -> str:
    ext = _extension(filename)
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in SOURCE_EXTENSIONS:
        return "source"
    if ext in ARCHIVE_EXTENSIONS:
        return "archive"
    return ""


def is_media_file(filename: str) -> bool:
    return bool(media_type_for_file(filename))


def is_text_file(filename: str) -> bool:
    return _extension(filename) in TEXT_EXTENSIONS


def extract_date(folder_name: str) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", folder_name)
    return m.group(1) if m else ""
