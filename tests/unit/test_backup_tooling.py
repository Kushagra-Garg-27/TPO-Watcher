import os
import re
import json
import sqlite3
import tempfile
import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.database.repository import DatabaseRepository
from app.backup.snapshot import create_snapshot, prune_local_backups, BACKUP_FILENAME_REGEX
from scripts.backup_sqlite import execute_host_backup
from scripts.restore_test import validate_snapshot

@pytest.fixture
def sample_db(tmp_path):
    from datetime import datetime, timezone, timedelta
    db_path = str(tmp_path / "watcher.sqlite")
    repo = DatabaseRepository(db_path)
    repo.set_baseline_initialized()
    user_id = repo.users.create_user("backup_test@vit.edu", 2028, "VIT_CSE")
    repo.tokens.create_token(user_id, "testhash", "SIGNUP_VERIFY", datetime.now(timezone.utc) + timedelta(hours=24))
    return db_path

# 1. Valid backup
def test_valid_backup(sample_db, tmp_path):
    backup_dir = str(tmp_path / "backups")
    res = create_snapshot(source_db=sample_db, backup_dir=backup_dir)
    assert res["status"] == "ok"
    assert res["size"] > 0
    snapshot_path = Path(res["path"])
    assert snapshot_path.exists()
    assert BACKUP_FILENAME_REGEX.match(snapshot_path.name)

    # Verify PRAGMA integrity_check
    with sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"

# 2. Corruption/integrity failure
def test_corruption_integrity_failure(tmp_path):
    corrupt_db = tmp_path / "corrupt.sqlite"
    # Write invalid SQLite header bytes
    corrupt_db.write_bytes(b"INVALID_SQLITE_DATA_PADDING_FOR_FAILURE" * 10)

    backup_dir = str(tmp_path / "backups")
    with pytest.raises(Exception):
        create_snapshot(source_db=str(corrupt_db), backup_dir=backup_dir)

    # Ensure no leftover temp files
    if Path(backup_dir).exists():
        tmp_files = list(Path(backup_dir).glob(".tmp_*"))
        assert len(tmp_files) == 0

# 3. No upload after snapshot failure
def test_no_upload_after_snapshot_failure():
    with patch("scripts.backup_sqlite.run_cmd") as mock_run:
        # Simulate snapshot command failure
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "exec"],
            returncode=1,
            stdout="",
            stderr="Database lock error"
        )

        with pytest.raises(RuntimeError, match="In-container snapshot failed"):
            execute_host_backup(bucket="my-bucket")

        # Verify aws s3 cp was NEVER called
        aws_calls = [c for c in mock_run.call_args_list if "aws" in c[0][0]]
        assert len(aws_calls) == 0

# 4. Upload failure
def test_upload_failure(tmp_path):
    fake_path = "/app/data/backups/watcher_20261007T120000Z.sqlite"
    snap_meta = {"status": "ok", "path": fake_path, "size": 1024, "timestamp": "20261007T120000Z"}

    def fake_run(args, check=True):
        if "docker" in args and "exec" in args:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(snap_meta), stderr="")
        if "docker" in args and "cp" in args:
            dest = args[3]
            Path(dest).write_bytes(b"x" * 1024)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "aws" in args and "s3" in args and "cp" in args:
            raise subprocess.CalledProcessError(1, args, stderr="S3 PutObject AccessDenied")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with patch("scripts.backup_sqlite.run_cmd", side_effect=fake_run):
        with pytest.raises(subprocess.CalledProcessError):
            execute_host_backup(bucket="my-bucket")

# 5. Remote object missing
def test_remote_object_missing():
    fake_path = "/app/data/backups/watcher_20261007T120000Z.sqlite"
    snap_meta = {"status": "ok", "path": fake_path, "size": 512, "timestamp": "20261007T120000Z"}

    def fake_run(args, check=True):
        if "docker" in args and "exec" in args:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(snap_meta), stderr="")
        if "docker" in args and "cp" in args:
            dest = args[3]
            Path(dest).write_bytes(b"x" * 512)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "aws" in args and "s3" in args and "cp" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "aws" in args and "s3api" in args and "list-objects-v2" in args:
            # Empty contents
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({}), stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with patch("scripts.backup_sqlite.run_cmd", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="Verification failed: Key .* not found"):
            execute_host_backup(bucket="my-bucket")

# 6. Remote size mismatch
def test_remote_size_mismatch():
    fake_path = "/app/data/backups/watcher_20261007T120000Z.sqlite"
    snap_meta = {"status": "ok", "path": fake_path, "size": 512, "timestamp": "20261007T120000Z"}

    def fake_run(args, check=True):
        if "docker" in args and "exec" in args:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(snap_meta), stderr="")
        if "docker" in args and "cp" in args:
            dest = args[3]
            Path(dest).write_bytes(b"x" * 512)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "aws" in args and "s3" in args and "cp" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "aws" in args and "s3api" in args and "list-objects-v2" in args:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({
                "Contents": [{"Key": "backups/watcher_20261007T120000Z.sqlite", "Size": 256}]
            }), stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with patch("scripts.backup_sqlite.run_cmd", side_effect=fake_run):
        with pytest.raises(ValueError, match="Remote size .* does not match local size"):
            execute_host_backup(bucket="my-bucket")

