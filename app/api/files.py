import hashlib
import io
import json
import math
import os
import subprocess
import threading
import time
from email.utils import formatdate
from urllib.parse import quote

from fastapi import APIRouter, Query, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from app.log import logger
from app.media_roots import load_media_root_globals

router = APIRouter()

PICTURES_ROOTS, _PICTURES_ROOT_LABELS, _PICTURES_ROOT_REAL_PATHS = load_media_root_globals()
PICTURES_ROOT = ",".join(PICTURES_ROOTS)
VIDEO_STREAM_CHUNK_SIZE = int(os.environ.get("VIDEO_STREAM_CHUNK_SIZE", str(1024 * 1024)))
VIDEO_PREVIEW_FRAME_TIMEOUT = float(os.environ.get("VIDEO_PREVIEW_FRAME_TIMEOUT", "8"))
VIDEO_COMPAT_MAX_EDGE = max(360, int(os.environ.get("VIDEO_COMPAT_MAX_EDGE", "1280")))
VIDEO_COMPAT_CRF = min(32, max(18, int(os.environ.get("VIDEO_COMPAT_CRF", "23"))))
VIDEO_COMPAT_PRESET = os.environ.get("VIDEO_COMPAT_PRESET", "veryfast")
VIDEO_COMPAT_THREADS = max(1, int(os.environ.get("VIDEO_COMPAT_THREADS", "1")))
VIDEO_TRANSCODE_CACHE_DIR = os.environ.get("VIDEO_TRANSCODE_CACHE_DIR", "/transcode-cache")
VIDEO_TRANSCODE_CACHE_MAX_BYTES = int(os.environ.get("VIDEO_TRANSCODE_CACHE_MAX_BYTES", "900000000"))
VIDEO_TRANSCODE_ENCODER = os.environ.get("VIDEO_TRANSCODE_ENCODER", "h264_qsv")
VIDEO_TRANSCODE_GPU_DECODE = os.environ.get("VIDEO_TRANSCODE_GPU_DECODE", "auto")
VIDEO_TRANSCODE_MAX_EDGE = max(360, int(os.environ.get("VIDEO_TRANSCODE_MAX_EDGE", "1280")))
VIDEO_TRANSCODE_QSV_GLOBAL_QUALITY = int(os.environ.get("VIDEO_TRANSCODE_QSV_GLOBAL_QUALITY", "28"))
VIDEO_TRANSCODE_TIMEOUT = float(os.environ.get("VIDEO_TRANSCODE_TIMEOUT", "1800"))
VIDEO_TRANSCODE_CONCURRENCY = max(1, int(os.environ.get("VIDEO_TRANSCODE_CONCURRENCY", "1")))
VIDEO_TRANSCODE_CPU_FALLBACK = os.environ.get("VIDEO_TRANSCODE_CPU_FALLBACK", "1").lower() not in {"0", "false", "no"}
VIDEO_TRANSCODE_VAAPI_FALLBACK = os.environ.get("VIDEO_TRANSCODE_VAAPI_FALLBACK", "1").lower() not in {"0", "false", "no"}
VIDEO_TRANSCODE_VAAPI_DEVICE = os.environ.get("VIDEO_TRANSCODE_VAAPI_DEVICE", "/dev/dri/renderD128")
VIDEO_TRANSCODE_VAAPI_QP = min(40, max(18, int(os.environ.get("VIDEO_TRANSCODE_VAAPI_QP", "28"))))
VIDEO_TRANSCODE_CPU_MAX_EDGE = max(360, int(os.environ.get("VIDEO_TRANSCODE_CPU_MAX_EDGE", "960")))
VIDEO_TRANSCODE_CPU_PRESET = os.environ.get("VIDEO_TRANSCODE_CPU_PRESET", "ultrafast")
VIDEO_TRANSCODE_CPU_CRF = min(35, max(18, int(os.environ.get("VIDEO_TRANSCODE_CPU_CRF", "26"))))
VIDEO_TRANSCODE_CPU_THREADS = max(1, int(os.environ.get("VIDEO_TRANSCODE_CPU_THREADS", "2")))
VIDEO_HLS_SEGMENT_SECONDS = max(1.0, float(os.environ.get("VIDEO_HLS_SEGMENT_SECONDS", "4")))
IMAGE_PREVIEW_MAX_EDGE = int(os.environ.get("IMAGE_PREVIEW_MAX_EDGE", "512"))
IMAGE_PREVIEW_MAX_EDGE_LIMIT = max(64, int(os.environ.get("IMAGE_PREVIEW_MAX_EDGE_LIMIT", "2048")))
IMAGE_PREVIEW_QUALITY = int(os.environ.get("IMAGE_PREVIEW_QUALITY", "72"))
IMAGE_PREVIEW_CONCURRENCY = max(1, int(os.environ.get("IMAGE_PREVIEW_CONCURRENCY", "2")))
IMAGE_PREVIEW_MAX_SOURCE_PIXELS = int(os.environ.get("IMAGE_PREVIEW_MAX_SOURCE_PIXELS", "60000000"))
IMAGE_PREVIEW_CACHE_DIR = os.environ.get("IMAGE_PREVIEW_CACHE_DIR", "/preview-cache")
IMAGE_PREVIEW_CACHE_MAX_BYTES = int(os.environ.get("IMAGE_PREVIEW_CACHE_MAX_BYTES", "10000000000"))
CLIENT_FILE_CACHE_CONTROL = "private, max-age=604800"
_image_preview_slots = threading.BoundedSemaphore(IMAGE_PREVIEW_CONCURRENCY)
_video_transcode_slots = threading.BoundedSemaphore(VIDEO_TRANSCODE_CONCURRENCY)
_image_preview_cache_cleanup_lock = threading.Lock()
_image_preview_cache_last_cleanup = 0.0
_IMAGE_PREVIEW_CACHE_CLEANUP_INTERVAL = 300.0
_PREVIEW_CACHE_TOUCH_INTERVAL = 3600.0
_preview_cache_touch_lock = threading.Lock()
_preview_cache_touch_times: dict[str, float] = {}
_preview_singleflight_lock = threading.Lock()
_preview_singleflight_locks: dict[str, threading.Lock] = {}
_PREVIEW_SINGLEFLIGHT_LOCK_LIMIT = 4096


