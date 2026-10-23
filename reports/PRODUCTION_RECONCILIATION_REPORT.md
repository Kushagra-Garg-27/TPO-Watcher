# TPO-WATCHER — PRODUCTION RECONCILIATION & RELEASE PROPOSAL

**Date:** 23 October 2026  
**Target Environment:** AWS EC2 (`65.0.66.80`, ap-south-1c)  
**Production Host:** `t4g.small` (ARM64), Amazon Linux 2023  
**Currently Deployed Container Revision:** `956892a` (Built 2026-10-07)  
**Intended Release Revision:** `eae6868` + Remediation Fixes (Working Tree)  
**Production Authority Level:** STRICTLY GATED — Explicit User Approval Required  

---

## 1. Production State Reconciliation

| Component | Deployed Production State (`956892a`) | Intended Release State (`eae6868` + Fixes) | Drift & Impact Assessment |
|---|---|---|---|
| **Container Image** | Built 2026-10-07 (`956892a`) | To be built from current branch | 11+ commits pending deployment. |
| **Frontend UI** | Serves static build with hardcoded `RecentDetections` | Serves live API client connecting to `/api/v1/opportunities` | **CRITICAL FIX:** Current site hides 7 real detected companies (Cummins, Eaton, etc.). |
| **HSTS Header** | `max-age=86400` (1 day) | `max-age=31536000` (1 year, domain-safe) | Strengthens HTTPS enforcement for all return visitors. |
| **Database Schema** | `notification_deliveries` without `raw_data_snapshot` | Automated migration adds `raw_data_snapshot TEXT` | Non-destructive column addition via `ALTER TABLE`. |
| **Delivery Queue** | Drops subsequent UPDATE events if already SENT | Re-queues distinct UPDATE events to PENDING | Fixes delivery suppression on repeated opportunity updates. |
| **Mastercard 5610** | `is_active = NULL` (historical orphan) | Repaired to `is_active = 'False'` via SQL script | Resolves orphan data quality defect. |
| **Email Copy** | Displays raw numbers (e.g. ₹25,000 without unit); no disclaimer | "Compensation (source value)" label; mandatory disclaimer | Eliminates LPA ambiguity; mitigates student liability. |
| **Action Tokens** | SHA-256 hashed, single use | SHA-256 hashed, single use + verified retention cleanup | Prunes expired tokens older than 30 days. |
| **Backups** | Daily S3 backup with AES256 encryption | Daily S3 backup with verified integrity checks | Production backups already active and operational. |
| **Memory / Swap** | 0B Swap (EC2 host) | 1 GB host swapfile | Alleviates Playwright Chromium launch OOM risk. |

---

## 2. Production Approval Gates

Explicit approval from the user is required before executing any of the following four deployment gates:

### Gate 1: Production Database Data Repair (G04)
- **Action:** Apply [`G04_PRODUCTION_REPAIR.sql`](file:///c:/Projects/TPO%20-%20Watcher/reports/G04_PRODUCTION_REPAIR.sql) to `/app/data/watcher.sqlite` via SQLite CLI.
- **Preconditions:** S3 backup confirmed present (`aws s3 ls s3://...`).
- **Command:**
  ```bash
  ssh -i <key.pem> ec2-user@65.0.66.80
  docker exec -i tpo-watcher sqlite3 /app/data/watcher.sqlite < G04_PRODUCTION_REPAIR.sql
  ```
- **Service Impact:** Zero downtime; single row transaction (<5ms).

### Gate 2: Verify & Rotate `SECRET_KEY` (G02)
- **Action:** Inspect `/home/ec2-user/TPO-Watcher/.env` to confirm `SECRET_KEY` does not equal the default string.
- **Command:**
  ```bash
  ssh -i <key.pem> ec2-user@65.0.66.80
  grep SECRET_KEY /home/ec2-user/TPO-Watcher/.env
  ```
- **If Default Detected:** Rotate using cryptographic hex token:
  ```bash
  python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
  ```
- **Service Impact:** Requires container restart; existing active session cookies invalidated.

### Gate 3: Allocate EC2 Host Swapfile (G06)
- **Action:** Configure 1GB swapfile on Amazon Linux 2023 host.
- **Command:**
  ```bash
  sudo dd if=/dev/zero of=/swapfile bs=1M count=1024
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab
  ```
- **Service Impact:** Zero downtime; increases memory safety margin during browser execution.

### Gate 4: Deploy Reconciled Container Image (G10)
- **Action:** Build and redeploy container image containing the verified remediation fixes and live frontend bundle.
- **Rollback Plan:** If health check fails post-deployment, roll back container tag to `956892a`:
  ```bash
  docker-compose -f docker-compose.prod.yml down
  # update image tag in docker-compose.prod.yml to 956892a
  docker-compose -f docker-compose.prod.yml up -d
  ```
- **Post-Deployment Verification:**
  ```bash
  curl -fsSL https://tpowatcher.duckdns.org/health
  curl -fsSL https://tpowatcher.duckdns.org/api/v1/opportunities
  curl -sI https://tpowatcher.duckdns.org/ | grep -i "strict-transport-security"
  ```