# 7. Temp cleanup on success
def test_temp_cleanup_on_success():
    fake_path = "/app/data/backups/watcher_20261007T120000Z.sqlite"
    snap_meta = {"status": "ok", "path": fake_path, "size": 512, "timestamp": "20261007T120000Z"}
    created_temp_dirs = []

    orig_mkdtemp = tempfile.mkdtemp
    def tracked_mkdtemp(*args, **kwargs):
        d = orig_mkdtemp(*args, **kwargs)
        created_temp_dirs.append(d)
        return d

    def fake_run(args, check=True):
        if "docker" in args and "exec" in args:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(snap_meta), stderr="")
        if "docker" in args and "cp" in args:
            dest = args[3]
            Path(dest).write_bytes(b"x" * 512)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "aws" in args and "s3" in args and "cp" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "aws" in args and "s3api" in args and "list-objects-v2" in args:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({
                "Contents": [{"Key": "backups/watcher_20261007T120000Z.sqlite", "Size": 512}]
            }), stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with patch("tempfile.mkdtemp", side_effect=tracked_mkdtemp), \
         patch("scripts.backup_sqlite.run_cmd", side_effect=fake_run):
        res = execute_host_backup(bucket="my-bucket")
        assert res["status"] == "success"

    assert len(created_temp_dirs) == 1
    assert not os.path.exists(created_temp_dirs[0])

# 8. Temp cleanup on failure
def test_temp_cleanup_on_failure():
    fake_path = "/app/data/backups/watcher_20261007T120000Z.sqlite"
    snap_meta = {"status": "ok", "path": fake_path, "size": 512, "timestamp": "20261007T120000Z"}
    created_temp_dirs = []

    orig_mkdtemp = tempfile.mkdtemp
    def tracked_mkdtemp(*args, **kwargs):
        d = orig_mkdtemp(*args, **kwargs)
        created_temp_dirs.append(d)
        return d

    def fake_run(args, check=True):
        if "docker" in args and "exec" in args:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(snap_meta), stderr="")
        if "docker" in args and "cp" in args:
            dest = args[3]
            Path(dest).write_bytes(b"x" * 512)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "aws" in args:
            raise RuntimeError("Network timeout")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with patch("tempfile.mkdtemp", side_effect=tracked_mkdtemp), \
         patch("scripts.backup_sqlite.run_cmd", side_effect=fake_run):
        with pytest.raises(RuntimeError):
            execute_host_backup(bucket="my-bucket")

    assert len(created_temp_dirs) == 1
    assert not os.path.exists(created_temp_dirs[0])

# 9. Local retention keeps newest 7
def test_local_retention_keeps_newest_7(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Create 10 backup files with ascending timestamps
    created_files = []
    for day in range(1, 11):
        filename = f"watcher_202610{day:02d}T020000Z.sqlite"
        f = backup_dir / filename
        f.write_text("data")
        created_files.append(f)

    pruned = prune_local_backups(backup_dir, keep_count=7)
    assert len(pruned) == 3

    remaining = sorted([f.name for f in backup_dir.iterdir()])
    assert len(remaining) == 7
    # Oldest 3 (01, 02, 03) must be deleted
    assert "watcher_20261001T020000Z.sqlite" not in remaining
    assert "watcher_20261002T020000Z.sqlite" not in remaining
    assert "watcher_20261003T020000Z.sqlite" not in remaining
    # Newest 7 (04 to 10) retained
    assert "watcher_20261010T020000Z.sqlite" in remaining

# 10. Retention never touches live DB/WAL/SHM
def test_retention_never_touches_live_db_wal_shm(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    live_db = backup_dir / "watcher.sqlite"
    live_wal = backup_dir / "watcher.sqlite-wal"
    live_shm = backup_dir / "watcher.sqlite-shm"
    temp_snap = backup_dir / ".tmp_watcher_20261010T020000Z.sqlite"

    live_db.write_text("live db")
    live_wal.write_text("wal data")
    live_shm.write_text("shm data")
    temp_snap.write_text("temp data")

    # Plus 8 valid backups
    for i in range(8):
        (backup_dir / f"watcher_2026100{i+1}T020000Z.sqlite").write_text("data")

    prune_local_backups(backup_dir, keep_count=7)

    # Live and temp files must remain untouched
    assert live_db.exists()
    assert live_wal.exists()
    assert live_shm.exists()
    assert temp_snap.exists()

# 11. No credentials emitted in logs
def test_no_credentials_emitted_in_logs(caplog):
    caplog.clear()
    with patch("scripts.backup_sqlite.run_cmd") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "exec"],
            returncode=1,
            stdout="",
            stderr="Standard failure"
        )
        with pytest.raises(RuntimeError):
            execute_host_backup(bucket="my-bucket")

    log_text = caplog.text
    for secret_keyword in ["AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "secret", "password", "token="]:
        assert secret_keyword not in log_text.lower()

# 12. Restore-validation success
def test_restore_validation_success(sample_db):
    assert validate_snapshot(sample_db) is True

# 13. Restore-validation failure
def test_restore_validation_failure(tmp_path):
    bad_db = tmp_path / "bad.sqlite"
    # Create empty SQLite db missing application tables
    conn = sqlite3.connect(str(bad_db))
    conn.execute("CREATE TABLE some_random_table (id INT);")
    conn.commit()
    conn.close()

    assert validate_snapshot(str(bad_db)) is False