def _preview_cache_key(key_data: dict) -> str:
    return hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _preview_cache_path_for_key(key: str, extension: str = ".jpg") -> str | None:
    root = _image_preview_cache_root()
    if not root:
        return None
    path = os.path.join(root, key[:2], key[2:4], f"{key}{extension}")
    if not _is_image_preview_cache_path_allowed(path, root):
        return None
    return path


def _preview_cache_headers(cache_path: str, media_type: str) -> dict[str, str]:
    stat = os.stat(cache_path)
    etag = f'"{os.path.basename(cache_path)}-{stat.st_size}"'
    return {
        "Cache-Control": CLIENT_FILE_CACHE_CONTROL,
        "ETag": etag,
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        "X-Content-Type-Options": "nosniff",
    }


def _request_has_matching_etag(request: Request | None, etag: str) -> bool:
    if request is None:
        return False
    header = request.headers.get("if-none-match", "")
    if not header:
        return False
    return any(part.strip() == etag for part in header.split(","))


def _touch_preview_cache_path(cache_path: str):
    now = time.monotonic()
    with _preview_cache_touch_lock:
        last = _preview_cache_touch_times.get(cache_path, 0.0)
        if last > 0 and now - last < _PREVIEW_CACHE_TOUCH_INTERVAL:
            return
        _preview_cache_touch_times[cache_path] = now
    try:
        os.utime(cache_path, None)
    except OSError:
        pass


def _preview_cache_response(cache_path: str | None, media_type: str = "image/jpeg", request: Request | None = None):
    if not cache_path or not os.path.isfile(cache_path):
        return None
    try:
        headers = _preview_cache_headers(cache_path, media_type)
    except OSError:
        return None
    if _request_has_matching_etag(request, headers["ETag"]):
        return Response(status_code=304, media_type=media_type, headers=headers)
    _touch_preview_cache_path(cache_path)
    return FileResponse(
        cache_path,
        media_type=media_type,
        headers=headers,
    )


def _preview_singleflight_for_key(key: str):
    with _preview_singleflight_lock:
        lock = _preview_singleflight_locks.get(key)
        if lock is None:
            if len(_preview_singleflight_locks) >= _PREVIEW_SINGLEFLIGHT_LOCK_LIMIT:
                for stale_key, stale_lock in list(_preview_singleflight_locks.items()):
                    if not stale_lock.locked():
                        _preview_singleflight_locks.pop(stale_key, None)
                    if len(_preview_singleflight_locks) < _PREVIEW_SINGLEFLIGHT_LOCK_LIMIT:
                        break
            lock = threading.Lock()
            _preview_singleflight_locks[key] = lock
        return lock


