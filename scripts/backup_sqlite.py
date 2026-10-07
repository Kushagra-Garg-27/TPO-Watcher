#!/usr/bin/env python3
"""
TPO-Watcher Host Backup Script (Python 3.9+ compatible, stdlib only).
Executes in-container SQLite snapshot, retrieves file to secure host temporary directory,
uploads to Amazon S3 via AWS CLI with AES-256 server-side encryption, and verifies
remote visibility and exact size match.
Supports optional Healthchecks.io check-in pings (success / failure) via host-only config file.
"""

import os
import sys
import json
import shutil
import tempfile
import argparse
import logging
import subprocess
import urllib.request
from typing import Dict, Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("tpo-backup")

def run_cmd(args: list, check: bool = True) -> subprocess.CompletedProcess:
    """Executes a command strictly with an argument list, never shell=True."""
    return subprocess.run(args, capture_output=True, text=True, check=check)

def load_checkin_url(config_path: Optional[str]) -> Optional[str]:
    """
    Loads healthcheck check-in URL from a root-only host configuration file.
    Does NOT place the URL on process command line or environment.
    """
    if not config_path or not os.path.exists(config_path):
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return None
            if content.startswith("{"):
                data = json.loads(content)
                return data.get("healthcheck_url") or data.get("HEALTHCHECK_BACKUP_URL")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("HEALTHCHECK_BACKUP_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
                if line.startswith("http://") or line.startswith("https://"):
                    return line
            return None
    except Exception as e:
        logger.warning("Could not read healthcheck config file: %s", type(e).__name__)
        return None

def send_healthcheck_ping(url: Optional[str], success: bool, timeout: float = 5.0) -> bool:
    """
    Sends Healthchecks.io ping without logging URL or secrets.
    Monitoring failure never alters backup execution status or raises exceptions.
    """
    if not url or not url.strip():
        return False
    clean_url = url.strip()
    target = clean_url if success else f"{clean_url.rstrip('/')}/fail"
    try:
        req = urllib.request.Request(
            target,
            headers={"User-Agent": "TPO-Backup-Healthcheck/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 201, 204)
    except Exception as exc:
        logger.warning(
            "Backup healthcheck check-in ping failed (%s): %s",
            "success" if success else "fail",
            type(exc).__name__
        )
        return False

def execute_host_backup(
    bucket: str,
    container: str = "tpo-watcher",
    prefix: str = "backups",
    module_path: str = "app.backup.snapshot",
    checkin_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes the end-to-end host backup workflow:
    1. Invokes in-container snapshot
    2. Copies snapshot to secure temporary directory (0700)
    3. Uploads via AWS CLI with SSE-S3 (AES256)
    4. Verifies remote visibility and size match via s3api list-objects-v2
    5. Always cleans up host temporary artifacts
    6. Sends optional Healthchecks.io ping on success or failure
    """
    temp_dir = None
    try:
        logger.info("Initiating in-container snapshot on container '%s'...", container)
        snapshot_cmd = ["docker", "exec", container, "python", "-m", module_path]
        try:
            snap_proc = run_cmd(snapshot_cmd, check=False)
        except Exception as e:
            logger.error("Failed to execute snapshot command in container: %s", e)
            raise

        if snap_proc.returncode != 0:
            logger.error("In-container snapshot failed with returncode %d: %s", snap_proc.returncode, snap_proc.stderr)
            raise RuntimeError(f"In-container snapshot failed: {snap_proc.stderr}")

        try:
            snapshot_meta = json.loads(snap_proc.stdout.strip())
        except json.JSONDecodeError as e:
            logger.error("Failed to parse snapshot metadata from container stdout: %s", snap_proc.stdout)
            raise RuntimeError(f"Invalid snapshot JSON: {e}")

        if snapshot_meta.get("status") != "ok":
            raise RuntimeError(f"Snapshot returned non-ok status: {snapshot_meta}")

        container_snapshot_path = snapshot_meta["path"]
        expected_size = snapshot_meta["size"]
        filename = os.path.basename(container_snapshot_path)
        logger.info("Snapshot created successfully in container: %s (%d bytes)", container_snapshot_path, expected_size)

        # Secure host temporary directory
        temp_dir = tempfile.mkdtemp(prefix="tpo_backup_")
        try:
            os.chmod(temp_dir, 0o700)
        except Exception as e:
            logger.warning("Could not set 0700 permissions on temp dir: %s", e)

        host_snapshot_path = os.path.join(temp_dir, filename)

        # Copy from container to host
        logger.info("Copying snapshot from container to host temporary directory...")
        cp_cmd = ["docker", "cp", f"{container}:{container_snapshot_path}", host_snapshot_path]
        run_cmd(cp_cmd, check=True)

        if not os.path.exists(host_snapshot_path):
            raise FileNotFoundError(f"Copied snapshot not found on host: {host_snapshot_path}")

        local_size = os.path.getsize(host_snapshot_path)
        if local_size != expected_size:
            raise ValueError(f"Size mismatch after copy: expected {expected_size}, got {local_size}")

        # Upload to S3 via AWS CLI (EC2 instance profile credentials)
        s3_key = f"{prefix.strip('/')}/{filename}"
        s3_uri = f"s3://{bucket}/{s3_key}"
        logger.info("Uploading snapshot to %s with AES256 server-side encryption...", s3_uri)

        upload_cmd = ["aws", "s3", "cp", host_snapshot_path, s3_uri, "--sse", "AES256"]
        run_cmd(upload_cmd, check=True)

        # Verification: list-objects-v2
        logger.info("Verifying remote object visibility and size match...")
        list_cmd = [
            "aws", "s3api", "list-objects-v2",
            "--bucket", bucket,
            "--prefix", s3_key
        ]
        list_proc = run_cmd(list_cmd, check=True)
        list_data = json.loads(list_proc.stdout)

        contents = list_data.get("Contents", [])
        matched = [obj for obj in contents if obj.get("Key") == s3_key]

        if not matched:
            raise RuntimeError(f"Verification failed: Key '{s3_key}' not found in S3 bucket '{bucket}'.")

        remote_size = matched[0].get("Size", 0)
        if remote_size <= 0:
            raise ValueError(f"Verification failed: Remote size is zero for key '{s3_key}'.")
        if remote_size != local_size:
            raise ValueError(f"Verification failed: Remote size ({remote_size}) does not match local size ({local_size}).")

        logger.info("Backup successfully uploaded and verified: %s (%d bytes)", s3_uri, remote_size)
        send_healthcheck_ping(checkin_url, success=True)
        return {
            "status": "success",
            "bucket": bucket,
            "key": s3_key,
            "size": remote_size,
            "filename": filename
        }

    except Exception as e:
        send_healthcheck_ping(checkin_url, success=False)
        raise

    finally:
        # Always remove host temporary directory and artifacts
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info("Cleaned up host temporary directory: %s", temp_dir)

def main():
    parser = argparse.ArgumentParser(description="TPO-Watcher Host SQLite Backup & S3 Upload Tool")
    parser.add_argument("--bucket", required=True, help="Target AWS S3 bucket name")
    parser.add_argument("--container", default="tpo-watcher", help="Docker container name")
    parser.add_argument("--prefix", default="backups", help="S3 object prefix")
    parser.add_argument("--config-file", help="Path to non-secret configuration file")
    parser.add_argument("--checkin-config", help="Path to root-only check-in configuration file")

    args = parser.parse_args()

    bucket = args.bucket
    if args.config_file and os.path.exists(args.config_file):
        try:
            with open(args.config_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                bucket = cfg.get("bucket", bucket)
        except Exception as e:
            logger.warning("Could not read config file %s: %s", args.config_file, e)

    checkin_url = load_checkin_url(args.checkin_config)

    try:
        execute_host_backup(
            bucket=bucket,
            container=args.container,
            prefix=args.prefix,
            checkin_url=checkin_url
        )
        sys.exit(0)
    except Exception as e:
        logger.error("Backup workflow failed: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
