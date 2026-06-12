from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import database
from app.db_lifecycle import (
    DEFAULT_MISSING_RETENTION_DAYS,
    DEFAULT_SCAN_CANDIDATE_RETENTION_DAYS,
    DEFAULT_SCAN_SEEN_RETENTION_DAYS,
    cleanup_database_lifecycle,
)


DEFAULT_DB = ROOT / "data" / "gallery.db"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Database lifecycle maintenance")
    sub = parser.add_subparsers(dest="command", required=True)

    cleanup = sub.add_parser("cleanup", help="Prune old missing and scan history rows")
    cleanup.add_argument("--db", type=Path, default=DEFAULT_DB)
    cleanup.add_argument("--dry-run", action="store_true", help="Print cleanup counts without mutating")
    cleanup.add_argument("--execute", action="store_true", help="Apply cleanup instead of dry-run")
    cleanup.add_argument("--vacuum", action="store_true", help="Run SQLite VACUUM after cleanup")
    cleanup.add_argument("--backup-root", type=Path)
    cleanup.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    cleanup.add_argument("--missing-days", type=int, default=DEFAULT_MISSING_RETENTION_DAYS)
    cleanup.add_argument("--scan-seen-days", type=int, default=DEFAULT_SCAN_SEEN_RETENTION_DAYS)
    cleanup.add_argument("--scan-candidate-days", type=int, default=DEFAULT_SCAN_CANDIDATE_RETENTION_DAYS)
    return parser


def _format_summary(result: dict) -> str:
    mode = "dry-run" if result["dry_run"] else "execute"
    lines = [
        f"mode: {mode}",
        f"missing items: {result['items']}",
        f"item tag links: {result['item_tags']}",
        f"protected missing items: {result['protected_items']}",
        f"scan_seen rows: {result['scan_seen']}",
        f"resolved scan_candidates: {result['scan_candidates']}",
    ]
    if result.get("backup_dir"):
        lines.append(f"backup: {result['backup_dir']}")
    if result.get("vacuumed"):
        lines.append("vacuum: yes")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "cleanup":
        if args.dry_run and args.execute:
            parser.error("--dry-run cannot be combined with --execute")
        if args.vacuum and not args.execute:
            parser.error("--vacuum requires --execute")
        db_path = args.db.resolve()
        if not db_path.exists():
            raise SystemExit(f"DB not found: {db_path}")

        database.close_db()
        database.DB_PATH = str(db_path)
        database.DATA_DIR = str(db_path.parent)
        result = cleanup_database_lifecycle(
            missing_retention_days=args.missing_days,
            scan_seen_retention_days=args.scan_seen_days,
            scan_candidate_retention_days=args.scan_candidate_days,
            execute=args.execute,
            backup_before=True,
            backup_root=args.backup_root,
            vacuum=args.vacuum,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(_format_summary(result))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