def _write_preview_cache(cache_path: str | None, body: bytes):
    if not cache_path:
        return
    try:
        if not _is_image_preview_cache_path_allowed(cache_path):
            return
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        part_path = f"{cache_path}.part"
        if not _is_image_preview_cache_path_allowed(part_path):
            return
        with open(part_path, "wb") as f:
            f.write(body)
        os.replace(part_path, cache_path)
        with _preview_cache_touch_lock:
            _preview_cache_touch_times[cache_path] = time.monotonic()
        _maybe_cleanup_image_preview_cache()
    except OSError as exc:
        logger.warning("preview cache write failed: %s", exc)
        try:
            if "part_path" in locals() and os.path.exists(part_path):
                os.remove(part_path)
        except OSError:
            pass


def _is_path_allowed(full: str) -> bool:
    full_norm = os.path.realpath(os.path.abspath(full))
    for root in PICTURES_ROOTS:
        root_norm = os.path.realpath(os.path.abspath(root))
        try:
            if os.path.commonpath([full_norm, root_norm]) == root_norm:
                return True
        except ValueError:
            continue
    return False

@router.get("/api/file")
def serve_file(path: str = Query(...)):
    full = os.path.realpath(os.path.abspath(path))
    if not _is_path_allowed(full):
        raise HTTPException(403)
    if not os.path.isfile(full):
        raise HTTPException(404)
    return FileResponse(full, headers={"Cache-Control": CLIENT_FILE_CACHE_CONTROL})


def _clamp_preview_max_edge(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise HTTPException(422, "invalid preview size")
    return max(64, min(parsed, IMAGE_PREVIEW_MAX_EDGE_LIMIT))


def _image_preview_cache_enabled(max_edge: int) -> bool:
    return (
        bool(IMAGE_PREVIEW_CACHE_DIR)
        and IMAGE_PREVIEW_CACHE_MAX_BYTES > 0
        and max_edge == IMAGE_PREVIEW_MAX_EDGE
    )


def _image_preview_cache_root() -> str | None:
    if not IMAGE_PREVIEW_CACHE_DIR:
        return None
    try:
        root = os.path.realpath(os.path.abspath(IMAGE_PREVIEW_CACHE_DIR))
        os.makedirs(root, exist_ok=True)
        return root
    except OSError as exc:
        logger.debug("image preview cache unavailable: %s", exc)
        return None


def _is_image_preview_cache_path_allowed(path: str, root: str | None = None) -> bool:
    root = root or os.path.realpath(os.path.abspath(IMAGE_PREVIEW_CACHE_DIR))
    full = os.path.realpath(os.path.abspath(path))
    try:
        return os.path.commonpath([full, root]) == root
    except ValueError:
        return False


def _image_preview_cache_path(full: str, max_edge: int) -> str | None:
    if not _image_preview_cache_enabled(max_edge):
        return None
    root = _image_preview_cache_root()
    if not root:
        return None
    try:
        stat = os.stat(full)
    except OSError:
        return None
    key_data = {
        "path": os.path.realpath(os.path.abspath(full)),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "max_edge": max_edge,
        "format": "jpeg",
        "quality": IMAGE_PREVIEW_QUALITY,
        "version": 1,
    }
    return _preview_cache_path_for_key(_preview_cache_key(key_data))


def _write_image_preview_cache(cache_path: str | None, body: bytes):
    _write_preview_cache(cache_path, body)


def _cleanup_image_preview_cache():
    root = _image_preview_cache_root()
    if not root or IMAGE_PREVIEW_CACHE_MAX_BYTES <= 0:
        return
    try:
        entries = []
        total = 0
        for current_root, _, files in os.walk(root):
            for name in files:
                if not name.endswith(".jpg"):
                    continue
                path = os.path.join(current_root, name)
                if not _is_image_preview_cache_path_allowed(path, root):
                    continue
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                entries.append((stat.st_mtime, stat.st_size, path))
                total += stat.st_size
        target_total = int(IMAGE_PREVIEW_CACHE_MAX_BYTES * 0.9)
        for _, size, path in sorted(entries):
            if total <= target_total:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass
    except OSError as exc:
        logger.warning("image preview cache cleanup failed: %s", exc)


def _maybe_cleanup_image_preview_cache():
    global _image_preview_cache_last_cleanup
    now = time.monotonic()
    if now - _image_preview_cache_last_cleanup < _IMAGE_PREVIEW_CACHE_CLEANUP_INTERVAL:
        return
    if not _image_preview_cache_cleanup_lock.acquire(blocking=False):
        return
    try:
        _image_preview_cache_last_cleanup = now
        _cleanup_image_preview_cache()
    finally:
        _image_preview_cache_cleanup_lock.release()


def _image_to_jpeg_preview(full: str, max_edge: int) -> bytes:
    try:
        with Image.open(full) as image:
            width, height = image.size
            if width <= 0 or height <= 0:
                raise HTTPException(415, "invalid image dimensions")
            if width * height > IMAGE_PREVIEW_MAX_SOURCE_PIXELS:
                raise HTTPException(413, "image is too large for preview")

            try:
                image.draft("RGB", (max_edge, max_edge))
            except OSError:
                pass

            image = ImageOps.exif_transpose(image)
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (15, 23, 42))
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")

            out = io.BytesIO()
            image.save(out, format="JPEG", quality=IMAGE_PREVIEW_QUALITY, optimize=True)
            return out.getvalue()
    except HTTPException:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise HTTPException(415, f"image preview failed: {exc}")


