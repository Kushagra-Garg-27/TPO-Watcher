# TPO-Watcher Backup Service & Timer (systemd)

Version-controlled templates for automated host-level database backups to Amazon S3.

> [!IMPORTANT]
> These unit definitions are templates. Do **NOT** install, enable, or start these services without explicit owner authorization and prerequisite AWS S3/IAM setup.

---

## Architecture & Security Profile

- **Trigger:** Systemd timer runs daily at `02:00 Asia/Kolkata` (`20:30 UTC` previous calendar day).
- **Execution Type:** `Type=oneshot` executing `scripts/backup_sqlite.py`.
- **Identity & Credentials:** Operates under host EC2 IAM Instance Profile with strictly scoped permissions (`s3:PutObject` on `backups/*`, `s3:ListBucket` on prefix `backups/*`).
- **No Credentials on CLI or Files:** AWS CLI automatically retrieves temporary instance profile credentials from the AWS metadata service. No AWS secret keys are stored in files or passed via arguments.
- **Explicit Failure Semantics:** Exits non-zero immediately on snapshot failure, copy failure, upload failure, or verification mismatch. Systemd logs failure state and triggers journal entries.

---

## Installation Procedure

1. **Create configuration files & permissions**
   The backup service runs under user `ec2-user`. Configuration files must be readable by `ec2-user` while isolated from other unprivileged users (`ec2-user:ec2-user`, file mode `0600`, directory mode `0700`):

   ```bash
   sudo mkdir -p /etc/tpo-watcher
   sudo chown ec2-user:ec2-user /etc/tpo-watcher
   sudo chmod 0700 /etc/tpo-watcher

   # Non-secret configuration (S3 bucket)
   sudo tee /etc/tpo-watcher/backup.env > /dev/null << 'EOF'
   BACKUP_S3_BUCKET=tpo-watcher-backups-ap-south-1
   EOF
   sudo chown ec2-user:ec2-user /etc/tpo-watcher/backup.env
   sudo chmod 0600 /etc/tpo-watcher/backup.env
   ```

   *(Optional) Configure Healthchecks.io check-in ping:*
   ```bash
   sudo tee /etc/tpo-watcher/backup-healthcheck.conf > /dev/null << 'EOF'
   HEALTHCHECK_BACKUP_URL=https://hc-ping.com/YOUR-UUID-HERE
   EOF
   sudo chown ec2-user:ec2-user /etc/tpo-watcher/backup-healthcheck.conf
   sudo chmod 0600 /etc/tpo-watcher/backup-healthcheck.conf
   ```

2. **Copy systemd units**
   ```bash
   sudo cp infra/systemd/tpo-backup.service /etc/systemd/system/
   sudo cp infra/systemd/tpo-backup.timer /etc/systemd/system/
   sudo chmod 0644 /etc/systemd/system/tpo-backup.service
   sudo chmod 0644 /etc/systemd/system/tpo-backup.timer
   ```

3. **Pre-install schedule validation**
   Before enabling the timer, validate the schedule expression on the production host:
   ```bash
   systemd-analyze calendar '*-*-* 02:00:00 Asia/Kolkata'
   ```
   *Note: If the host systemd version lacks named IANA timezone support, edit `/etc/systemd/system/tpo-backup.timer` to use the UTC equivalent (`*-*-* 20:30:00 UTC`) and validate:*
   ```bash
   systemd-analyze calendar '*-*-* 20:30:00 UTC'
   ```

4. **Reload systemd daemon**
   ```bash
   sudo systemctl daemon-reload
   ```

5. **Enable and start the timer**
   ```bash
   sudo systemctl enable --now tpo-backup.timer
   ```

---

## Operational Verification

### 1. Check Timer Status
```bash
sudo systemctl status tpo-backup.timer
```

### 2. List Active Timers
```bash
systemctl list-timers --all
```
Expected output shows `tpo-backup.timer` with `NEXT` scheduled run at 02:00:00 IST (or 20:30:00 UTC).

### 3. Safe Manual One-Off Execution
To test the backup pipeline without waiting for the timer:
```bash
sudo systemctl start tpo-backup.service
```

### 4. Check Service Status & Logs
```bash
sudo systemctl status tpo-backup.service
sudo journalctl -u tpo-backup.service -n 50 --no-pager
```

---

## Disable Procedure

To temporarily or permanently deactivate the automated backup timer:
```bash
sudo systemctl disable --now tpo-backup.timer
sudo systemctl daemon-reload
```

---

## Monthly Restore Verification Runbook

> [!CAUTION]
> The production EC2 instance profile has **NO** `s3:GetObject` permission by design (least privilege principle).
> Monthly restore tests must be executed by an **OWNER/RECOVERY** identity with read access to the backup S3 bucket.

1. **Download latest snapshot using recovery identity:**
   ```bash
   aws s3 cp s3://<BACKUP_BUCKET>/backups/<snapshot_filename>.sqlite /tmp/test_restore.sqlite
   ```

2. **Run the Restore Validation Tool:**
   ```bash
   python -m scripts.restore_test /tmp/test_restore.sqlite
   ```

3. **Verification Checklist:**
   - `PRAGMA integrity_check` reports `ok`
   - All 7 core schema tables present
   - Expected column structures match
   - Repository read queries succeed
   - File cleaned up: `rm /tmp/test_restore.sqlite`
