# Continuous Integration & Software Supply-Chain Security

This document outlines the CI pipeline and software supply-chain security architecture for **TPO-Watcher**, implemented in **Sprint 4 Phase 1**.

---

## 1. CI Workflow Purpose & Triggers

The GitHub Actions CI workflow (`.github/workflows/ci.yml`) automatically validates code quality, test suite integrity, and dependency security on:
- **Pushes** to the default branch (`main`).
- **Pull Requests** targeting the default branch (`main`).

It ensures that no breaking regressions, syntax errors, typecheck failures, or critical dependency vulnerabilities enter the repository.

---

## 2. Jobs & Quality Gates

The CI pipeline consists of four distinct, decoupled jobs executing on standard `ubuntu-latest` runners:

### A. Python Backend Tests (`backend-tests`)
- **Environment:** Python 3.12 with pip dependency caching.
- **Dependencies:** Installed from requirements manifest via `pip install -r requirements.txt`. Note: Because top-level Python dependencies are unpinned in the current baseline, version resolution is dynamic; full deterministic reproducibility requires a pinned lockfile (planned for an owner-approved follow-up).
- **Command:** `pytest -v`.
- **Quality Gate:** the complete unit and integration suite must pass with 0 failures; the exact test count is tracked per release receipt rather than fixed in this document.
- **Safety Boundary:** All test paths use hermetic synthetic database fixtures and mock transports (`guard_live_smtp`, `guard_live_database`). No live external connections (SMTP, VIERP, AWS, or production databases) are permitted or initiated.

### B. Frontend Lint & Build (`frontend-checks`)
- **Environment:** Node.js 22 with npm dependency caching keyed to `frontend/package-lock.json`.
- **Dependencies:** Clean, reproducible installation via `npm ci` inside `frontend/`.
- **Linting:** `npm run lint` executing `oxlint` across all TypeScript and React source files (must pass with 0 errors).
- **Compilation:** `npm run build` executing `tsc -b` (TypeScript typecheck) and `vite build` (production bundle generation).

### C. Dependency Vulnerability Audit (`security-audit`)
- **Python Audit:** Executes `pip-audit -r requirements.txt --desc`.
  - **Status:** **Blocking Quality Gate**. Any high or critical CVE detected in direct Python dependencies will fail the CI run.
- **Node Audit:** Executes `npm audit --audit-level=high` inside `frontend/`.
  - **Status:** **Advisory / Report-Only**.
  - **Tracked Baseline Finding:** Currently, the transitive build-time dependency `source-map-js` (pulled by PostCSS/Tailwind) has a known advisory ([GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q)). Because runtime dependencies must not be upgraded without separate owner authorization, this finding is surfaced as an advisory warning rather than blocking general feature delivery until an approved dependency update task is executed.

### D. CycloneDX SBOM Generation (`sbom-generation`)
- Generates Software Bill of Materials (SBOM) in standard CycloneDX JSON format:
  - Python: Generated via `cyclonedx-py requirements requirements.txt`.
  - Node: Generated via `@cyclonedx/cyclonedx-npm`.
- Uploads `sbom-python-cyclonedx.json` and `sbom-node-cyclonedx.json` as build artifacts retained for 30 days.

---

## 3. GitHub Actions Security & Least Privilege

The workflow adheres strictly to security hardening best practices:
1. **Explicit Least Privilege:** The workflow declares top-level `permissions: contents: read`. No write tokens, deployment tokens, or elevated scopes are granted.
2. **Immutable Action Pinning:** All external GitHub Actions are pinned to verified, immutable 40-character commit SHAs, accompanied by human-readable version comments:
   - `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2`
   - `actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 # v5.4.0`
   - `actions/setup-node@1d0ff469b7ec7b3cb9d8673fde0c81c44821de2a # v4.2.0`
   - `actions/upload-artifact@4cec3d8aa04e39d1a68397de0c4cd6fb9dce8ec1 # v4.6.1`
3. **Zero Production Secrets:** The CI workflow requires and receives **zero production credentials**:
   - NO AWS access keys or IAM tokens.
   - NO EC2 SSH deployment keys.
   - NO production `.env` files.
   - NO SMTP or VIERP institutional passwords.
4. **Decoupled from Deployment:** There is **no Continuous Deployment (CD)** in this repository. Production deployment is strictly a manual, owner-supervised pull on the EC2 host.

---

## 4. Dependabot Automated Auditing

Automated dependency checking is configured in `.github/dependabot.yml`:
- **Ecosystems Monitored:**
  - Python (`pip`) in `/` (weekly).
  - Node (`npm`) in `/frontend` (weekly).
  - GitHub Actions (`github-actions`) in `/` (weekly).
- **Concurrency Limit:** Capped at 5 open pull requests per ecosystem to minimize maintenance noise.
- **Safety:** Automatic merging is **disabled**. All dependency updates require human review and local test validation.

---

## 5. How to Run Equivalent Checks Locally

Developers can reproduce all CI checks on a local machine prior to committing:

```bash
# 1. Run Python Backend Tests
.\.venv\Scripts\pytest.exe -v
# (Expect the full suite; see the release receipt for the exact count)

# 2. Run Frontend Linter
cd frontend
npm run lint
# (Expect 0 errors, 0 warnings from oxlint)

# 3. Run Frontend Typecheck and Build
npm run build
# (Expect clean compilation via tsc and vite)

# 4. Run Node Dependency Audit
npm audit
```