def _video_frame_cache_path(full: str, timestamp: float) -> str | None:
    if not IMAGE_PREVIEW_CACHE_DIR or IMAGE_PREVIEW_CACHE_MAX_BYTES <= 0:
        return None
    try:
        stat = os.stat(full)
    except OSError:
        return None
    key_data = {
        "path": os.path.realpath(os.path.abspath(full)),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "timestamp": round(float(timestamp), 3),
        "scale": "360:-2",
        "format": "mjpeg",
        "quality": 4,
        "version": 1,
    }
    return _preview_cache_path_for_key(_preview_cache_key(key_data))


def _video_frame_jpeg(full: str, timestamp: float) -> bytes:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-ss",
        f"{timestamp:g}",
        "-i",
        full,
        "-frames:v",
        "1",
        "-an",
        "-vf",
        "scale=360:-2",
        "-q:v",
        "4",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=VIDEO_PREVIEW_FRAME_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise HTTPException(503, "ffmpeg is not available")
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "video preview timed out")

    if result.returncode != 0 or not result.stdout:
        raise HTTPException(502, "video preview failed")
    return result.stdout


@router.get("/api/file/preview")
def preview_file(path: str = Query(...), max: int = IMAGE_PREVIEW_MAX_EDGE, request: Request = None):
    full = os.path.realpath(os.path.abspath(path))
    if not _is_path_allowed(full):
        raise HTTPException(403)
    if not os.path.isfile(full):
        raise HTTPException(404)

    max_edge = _clamp_preview_max_edge(max)
    cache_path = _image_preview_cache_path(full, max_edge)
    cached = _preview_cache_response(cache_path, request=request)
    if cached:
        return cached
    singleflight = _preview_singleflight_for_key(cache_path) if cache_path else None
    if singleflight:
        with singleflight:
            cached = _preview_cache_response(cache_path, request=request)
            if cached:
                return cached
            with _image_preview_slots:
                cached = _preview_cache_response(cache_path, request=request)
                if cached:
                    return cached
                body = _image_to_jpeg_preview(full, max_edge)
                _write_image_preview_cache(cache_path, body)
    else:
        with _image_preview_slots:
            body = _image_to_jpeg_preview(full, max_edge)
    return Response(
        body,
        media_type="image/jpeg",
        headers={"Cache-Control": CLIENT_FILE_CACHE_CONTROL},
    )


@router.get("/api/file/video-frame")
def video_preview_frame(path: str = Query(...), t: float = 0.1, request: Request = None):
    full = os.path.realpath(os.path.abspath(path))
    if not _is_path_allowed(full):
        raise HTTPException(403)
    if not os.path.isfile(full):
        raise HTTPException(404)

    timestamp = max(0.0, min(float(t), 30.0))
    cache_path = _video_frame_cache_path(full, timestamp)
    cached = _preview_cache_response(cache_path, request=request)
    if cached:
        return cached
    singleflight = _preview_singleflight_for_key(cache_path) if cache_path else None
    if singleflight:
        with singleflight:
            cached = _preview_cache_response(cache_path, request=request)
            if cached:
                return cached
            body = _video_frame_jpeg(full, timestamp)
            _write_preview_cache(cache_path, body)
    else:
        body = _video_frame_jpeg(full, timestamp)
    return Response(
        body,
        media_type="image/jpeg",
        headers={"Cache-Control": CLIENT_FILE_CACHE_CONTROL},
    )


def _video_compatible_filter() -> str:
    max_edge = VIDEO_COMPAT_MAX_EDGE - (VIDEO_COMPAT_MAX_EDGE % 2)
    return (
        f"scale=w='if(gte(iw,ih),min(iw,{max_edge}),-2)':"
        f"h='if(gt(ih,iw),min(ih,{max_edge}),-2)',"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        "format=yuv420p"
    )


