"""
Unit tests for optional Healthchecks.io check-in monitoring.
Tests mock urllib.request.urlopen to guarantee hermetic execution with zero real network traffic.
Verifies watcher and backup pings, endpoint failure immunity, and disabled monitoring paths.
"""

import os
import json
import tempfile
import urllib.request
import urllib.error
import pytest
from unittest.mock import patch, MagicMock, Mock
from app.config import settings
from app.monitoring.checkin import send_healthcheck_ping
from app.monitoring.watcher import PlacementWatcher
from app.database.repository import DatabaseRepository
from scripts.backup_sqlite import (
    load_checkin_url,
    send_healthcheck_ping as backup_send_healthcheck_ping,
    execute_host_backup
)

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_hc.sqlite"
    repo = DatabaseRepository(str(db_file))
    repo.set_baseline_initialized()
    return str(db_file)

class TestCheckinPingHelper:
    def test_send_healthcheck_ping_success(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = send_healthcheck_ping("https://hc-ping.test/test-uuid", success=True)
            assert result is True
            assert mock_urlopen.call_count == 1
            call_req = mock_urlopen.call_args[0][0]
            assert call_req.full_url == "https://hc-ping.test/test-uuid"

    def test_send_healthcheck_ping_failure(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = send_healthcheck_ping("https://hc-ping.test/test-uuid", success=False)
            assert result is True
            assert mock_urlopen.call_count == 1
            call_req = mock_urlopen.call_args[0][0]
            assert call_req.full_url == "https://hc-ping.test/test-uuid/fail"

    def test_send_healthcheck_ping_trailing_slash_handled(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = send_healthcheck_ping("https://hc-ping.test/test-uuid/", success=False)
            assert result is True
            call_req = mock_urlopen.call_args[0][0]
            assert call_req.full_url == "https://hc-ping.test/test-uuid/fail"

    def test_send_healthcheck_ping_disabled(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            assert send_healthcheck_ping(None, success=True) is False
            assert send_healthcheck_ping("", success=True) is False
            assert send_healthcheck_ping("   ", success=False) is False
            assert mock_urlopen.call_count == 0

    def test_send_healthcheck_ping_endpoint_unavailable(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            result = send_healthcheck_ping("https://hc-ping.test/test-uuid", success=True)
            assert result is False

    def test_send_healthcheck_ping_timeout(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
            result = send_healthcheck_ping("https://hc-ping.test/test-uuid", success=True)
            assert result is False


class TestWatcherHealthchecks:
    @pytest.mark.asyncio
    async def test_watcher_success_ping(self, temp_db, monkeypatch):
        monkeypatch.setattr(settings, "HEALTHCHECK_WATCHER_URL", "https://hc-ping.test/watcher-checkin")

        watcher = PlacementWatcher(db_path=temp_db)
        from unittest.mock import AsyncMock
        watcher.auth.get_valid_page = AsyncMock(return_value=MagicMock())
        watcher.delivery_worker.process_batch_once = AsyncMock(return_value=0)
        watcher._process_notifications = AsyncMock(return_value=None)

        with patch("app.monitoring.watcher.TPOClient") as mock_client_cls, \
             patch("urllib.request.urlopen") as mock_urlopen:
            mock_client = MagicMock()
            mock_client.fetch_companies = AsyncMock(return_value=[])
            mock_client_cls.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = await watcher.check_once()
            assert result is True
            assert mock_urlopen.call_count == 1
            call_req = mock_urlopen.call_args[0][0]
            assert call_req.full_url == "https://hc-ping.test/watcher-checkin"

    @pytest.mark.asyncio
    async def test_watcher_failure_ping(self, temp_db, monkeypatch):
        monkeypatch.setattr(settings, "HEALTHCHECK_WATCHER_URL", "https://hc-ping.test/watcher-checkin")

        watcher = PlacementWatcher(db_path=temp_db)
        from unittest.mock import AsyncMock
        watcher.auth.get_valid_page = AsyncMock(side_effect=RuntimeError("Database connection failure"))

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = await watcher.check_once()
            assert result is False
            assert mock_urlopen.call_count == 1
            call_req = mock_urlopen.call_args[0][0]
            assert call_req.full_url == "https://hc-ping.test/watcher-checkin/fail"

    @pytest.mark.asyncio
    async def test_watcher_monitoring_disabled(self, temp_db, monkeypatch):
        monkeypatch.setattr(settings, "HEALTHCHECK_WATCHER_URL", None)

        watcher = PlacementWatcher(db_path=temp_db)
        from unittest.mock import AsyncMock
        watcher.auth.get_valid_page = AsyncMock(return_value=MagicMock())
        watcher.delivery_worker.process_batch_once = AsyncMock(return_value=0)
        watcher._process_notifications = AsyncMock(return_value=None)

        with patch("app.monitoring.watcher.TPOClient") as mock_client_cls, \
             patch("urllib.request.urlopen") as mock_urlopen:
            mock_client = MagicMock()
            mock_client.fetch_companies = AsyncMock(return_value=[])
            mock_client_cls.return_value = mock_client

            result = await watcher.check_once()
            assert result is True
            assert mock_urlopen.call_count == 0

    @pytest.mark.asyncio
    async def test_watcher_ping_exception_does_not_alter_success_result(self, temp_db, monkeypatch):
        monkeypatch.setattr(settings, "HEALTHCHECK_WATCHER_URL", "https://hc-ping.test/watcher-checkin")

        watcher = PlacementWatcher(db_path=temp_db)
        from unittest.mock import AsyncMock
        watcher.auth.get_valid_page = AsyncMock(return_value=MagicMock())
        watcher.delivery_worker.process_batch_once = AsyncMock(return_value=0)
        watcher._process_notifications = AsyncMock(return_value=None)

        with patch("app.monitoring.watcher.TPOClient") as mock_client_cls, \
             patch("urllib.request.urlopen", side_effect=urllib.error.URLError("DNS lookup failed")):
            mock_client = MagicMock()
            mock_client.fetch_companies = AsyncMock(return_value=[])
            mock_client_cls.return_value = mock_client

            # Must succeed despite healthcheck ping exception
            result = await watcher.check_once()
            assert result is True


class TestBackupHealthchecks:
    def test_load_checkin_url_json(self, tmp_path):
        cfg = tmp_path / "hc.json"
        cfg.write_text(json.dumps({"healthcheck_url": "https://hc-ping.test/json-backup"}))
        assert load_checkin_url(str(cfg)) == "https://hc-ping.test/json-backup"

    def test_load_checkin_url_env_format(self, tmp_path):
        cfg = tmp_path / "hc.conf"
        cfg.write_text("HEALTHCHECK_BACKUP_URL=https://hc-ping.test/env-backup\n")
        assert load_checkin_url(str(cfg)) == "https://hc-ping.test/env-backup"

    def test_load_checkin_url_raw_url(self, tmp_path):
        cfg = tmp_path / "hc.txt"
        cfg.write_text("https://hc-ping.test/raw-backup\n")
        assert load_checkin_url(str(cfg)) == "https://hc-ping.test/raw-backup"

    def test_load_checkin_url_missing_file(self):
        assert load_checkin_url("/nonexistent/file/path") is None
        assert load_checkin_url(None) is None

    def test_backup_success_ping(self, tmp_path):
        dummy_file = tmp_path / "watcher_test.sqlite"
        dummy_file.write_bytes(b"SQLite format 3\x00test")
        file_size = dummy_file.stat().st_size

        snapshot_meta = json.dumps({"status": "ok", "path": "/app/data/backups/test.sqlite", "size": file_size})
        list_response = json.dumps({"Contents": [{"Key": "backups/test.sqlite", "Size": file_size}]})

        def mock_run_cmd(args, check=True):
            if args[0] == "docker" and args[1] == "exec":
                return MagicMock(returncode=0, stdout=snapshot_meta, stderr="")
            elif args[0] == "docker" and args[1] == "cp":
                dest = args[3]
                with open(dest, "wb") as f:
                    f.write(dummy_file.read_bytes())
                return MagicMock(returncode=0, stdout="", stderr="")
            elif args[0] == "aws" and args[1] == "s3" and args[2] == "cp":
                return MagicMock(returncode=0, stdout="upload ok", stderr="")
            elif args[0] == "aws" and args[1] == "s3api":
                return MagicMock(returncode=0, stdout=list_response, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("scripts.backup_sqlite.run_cmd", side_effect=mock_run_cmd), \
             patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = execute_host_backup(
                bucket="test-bucket",
                checkin_url="https://hc-ping.test/backup-checkin"
            )
            assert result["status"] == "success"
            assert mock_urlopen.call_count == 1
            call_req = mock_urlopen.call_args[0][0]
            assert call_req.full_url == "https://hc-ping.test/backup-checkin"

    def test_backup_failure_ping(self):
        def mock_failing_cmd(args, check=True):
            return MagicMock(returncode=1, stdout="", stderr="Container not running")

        with patch("scripts.backup_sqlite.run_cmd", side_effect=mock_failing_cmd), \
             patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            with pytest.raises(RuntimeError):
                execute_host_backup(
                    bucket="test-bucket",
                    checkin_url="https://hc-ping.test/backup-checkin"
                )

            assert mock_urlopen.call_count == 1
            call_req = mock_urlopen.call_args[0][0]
            assert call_req.full_url == "https://hc-ping.test/backup-checkin/fail"

    def test_backup_monitoring_disabled(self, tmp_path):
        dummy_file = tmp_path / "watcher_test.sqlite"
        dummy_file.write_bytes(b"SQLite format 3\x00test")
        file_size = dummy_file.stat().st_size

        snapshot_meta = json.dumps({"status": "ok", "path": "/app/data/backups/test.sqlite", "size": file_size})
        list_response = json.dumps({"Contents": [{"Key": "backups/test.sqlite", "Size": file_size}]})

        def mock_run_cmd(args, check=True):
            if args[0] == "docker" and args[1] == "exec":
                return MagicMock(returncode=0, stdout=snapshot_meta, stderr="")
            elif args[0] == "docker" and args[1] == "cp":
                dest = args[3]
                with open(dest, "wb") as f:
                    f.write(dummy_file.read_bytes())
                return MagicMock(returncode=0, stdout="", stderr="")
            elif args[0] == "aws" and args[1] == "s3" and args[2] == "cp":
                return MagicMock(returncode=0, stdout="upload ok", stderr="")
            elif args[0] == "aws" and args[1] == "s3api":
                return MagicMock(returncode=0, stdout=list_response, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("scripts.backup_sqlite.run_cmd", side_effect=mock_run_cmd), \
             patch("urllib.request.urlopen") as mock_urlopen:
            result = execute_host_backup(
                bucket="test-bucket",
                checkin_url=None
            )
            assert result["status"] == "success"
            assert mock_urlopen.call_count == 0

    def test_backup_ping_exception_does_not_alter_success_result(self, tmp_path):
        dummy_file = tmp_path / "watcher_test.sqlite"
        dummy_file.write_bytes(b"SQLite format 3\x00test")
        file_size = dummy_file.stat().st_size

        snapshot_meta = json.dumps({"status": "ok", "path": "/app/data/backups/test.sqlite", "size": file_size})
        list_response = json.dumps({"Contents": [{"Key": "backups/test.sqlite", "Size": file_size}]})

        def mock_run_cmd(args, check=True):
            if args[0] == "docker" and args[1] == "exec":
                return MagicMock(returncode=0, stdout=snapshot_meta, stderr="")
            elif args[0] == "docker" and args[1] == "cp":
                dest = args[3]
                with open(dest, "wb") as f:
                    f.write(dummy_file.read_bytes())
                return MagicMock(returncode=0, stdout="", stderr="")
            elif args[0] == "aws" and args[1] == "s3" and args[2] == "cp":
                return MagicMock(returncode=0, stdout="upload ok", stderr="")
            elif args[0] == "aws" and args[1] == "s3api":
                return MagicMock(returncode=0, stdout=list_response, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("scripts.backup_sqlite.run_cmd", side_effect=mock_run_cmd), \
             patch("urllib.request.urlopen", side_effect=TimeoutError("Ping timed out")):
            result = execute_host_backup(
                bucket="test-bucket",
                checkin_url="https://hc-ping.test/backup-checkin"
            )
            # Backup result must succeed despite check-in timeout
            assert result["status"] == "success"
