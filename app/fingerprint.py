import os
import importlib.util
from dataclasses import dataclass


DEFAULT_CHUNK_SIZE = 1024 * 1024
DEFAULT_BLAKE3_THREADS = int(os.environ.get("BLAKE3_THREADS", "1"))


@dataclass(frozen=True)
class FileStat:
    file_size: int
    file_mtime: float
    st_dev: int | None
    st_ino: int | None


def normalize_path(path: str) -> str:
    return os.path.abspath(path).replace("\\", "/")


def stat_path(path: str) -> FileStat:
    stat = os.stat(path)
    st_dev = getattr(stat, "st_dev", None)
    st_ino = getattr(stat, "st_ino", None)
    return FileStat(
        file_size=stat.st_size,
        file_mtime=stat.st_mtime,
        st_dev=st_dev if st_dev else None,
        st_ino=st_ino if st_ino else None,
    )


def is_blake3_available() -> bool:
    return importlib.util.find_spec("blake3") is not None


def hash_file(path: str, *, max_threads: int | None = None, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    try:
        from blake3 import blake3
    except ImportError as exc:
        raise RuntimeError("blake3 package is required for content hashing") from exc

    threads = max_threads if max_threads and max_threads > 0 else DEFAULT_BLAKE3_THREADS
    hasher = blake3(max_threads=threads)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()