def _video_compatible_command(full: str) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        full,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-vf",
        _video_compatible_filter(),
        "-c:v",
        "libx264",
        "-preset",
        VIDEO_COMPAT_PRESET,
        "-tune",
        "fastdecode",
        "-profile:v",
        "main",
        "-level:v",
        "4.0",
        "-crf",
        str(VIDEO_COMPAT_CRF),
        "-pix_fmt",
        "yuv420p",
        "-threads",
        str(VIDEO_COMPAT_THREADS),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof",
        "-f",
        "mp4",
        "pipe:1",
    ]


def _iter_process_stdout(process: subprocess.Popen):
    try:
        if process.stdout is None:
            return
        while True:
            chunk = process.stdout.read(VIDEO_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def _video_compatible_response(path: str, head_only: bool = False):
    full = os.path.realpath(os.path.abspath(path))
    if not _is_path_allowed(full):
        raise HTTPException(403)
    if not os.path.isfile(full):
        raise HTTPException(404)

    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if head_only:
        return Response(media_type="video/mp4", headers=headers)

    try:
        process = subprocess.Popen(
            _video_compatible_command(full),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            bufsize=0,
        )
    except FileNotFoundError:
        raise HTTPException(503, "ffmpeg is not available")
    except OSError as exc:
        raise HTTPException(502, f"video compatible stream failed: {exc}")

    return StreamingResponse(
        _iter_process_stdout(process),
        media_type="video/mp4",
        headers=headers,
    )


@router.head("/api/file/video-compatible")
def video_compatible_head(path: str = Query(...)):
    return _video_compatible_response(path, head_only=True)


@router.get("/api/file/video-compatible")
def video_compatible_stream(path: str = Query(...)):
    return _video_compatible_response(path, head_only=False)


def _video_transcode_filter(gpu_decode: bool) -> str:
    if gpu_decode:
        return f"scale_qsv=w='min(iw,{VIDEO_TRANSCODE_MAX_EDGE})':h='min(ih,{VIDEO_TRANSCODE_MAX_EDGE})':force_original_aspect_ratio=decrease"
    return f"scale=w='min(iw,{VIDEO_TRANSCODE_MAX_EDGE})':h='min(ih,{VIDEO_TRANSCODE_MAX_EDGE})':force_original_aspect_ratio=decrease,format=nv12"


def _video_transcode_cpu_filter() -> str:
    max_edge = VIDEO_TRANSCODE_CPU_MAX_EDGE - (VIDEO_TRANSCODE_CPU_MAX_EDGE % 2)
    return (
        f"scale=w='if(gte(iw,ih),min(iw,{max_edge}),-2)':"
        f"h='if(gt(ih,iw),min(ih,{max_edge}),-2)',"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        "format=yuv420p"
    )


def _video_transcode_vaapi_filter() -> str:
    max_edge = VIDEO_TRANSCODE_MAX_EDGE - (VIDEO_TRANSCODE_MAX_EDGE % 2)
    return (
        f"scale=w='if(gte(iw,ih),min(iw,{max_edge}),-2)':"
        f"h='if(gt(ih,iw),min(ih,{max_edge}),-2)',"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        "format=nv12,hwupload"
    )


def _video_transcode_config() -> dict:
    return {
        "encoder": VIDEO_TRANSCODE_ENCODER,
        "gpu_decode": VIDEO_TRANSCODE_GPU_DECODE,
        "max_edge": VIDEO_TRANSCODE_MAX_EDGE,
        "global_quality": VIDEO_TRANSCODE_QSV_GLOBAL_QUALITY,
        "preset": VIDEO_COMPAT_PRESET,
        "cpu_fallback": VIDEO_TRANSCODE_CPU_FALLBACK,
        "vaapi_fallback": VIDEO_TRANSCODE_VAAPI_FALLBACK,
        "vaapi_device": VIDEO_TRANSCODE_VAAPI_DEVICE,
        "vaapi_qp": VIDEO_TRANSCODE_VAAPI_QP,
        "cpu_max_edge": VIDEO_TRANSCODE_CPU_MAX_EDGE,
        "cpu_preset": VIDEO_TRANSCODE_CPU_PRESET,
        "cpu_crf": VIDEO_TRANSCODE_CPU_CRF,
        "cpu_threads": VIDEO_TRANSCODE_CPU_THREADS,
    }


def _video_transcode_paths(full: str) -> dict[str, str]:
    stat = os.stat(full)
    key_data = {
        "path": os.path.realpath(os.path.abspath(full)),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "config": _video_transcode_config(),
    }
    key = hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    cache_dir = os.path.realpath(os.path.abspath(VIDEO_TRANSCODE_CACHE_DIR))
    return {
        "key": key,
        "ready": os.path.join(cache_dir, f"{key}.mp4"),
        "part": os.path.join(cache_dir, f"{key}.part"),
    }


def _video_transcode_command(full: str, output_part: str, gpu_decode: bool, encoder: str | None = None) -> list[str]:
    encoder = encoder or VIDEO_TRANSCODE_ENCODER
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if encoder == "h264_qsv" and gpu_decode:
        command.extend(["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"])
    if encoder == "h264_vaapi":
        command.extend(["-vaapi_device", VIDEO_TRANSCODE_VAAPI_DEVICE])
    command.extend([
        "-i", full,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-sn",
        "-dn",
    ])
    if encoder == "h264_qsv":
        command.extend([
            "-vf", _video_transcode_filter(gpu_decode),
            "-c:v", "h264_qsv",
            "-preset", VIDEO_COMPAT_PRESET,
            "-global_quality", str(VIDEO_TRANSCODE_QSV_GLOBAL_QUALITY),
            "-look_ahead", "0",
        ])
    elif encoder == "h264_vaapi":
        command.extend([
            "-vf", _video_transcode_vaapi_filter(),
            "-c:v", "h264_vaapi",
            "-qp", str(VIDEO_TRANSCODE_VAAPI_QP),
        ])
    elif encoder == "libx264":
        command.extend([
            "-vf", _video_transcode_cpu_filter(),
            "-c:v", "libx264",
            "-preset", VIDEO_TRANSCODE_CPU_PRESET,
            "-tune", "fastdecode",
            "-profile:v", "main",
            "-level:v", "4.0",
            "-crf", str(VIDEO_TRANSCODE_CPU_CRF),
            "-pix_fmt", "yuv420p",
            "-threads", str(VIDEO_TRANSCODE_CPU_THREADS),
        ])
    else:
        raise ValueError(f"unsupported video transcode encoder: {encoder}")
    command.extend([
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-ar", "48000",
        "-movflags", "+faststart",
        "-f", "mp4",
        output_part,
    ])
    return command

def _validate_transcode_source(path: str) -> str:
    full = os.path.realpath(os.path.abspath(path))
    if not _is_path_allowed(full):
        raise HTTPException(403)
    if not os.path.isfile(full):
        raise HTTPException(404)
    return full


def _is_cache_path_allowed(path: str) -> bool:
    cache_dir = os.path.realpath(os.path.abspath(VIDEO_TRANSCODE_CACHE_DIR))
    full = os.path.realpath(os.path.abspath(path))
    try:
        return os.path.commonpath([full, cache_dir]) == cache_dir
    except ValueError:
        return False


def _video_transcode_status_for_path(full: str) -> dict:
    paths = _video_transcode_paths(full)
    if os.path.exists(paths["ready"]):
        return {"status": "ready", "key": paths["key"]}
    if os.path.exists(paths["part"]):
        return {"status": "processing", "key": paths["key"]}
    return {"status": "missing", "key": paths["key"]}


def _cleanup_transcode_cache(cache_dir: str, reserve_bytes: int = 0):
    try:
        entries = []
        total = 0
        target_total = max(0, VIDEO_TRANSCODE_CACHE_MAX_BYTES - max(0, reserve_bytes))
        for name in os.listdir(cache_dir):
            if not name.endswith(".mp4"):
                continue
            path = os.path.join(cache_dir, name)
            if not _is_cache_path_allowed(path):
                continue
            stat = os.stat(path)
            entries.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size
        for _, size, path in sorted(entries):
            if total <= target_total:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass
    except OSError:
        return


def _run_video_transcode(full: str, paths: dict[str, str]) -> dict:
    if VIDEO_TRANSCODE_ENCODER not in {"h264_qsv", "h264_vaapi"}:
        raise HTTPException(500, "only h264_qsv or h264_vaapi video transcode is supported")
    cache_dir = os.path.dirname(paths["ready"])
    os.makedirs(cache_dir, mode=0o1777, exist_ok=True)
    _cleanup_transcode_cache(cache_dir, reserve_bytes=os.path.getsize(full))
    if os.path.exists(paths["ready"]):
        return {"status": "ready", "key": paths["key"]}

    def run_once(gpu_decode: bool, encoder: str | None = None):
        subprocess.run(
            _video_transcode_command(full, paths["part"], gpu_decode=gpu_decode, encoder=encoder),
            check=True,
            capture_output=True,
            text=True,
            timeout=VIDEO_TRANSCODE_TIMEOUT,
        )

    with _video_transcode_slots:
        if os.path.exists(paths["ready"]):
            return {"status": "ready", "key": paths["key"]}
        if os.path.exists(paths["part"]):
            try:
                os.remove(paths["part"])
            except OSError:
                pass
        try:
            try:
                if VIDEO_TRANSCODE_ENCODER == "h264_vaapi":
                    run_once(False, encoder="h264_vaapi")
                elif VIDEO_TRANSCODE_GPU_DECODE == "0":
                    run_once(False)
                else:
                    try:
                        run_once(True)
                    except subprocess.CalledProcessError:
                        if VIDEO_TRANSCODE_GPU_DECODE == "auto":
                            run_once(False)
                        else:
                            raise
            except subprocess.CalledProcessError:
                if VIDEO_TRANSCODE_VAAPI_FALLBACK and VIDEO_TRANSCODE_ENCODER != "h264_vaapi":
                    try:
                        run_once(False, encoder="h264_vaapi")
                    except subprocess.CalledProcessError:
                        if not VIDEO_TRANSCODE_CPU_FALLBACK:
                            raise
                        run_once(False, encoder="libx264")
                else:
                    if not VIDEO_TRANSCODE_CPU_FALLBACK:
                        raise
                    run_once(False, encoder="libx264")
        except FileNotFoundError:
            raise HTTPException(503, "ffmpeg is not available")
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "video transcode timed out")
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr or "video transcode failed"
            raise HTTPException(502, detail)
        except OSError as exc:
            raise HTTPException(502, f"video transcode failed: {exc}")
        if not os.path.exists(paths["part"]):
            raise HTTPException(502, "video transcode produced no output")
        os.replace(paths["part"], paths["ready"])
    return {"status": "ready", "key": paths["key"]}


