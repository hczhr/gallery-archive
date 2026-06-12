import argparse
from multiprocessing import Process, Queue
import os
from queue import Empty
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "gallery.db"


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _db_parts(db_path: Path) -> list[Path]:
    return [db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")]


def backup(db_path: Path, backup_dir: Path | None = None) -> Path:
    db_path = db_path.resolve()
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    target_dir = backup_dir or db_path.parent / "db-backups" / _timestamp()
    target_dir.mkdir(parents=True, exist_ok=False)
    for part in _db_parts(db_path):
        if part.exists():
            shutil.copy2(part, target_dir / part.name)
    print(target_dir)
    return target_dir


def _run_quick_check_direct(db_path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    try:
        return [row[0] for row in conn.execute("PRAGMA quick_check").fetchall()]
    finally:
        conn.close()


def _quick_check_worker(db_path: Path, queue) -> None:
    try:
        queue.put(("rows", _run_quick_check_direct(db_path)))
    except sqlite3.DatabaseError as exc:
        queue.put(("error", str(exc)))
    except Exception as exc:
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _run_quick_check_with_timeout(
    db_path: Path,
    timeout: float,
    worker=_quick_check_worker,
) -> tuple[bool, list[str], str | None]:
    timeout = max(0.0, float(timeout))
    result_queue = Queue()
    process = Process(target=worker, args=(db_path, result_queue))
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
        result_queue.close()
        result_queue.join_thread()
        return False, [], f"quick_check timed out after {timeout:g}s"

    try:
        kind, payload = result_queue.get(timeout=1)
    except Empty:
        if process.exitcode == 0:
            error = "quick_check worker exited without result"
        else:
            error = f"quick_check worker exited with code {process.exitcode}"
        return False, [], error
    finally:
        result_queue.close()
        result_queue.join_thread()

    if kind == "error":
        return False, [], payload
    if kind != "rows":
        return False, [], f"quick_check worker returned unknown result kind: {kind}"
    return True, payload, None


def quick_check(db_path: Path, timeout: float) -> bool:
    completed, rows, error = _run_quick_check_with_timeout(db_path, timeout)
    if error:
        print(f"ERROR: {error}", file=sys.stderr)
        return False
    for row in rows:
        print(row)
    return completed and len(rows) == 1 and rows[0].lower() == "ok"


def recover(db_path: Path, output_path: Path, sqlite_bin: str) -> None:
    if output_path.exists():
        raise SystemExit(f"Output already exists: {output_path}")
    first = subprocess.Popen(
        [sqlite_bin, str(db_path), ".recover"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        [sqlite_bin, str(output_path)],
        stdin=first.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert first.stdout is not None
    first.stdout.close()
    second_stdout, second_stderr = second.communicate()
    first_stderr = first.stderr.read() if first.stderr else ""
    first_rc = first.wait()
    if first_rc != 0 or second.returncode != 0:
        if output_path.exists():
            output_path.unlink()
        raise SystemExit(
            "recover failed\n"
            f"recover rc={first_rc}: {first_stderr}\n"
            f"import rc={second.returncode}: {second_stderr}\n"
            f"{second_stdout}"
        )
    if not quick_check(output_path, timeout=120):
        raise SystemExit(f"Recovered DB quick_check failed: {output_path}")
    print(output_path)


def replace(db_path: Path, recovered_path: Path, yes: bool) -> None:
    if not yes:
        raise SystemExit("Refusing to replace without --yes")
    if not recovered_path.exists():
        raise SystemExit(f"Recovered DB not found: {recovered_path}")
    backup(db_path)
    malformed = db_path.with_name(f"{db_path.name}.malformed-{_timestamp()}")
    if db_path.exists():
        os.replace(db_path, malformed)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            os.replace(sidecar, malformed.with_name(malformed.name + suffix))
    os.replace(recovered_path, db_path)
    print(db_path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Safely check, recover, or replace gallery.db")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="cmd", required=True)

    check_cmd = sub.add_parser("check")
    check_cmd.add_argument("--timeout", type=float, default=60)

    backup_cmd = sub.add_parser("backup")
    backup_cmd.add_argument("--backup-dir", type=Path)

    recover_cmd = sub.add_parser("recover")
    recover_cmd.add_argument("--out", type=Path, required=True)
    recover_cmd.add_argument("--sqlite", default="sqlite3")

    replace_cmd = sub.add_parser("replace")
    replace_cmd.add_argument("--recovered", type=Path, required=True)
    replace_cmd.add_argument("--yes", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "check":
        return 0 if quick_check(args.db, args.timeout) else 1
    if args.cmd == "backup":
        backup(args.db, args.backup_dir)
        return 0
    if args.cmd == "recover":
        recover(args.db, args.out, args.sqlite)
        return 0
    if args.cmd == "replace":
        replace(args.db, args.recovered, args.yes)
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
