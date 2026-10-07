import os
import sys
import json
import re
import sqlite3
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

BACKUP_FILENAME_REGEX = re.compile(r"^watcher_\d{8}T\d{6}Z\.sqlite$")

def prune_local_backups(backup_dir: Path, keep_count: int = 7) -> List[Path]:
    """
    Retains the newest keep_count validated backup files.
    Strictly matches only files named watcher_<timestamp>.sqlite.
    Never touches live database files (watcher.sqlite, WAL, SHM).
    """
    removed = []
    if not backup_dir.exists():
        return removed

    candidates = [
        f for f in backup_dir.iterdir()
        if f.is_file() and BACKUP_FILENAME_REGEX.match(f.name)
    ]
    # Sort descending by filename (ISO timestamp ensures chronological sort)
    candidates.sort(key=lambda x: x.name, reverse=True)

    to_prune = candidates[keep_count:]
    for old_file in to_prune:
        try:
            old_file.unlink()
            removed.append(old_file)
            logger.info("Pruned old local backup: %s", old_file.name)
        except OSError as e:
            logger.warning("Failed to remove old backup %s: %s", old_file.name, e)

    return removed

def create_snapshot(
    source_db: str = "/app/data/watcher.sqlite",
    backup_dir: str = "/app/data/backups",
    retention_count: int = 7
) -> Dict[str, Any]:
    """
    Creates an application-consistent SQLite snapshot using sqlite3.Connection.backup().
    Validates PRAGMA integrity_check == 'ok' and size > 0 before publishing.
    """
    src_path = Path(source_db).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"Source database not found: {src_path}")

    dst_dir = Path(backup_dir).resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_filename = f"watcher_{timestamp_str}.sqlite"
    final_path = dst_dir / final_filename
    temp_path = dst_dir / f".tmp_{final_filename}"

    try:
        # 1. Open source and destination connections
        src_conn = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(str(temp_path))

        # 2. Perform live database backup via sqlite3 backup API
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        # 3. Re-open snapshot read-only to verify PRAGMA integrity_check
        check_conn = sqlite3.connect(f"file:{temp_path}?mode=ro", uri=True)
        try:
            cursor = check_conn.execute("PRAGMA integrity_check;")
            integrity_result = cursor.fetchone()
            if not integrity_result or integrity_result[0] != "ok":
                raise ValueError(f"Snapshot integrity check failed: {integrity_result}")
        finally:
            check_conn.close()

        # 4. Verify file size > 0
        file_size = temp_path.stat().st_size
        if file_size <= 0:
            raise ValueError(f"Snapshot file size is zero: {temp_path}")

        # 5. Atomically promote temp snapshot to final validated name
        temp_path.replace(final_path)

        # 6. Apply local retention lifecycle
        prune_local_backups(dst_dir, keep_count=retention_count)

        result = {
            "status": "ok",
            "path": str(final_path),
            "size": file_size,
            "timestamp": timestamp_str
        }
        return result

    except Exception:
        # Clean up unvalidated temp snapshot on any failure
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise

def main():
    parser = argparse.ArgumentParser(description="TPO-Watcher In-Container SQLite Snapshot Tool")
    parser.add_argument(
        "--source",
        default=os.environ.get("DB_PATH", "/app/data/watcher.sqlite"),
        help="Path to source SQLite database"
    )
    parser.add_argument(
        "--dest-dir",
        default="/app/data/backups",
        help="Directory to store snapshots"
    )
    parser.add_argument(
        "--retention",
        type=int,
        default=7,
        help="Number of local backups to retain"
    )

    args = parser.parse_args()

    try:
        result = create_snapshot(
            source_db=args.source,
            backup_dir=args.dest_dir,
            retention_count=args.retention
        )
        # Emit exactly one machine-readable JSON result to stdout
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()
        sys.exit(0)
    except Exception as e:
        err_result = {
            "status": "error",
            "error": str(e)
        }
        sys.stderr.write(json.dumps(err_result) + "\n")
        sys.stderr.flush()
        sys.exit(1)

if __name__ == "__main__":
    main()