@router.get("/api/file/video-transcode-status")
def video_transcode_status(path: str = Query(...)):
    full = _validate_transcode_source(path)
    return _video_transcode_status_for_path(full)


@router.post("/api/file/video-transcode")
def video_transcode(path: str = Query(...)):
    full = _validate_transcode_source(path)
    paths = _video_transcode_paths(full)
    return _run_video_transcode(full, paths)


@router.get("/api/file/video-transcoded")
def video_transcoded(request: Request, path: str = Query(...)):
    full = _validate_transcode_source(path)
    paths = _video_transcode_paths(full)
    ready = paths["ready"]
    if not _is_cache_path_allowed(ready):
        raise HTTPException(403)
    if not os.path.isfile(ready):
        raise HTTPException(404)
    return _stream_existing_file_response(request, ready, "video/mp4")


def _format_hls_seconds(value: float) -> str:
    return f"{max(0.0, value):.3f}"


def _video_hls_segment_command(full: str, start: float = 0.0, duration: float | None = None) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
    ]
    start_seconds = max(0.0, float(start))
    if start_seconds > 0:
        command.extend(["-ss", f"{start_seconds:g}"])
    if duration is not None:
        command.extend(["-t", f"{max(0.1, float(duration)):g}"])
    command.extend([
        "-i",
        full,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-vf",
        _video_transcode_cpu_filter(),
        "-c:v",
        "libx264",
        "-preset",
        VIDEO_TRANSCODE_CPU_PRESET,
        "-tune",
        "fastdecode",
        "-profile:v",
        "main",
        "-level:v",
        "4.0",
        "-crf",
        str(VIDEO_TRANSCODE_CPU_CRF),
        "-pix_fmt",
        "yuv420p",
        "-threads",
        str(VIDEO_TRANSCODE_CPU_THREADS),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-muxdelay",
        "0",
        "-muxpreload",
        "0",
        "-f",
        "mpegts",
        "pipe:1",
    ])
    return command


def _probe_video_duration_seconds(full: str) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                full,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        duration = float(result.stdout.strip())
    except (FileNotFoundError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return duration


@router.get("/api/file/video-hls")
def video_hls_playlist(path: str = Query(...)):
    full = os.path.realpath(os.path.abspath(path))
    if not _is_path_allowed(full):
        raise HTTPException(403)
    if not os.path.isfile(full):
        raise HTTPException(404)

    duration = _probe_video_duration_seconds(full) or 600.0
    segment_seconds = VIDEO_HLS_SEGMENT_SECONDS
    target_duration = max(1, math.ceil(segment_seconds))
    segment_count = max(1, math.ceil(duration / segment_seconds))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    for index in range(segment_count):
        start = index * segment_seconds
        segment_duration = min(segment_seconds, max(0.0, duration - start))
        if segment_duration <= 0:
            break
        if index > 0:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{segment_duration:.3f},")
        lines.append(
            "/api/file/video-hls-segment?path="
            + quote(full, safe="")
            + f"&start={_format_hls_seconds(start)}&duration={_format_hls_seconds(segment_duration)}"
        )
    lines.extend(["#EXT-X-ENDLIST", ""])
    body = "\n".join(lines)
    return Response(
        body,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/file/video-hls-segment")
def video_hls_segment(path: str = Query(...), start: float = Query(0.0), duration: float = Query(VIDEO_HLS_SEGMENT_SECONDS)):
    full = os.path.realpath(os.path.abspath(path))
    if not _is_path_allowed(full):
        raise HTTPException(403)
    if not os.path.isfile(full):
        raise HTTPException(404)

    segment_start = max(0.0, float(start))
    segment_duration = min(max(0.1, float(duration)), VIDEO_HLS_SEGMENT_SECONDS * 2)
    try:
        process = subprocess.Popen(
            _video_hls_segment_command(full, start=segment_start, duration=segment_duration),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            bufsize=0,
        )
    except FileNotFoundError:
        raise HTTPException(503, "ffmpeg is not available")
    except OSError as exc:
        raise HTTPException(502, f"video hls stream failed: {exc}")

    return StreamingResponse(
        _iter_process_stdout(process),
        media_type="video/mp2t",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def _media_type_for_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    mime_map = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "jpe": "image/jpeg", "jfif": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
        "avif": "image/avif", "heic": "image/heic", "heif": "image/heif",
        "mp4": "video/mp4", "m4v": "video/mp4", "webm": "video/webm",
        "mov": "video/quicktime", "mkv": "video/x-matroska",
        "avi": "video/x-msvideo", "wmv": "video/x-ms-wmv",
        "mpg": "video/mpeg", "mpeg": "video/mpeg", "ts": "video/mp2t",
        "m2ts": "video/mp2t", "flv": "video/x-flv", "3gp": "video/3gpp",
    }
    return mime_map.get(ext, "application/octet-stream")


def _parse_byte_range(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    if not range_header:
        return None
    if not range_header.startswith("bytes="):
        raise ValueError("unsupported range unit")
    spec = range_header[6:].strip()
    if "," in spec or "-" not in spec:
        raise ValueError("unsupported range")
    start_text, end_text = spec.split("-", 1)
    if not start_text:
        if not end_text:
            raise ValueError("empty range")
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError("invalid suffix range")
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else file_size - 1
    if file_size <= 0 or start < 0 or start >= file_size or end < start:
        raise ValueError("unsatisfiable range")
    return start, min(end, file_size - 1)


def _stream_existing_file_response(request: Request, full: str, media_type: str, head_only: bool = False):
    file_size = os.path.getsize(full)
    base_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    try:
        byte_range = _parse_byte_range(request.headers.get("range"), file_size)
    except ValueError:
        return Response(
            status_code=416,
            media_type=media_type,
            headers={
                **base_headers,
                "Content-Range": f"bytes */{file_size}",
            },
        )

    start, end = byte_range if byte_range else (0, file_size - 1)

    if head_only:
        if byte_range:
            return Response(
                status_code=206,
                media_type=media_type,
                headers={
                    **base_headers,
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Length": str(end - start + 1),
                },
            )
        return Response(
            media_type=media_type,
            headers={**base_headers, "Content-Length": str(file_size)},
        )

    def iterfile_range():
        remaining = max(0, end - start + 1)
        with open(full, "rb") as f:
            f.seek(start)
            while remaining > 0:
                chunk = f.read(min(VIDEO_STREAM_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    if byte_range:
        headers = {
            **base_headers,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(end - start + 1),
        }
        return StreamingResponse(
            iterfile_range(),
            status_code=206,
            media_type=media_type,
            headers=headers,
        )

    return StreamingResponse(
        iterfile_range(),
        media_type=media_type,
        headers={**base_headers, "Content-Length": str(file_size)},
    )


def _stream_file_response(request: Request, path: str, head_only: bool = False):
    full = os.path.realpath(os.path.abspath(path))
    if not _is_path_allowed(full):
        raise HTTPException(403)
    if not os.path.isfile(full):
        raise HTTPException(404)
    return _stream_existing_file_response(request, full, _media_type_for_path(path), head_only=head_only)


@router.head("/api/file/stream")
def stream_file_head(request: Request, path: str = Query(...)):
    return _stream_file_response(request, path, head_only=True)


@router.get("/api/file/stream")
def stream_file(request: Request, path: str = Query(...)):
    return _stream_file_response(request, path, head_only=False)
