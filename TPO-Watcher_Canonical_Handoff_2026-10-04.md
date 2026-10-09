# TPO-Watcher — Canonical Project Handoff
## Complete Project Context as of 2026-10-04

**Canonical production baseline:** `e2d548c229f036dc7d67b85b7bdd4a346d254ac4`  
**Short commit:** `e2d548c`  
**Production release status:** **DEPLOYED + RECONCILED + FULLY VERIFIED**  
**Production URL:** `https://tpowatcher.duckdns.org`  
**AWS region:** `ap-south-1` (Mumbai)  
**EC2 instance:** `i-03328ee0e60347254`  
**Architecture:** ARM64 / `aarch64`

---

# 0. PURPOSE OF THIS HANDOFF

This document is the canonical context package for continuing the TPO-Watcher project in a new AI/chat session.

When starting a new chat, provide this entire file and tell the AI:

> Treat this handoff as the authoritative current project context unless I explicitly provide newer evidence. Do not invent missing details. Preserve all production-safety rules, the student/TPO identity separation, the current deployment architecture, and the verified production baseline.

Important chronology:

- The older verified security baseline was `64e6a33`.
- The project then received:
  - a backend Live Opportunities API,
  - authoritative VIT TPO feed reconciliation,
  - session-expiry/auth robustness fixes,
  - a major cinematic frontend redesign,
  - a dynamic Live Opportunities hero,
  - regenerated production static assets.
- These changes were committed in three logical commits and deployed.
- Production is now at `e2d548c`.
- The first real production watcher cycles after deployment successfully reconciled stale companies.
- The release is now **FULLY VERIFIED**.

This document supersedes earlier project handoffs for current-state work.

---

# 1. PROJECT IDENTITY

## Project name
**TPO-Watcher**

## Repository
`https://github.com/Kushagra-Garg-27/TPO-Watcher`

## Production
`https://tpowatcher.duckdns.org`

## Production health
`https://tpowatcher.duckdns.org/health`

## Public Live Opportunities endpoint
`https://tpowatcher.duckdns.org/api/v1/opportunities`

## VIT TPO portal
`https://tpo.vierp.in/`

## VIT TPO company schedule endpoint used by the watcher
`POST https://tpoapi.vierp.in/TPOCompanyScheduling/newschedulesdcopanies`

The misspelling in `newschedulesdcopanies` is the real endpoint spelling and must be preserved.

---

# 2. PRODUCT PURPOSE

TPO-Watcher is a VIT Pune placement/internship/PPO opportunity monitoring service.

Core responsibilities:

1. Authenticate to the official VIT/VIERP TPO portal using one internal watcher identity.
2. Read the current VIT TPO company schedule feed.
3. Persist company/opportunity state in SQLite.
4. Detect new or changed opportunity state.
5. Reconcile stale active records against the authoritative current TPO feed.
6. Match relevant opportunities to subscribed students.
7. Send email notifications.
8. Expose a public student-facing website.
9. Allow students to sign up and manage preferences without ever providing their VIERP password.
10. Show a dynamic public “Live Opportunities” section driven by the same persisted TPO data.

The current product rule is:

```text
VIT TPO portal/feed
        ↓
Authenticated watcher
        ↓
CompanyRecord / ingestion pipeline
        ↓
SQLite companies table
        ↓
DatabaseRepository
        ↓
GET /api/v1/opportunities
        ↓
frontend api.getOpportunities()
        ↓
LiveOpportunities.tsx
        ↓
Hero / public website
```

The website must never invent opportunity data.

---

# 3. FROZEN IDENTITY ARCHITECTURE

The application deliberately has **two completely separate identities**.

## 3.1 Internal watcher identity

The watcher uses privileged server-side credentials:

- `TPO_USERNAME`
- `TPO_PASSWORD`

Authentication mechanism:

- Playwright
- normal VIERP web login
- Altcha challenge
- persisted browser/session state

Playwright state file:

`/app/data/playwright_state.json`

This identity is server-side only.

## 3.2 Student identity

Students authenticate to **TPO-Watcher itself**, not to VIERP.

The student identity model is based on:

- VIT institutional email verification
- passwordless account verification
- secure TPO-Watcher sessions
- preference management

Students must never provide their VIERP password.

## 3.3 Absolute identity rules

TPO-Watcher must never:

- ask students for VIERP passwords
- receive student VIERP passwords
- store student VIERP passwords
- proxy student VIERP passwords
- impersonate an official VIT OAuth flow
- build fake OAuth/OIDC/SAML
- bypass Altcha
- reverse-engineer Altcha

Future “Continue with VIERP” is allowed only if VIT later exposes an official delegated OAuth/OIDC/SAML/SSO mechanism suitable for third-party apps.

No such official third-party delegated SSO was verified during earlier research.

---

# 4. CURRENT HIGH-LEVEL PRODUCTION ARCHITECTURE

```text
Internet
   ↓
https://tpowatcher.duckdns.org
   ↓
Caddy
   ↓
tpo-watcher FastAPI container
   ├── React/Vite production static files
   ├── student auth API
   ├── /api/v1/opportunities
   ├── SQLite
   ├── scheduler
   └── watcher
          ↓
      Playwright
          ↓
      VIERP / VIT TPO
          ↓
      company schedule API
```

Public network topology:

```text
Client
  ↓ HTTPS
Caddy :443
  ↓
watcher:8000
```

The watcher is not publicly exposed directly.

---

# 5. AWS PRODUCTION ENVIRONMENT

## Provider
AWS EC2

## Region
`ap-south-1`

Mumbai region.

## Instance ID
`i-03328ee0e60347254`

## Architecture
`aarch64` / ARM64

## Private IPv4
`172.31.21.39`

## Current known public IPv4
`65.0.66.80`

Historical public IPv4 addresses included:

- `13.205.3.36`
- `13.204.91.213`

The current public IP is **ephemeral** because no Elastic IP has been confirmed.

Stopping/restarting the EC2 instance can change it.

## SSH user
`ec2-user`

## Windows SSH private key path
`C:\Users\kusha\Downloads\tpo.pem`

Typical SSH command:

```powershell
ssh -i "C:\Users\kusha\Downloads\tpo.pem" ec2-user@65.0.66.80
```

---

# 6. CURRENT EC2 SECURITY GROUP / SSH CONTEXT

As of the most recent access update, the user’s current public IPv4 was:

`58.84.62.92`

Therefore the personal SSH inbound rule should be:

```text
Type: SSH
Protocol: TCP
Port: 22
Source: 58.84.62.92/32
```

This IP can change again.

To discover the current public IP from the Windows development machine:

```powershell
curl.exe https://checkip.amazonaws.com
```

or:

```powershell
(Invoke-RestMethod -Uri "https://checkip.amazonaws.com").Trim()
```

Then allow only:

`CURRENT_IP/32`

Do **not** open SSH to `0.0.0.0/0`.

## Current known inbound rules from AWS console

### HTTPS
- Security group rule ID: `sgr-0a9ea1161a7485331`
- Type: HTTPS
- Protocol: TCP
- Port: 443
- Source: `0.0.0.0/0`

### HTTP
- Security group rule ID: `sgr-0210dae85ff4c9da2`
- Type: HTTP
- Protocol: TCP
- Port: 80
- Source: `0.0.0.0/0`

### Personal SSH
- Security group rule ID: `sgr-07d93271917451e8c`
- Type: SSH
- Protocol: TCP
- Port: 22
- Current known source: `58.84.62.92/32`

### Additional SSH prefix-list rule
- Security group rule ID: `sgr-0b521c93736f87b9d`
- Type: SSH
- Protocol: TCP
- Port: 22
- Source prefix list: `pl-0fa83cebf909345ca`

Do not remove or alter the prefix-list SSH rule merely because the personal `/32` rule changes. Its purpose should be established before modification.

---

# 7. PRODUCTION DOMAIN / REVERSE PROXY

Production domain:

`https://tpowatcher.duckdns.org`

Caddy is the public reverse proxy.

Current Caddy configuration:

```text
tpowatcher.duckdns.org {
    reverse_proxy watcher:8000
}
```

Caddy owns public ports:

- `80`
- `443`

Watcher is exposed only to localhost on the EC2 host:

`127.0.0.1:8000:8000`

Do not expose port 8000 publicly.

---

# 8. CURRENT DOCKER SERVICES

## Watcher

Container:

`tpo-watcher`

Image:

`tpo-watcher-watcher:latest`

Current deployed image ID after release `e2d548c`:

`3e76fb199380`

Current image size observed:

approximately `2.74 GB`

Command:

`python -m app.main`

Restart policy:

`unless-stopped`

Port binding:

`127.0.0.1:8000 -> 8000/tcp`

## Caddy

Container:

`tpo-caddy`

Image:

`caddy:2`

Public ports:

- `0.0.0.0:80`
- `0.0.0.0:443`

Caddy remained continuously running during the `e2d548c` deployment.

---

# 9. PRODUCTION DOCKER COMPOSE

Production has intentional local Compose/Caddy infrastructure drift.

Important production Compose structure:

```yaml
services:
  watcher:
    build: .
    container_name: tpo-watcher
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    env_file:
      - .env
    volumes:
      - watcher_data:/app/data
    environment:
      - DB_PATH=/app/data/watcher.sqlite
      - STATE_FILE=/app/data/playwright_state.json

  caddy:
    image: caddy:2
    container_name: tpo-caddy
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - watcher

volumes:
  watcher_data:
  caddy_data:
  caddy_config:
```

Critical:

- Production `docker-compose.yml` is locally modified.
- `Caddyfile` is local/untracked in the production checkout.
- These are intentional infrastructure differences.
- Do not “clean” them away.

---

# 10. PRODUCTION PERSISTENT VOLUMES

Current volumes:

- `tpo-watcher_watcher_data`
- `tpo-watcher_caddy_data`
- `tpo-watcher_caddy_config`

Watcher data volume mount:

```text
tpo-watcher_watcher_data
    ↓
/app/data
```

Important files:

## SQLite
`/app/data/watcher.sqlite`

Latest verified size:

approximately `96 KB`

## Playwright state
`/app/data/playwright_state.json`

Latest verified size:

approximately `2.0 KB`

Both survived the production deployment to `e2d548c`.

---

# 11. PRODUCTION BACKUP CREATED BEFORE e2d548c DEPLOYMENT

A transactionally consistent SQLite backup was created using Python’s SQLite online backup API.

Backup path:

`/app/data/watcher_backup_pre_e2d548c_20261003_082126.sqlite`

Size:

approximately `96 KB`

This backup was verified intact after deployment and after reconciliation.

Do not delete it casually.

---

# 12. HISTORICAL BACKUPS / PRODUCTION ARTIFACTS

Known production-local files include:

```text
.env.save
Caddyfile
Caddyfile.pre-security-deploy
docker-compose.yml.before-caddy
docker-compose.yml.pre-security-deploy
tpo-watcher-data-backup-.tar.gz
tpo-watcher-data-backup-20260929-133827.tar.gz
tpo-watcher-data-pre-ui-deploy.tar.gz
```

Additional backup observed in `/home/ec2-user`:

`/home/ec2-user/tpo-watcher-backup-20260930_182621.tar.gz`

Do not print `.env.save`.

Do not commit these backup/config files.

Do not delete them casually.

---

# 13. PRODUCTION GIT WORKTREE STATE

Production is currently at:

`e2d548c229f036dc7d67b85b7bdd4a346d254ac4`

Branch:

`main`

GitHub `origin/main` was also verified at `e2d548c` immediately before/after deployment.

Production working tree intentionally remains non-clean because of local infrastructure:

```text
 M docker-compose.yml
?? .env.save
?? Caddyfile
?? Caddyfile.pre-security-deploy
?? docker-compose.yml.before-caddy
?? docker-compose.yml.pre-security-deploy
?? tpo-watcher-data-backup-.tar.gz
?? tpo-watcher-data-backup-20260929-133827.tar.gz
?? tpo-watcher-data-pre-ui-deploy.tar.gz
```

This is expected.

Never blindly run:

```text
git reset --hard
git clean -fd
git checkout -- .
git restore .
```

on production.

---

# 14. IMPORTANT GIT HISTORY

Key historical commits:

## `9141f1a`
`feat(v1): launch React public UI for TPO watcher`

Earlier stable V1 public UI baseline.

## `c0e12e1`
`fix(test): isolate email delivery from live SMTP`

Fixed test leakage into Gmail SMTP.

## `f03322c`
`feat(branches): add complete current VIT B.Tech branch catalog`

Expanded branch coverage.

## `66205cf`
`chore(deploy): add Caddy reverse proxy service`

Caddy infrastructure history.

## `64e6a33`
Full:
`64e6a3324ab949e9a48526bf9ef53c4ea454e2db`

Message:
`feat(security): harden identity and session security`

This is the formally verified **Sprint 1 security baseline**.

## `dd2cb76`
`feat(backend): add live opportunities and authoritative feed reconciliation`

Includes:

- `/api/v1/opportunities`
- active-opportunity repository query
- feed reconciliation
- auth/session-expiry robustness
- test DB isolation
- reconciliation tests
- `.gitignore` WAL/SHM rules

## `efcb262`
`feat(frontend): launch cinematic UI and dynamic live opportunities`

Includes:

- major frontend redesign
- dynamic Live Opportunities hero
- new motion components
- new sections
- redesigned student-facing pages

## `e2d548c`
Full:
`e2d548c229f036dc7d67b85b7bdd4a346d254ac4`

Message:
`build(static): regenerate production frontend bundle`

This is the current deployed production baseline.

---

# 15. CURRENT RELEASE CHAIN

```text
64e6a33
Sprint 1 security baseline
    ↓
dd2cb76
Live Opportunities backend + feed reconciliation
    ↓
efcb262
Cinematic frontend + dynamic Live Opportunities
    ↓
e2d548c
Production frontend static bundle
```

Current production:

**`e2d548c`**

Status:

**DEPLOYED + RECONCILED + FULLY VERIFIED**

---

# 16. GITHUB / DEPLOY KEY HISTORY

A dedicated EC2 GitHub deploy key exists:

`/home/ec2-user/.ssh/github_tpo_watcher_deploy`

Public fingerprint:

`SHA256:d+86uYnO6/Xb3/b05b+jZtzNl6NlQ7kQZ5xS9U0j8n0`

Historical SSH alias:

`github.com-tpo-watcher`

Historical SSH remote form:

`git@github.com-tpo-watcher:Kushagra-Garg-27/TPO-Watcher.git`

At other points the production checkout appeared to use HTTPS.

Do not assume the current remote URL without checking `git remote -v`.

Never expose the private deploy key.

---

# 17. CURRENT PRODUCTION HEALTH

Verified local health:

`http://127.0.0.1:8000/health`

Verified public health:

`https://tpowatcher.duckdns.org/health`

Current response:

```json
{
  "status": "healthy",
  "database": "connected",
  "baseline_initialized": true,
  "deliveries": {}
}
```

Both return HTTP 200.

Caddy remains in front of Uvicorn.

---

# 18. SCHEDULER

Timezone:

`Asia/Kolkata`

Scheduled watcher checks:

- `00:00`
- `10:00`
- `17:00`

Verified post-deployment production cycles:

- 2026-10-03 17:00 IST
- 2026-10-04 00:00 IST
- 2026-10-04 10:00 IST

The first post-deployment reconciliation happened at the 2026-10-03 17:00 IST cycle.

UTC timestamp in logs for that cycle:

approximately `2026-10-03 11:30:13 UTC`

---

# 19. CURRENT VIT TPO AUTHORITATIVE FEED

The current verified VIERP source feed contains exactly four active records:

1. BMC Software
   - ID: `5705`

2. Visteon
   - ID: `5785`

3. Technip Energies
   - ID: `5864`

4. CITI
   - ID: `5869`

All were returned by the real authenticated VIT TPO feed.

These are not hardcoded frontend values.

---

# 20. CURRENT PRODUCTION COMPANY STATE

Production SQLite contains six historical/company records total.

Current state after authoritative reconciliation:

| ID | Company | is_active | Status |
|---|---|---|---|
| 5610 | Mastercard | `False` | historical/stale |
| 5705 | BMC Software | `True` | current feed |
| 5785 | Visteon | `True` | current feed |
| 5812 | Siemens | `False` | stale/ghost reconciled |
| 5864 | Technip Energies | `True` | current feed |
| 5869 | CITI | `True` | current feed |

Current active count:

`4`

Total company rows:

`6`

---

# 21. VERIFIED COMPANY TIMESTAMPS

Latest production verification showed:

## Mastercard
- ID: `5610`
- `is_active = False`
- `first_seen_at = 2026-09-10T08:19:39.864541+00`
- `last_seen_at = 2026-09-15T04:30:12.799396+00`

## BMC Software
- ID: `5705`
- `is_active = True`
- `first_seen_at = 2026-09-10T08:19:39.872980+00`
- `last_seen_at = 2026-10-04T04:30:13.175922+00`

## Visteon
- ID: `5785`
- `is_active = True`
- `first_seen_at = 2026-09-10T08:19:39.876219+00`
- `last_seen_at = 2026-10-04T04:30:13.186719+00`

## Siemens
- ID: `5812`
- `is_active = False`
- `first_seen_at = 2026-09-10T08:19:39.880850+00`
- `last_seen_at = 2026-09-22T04:30:12.703802+00`

## Technip Energies
- ID: `5864`
- `is_active = True`
- `first_seen_at = 2026-09-22T11:30:12.955625+00`
- `last_seen_at = 2026-10-04T04:30:13.195542+00`

## CITI
- ID: `5869`
- `is_active = True`
- `first_seen_at = 2026-09-23T11:30:12.162539+00`
- `last_seen_at = 2026-10-04T04:30:13.205874+00`

---

# 22. LIVE OPPORTUNITIES ARCHITECTURE

Current data flow:

```text
SQLite companies table
        ↓
DatabaseRepository.get_active_opportunities()
        ↓
GET /api/v1/opportunities
        ↓
frontend api.getOpportunities()
        ↓
LiveOpportunities.tsx
        ↓
HeroSection
```

The Hero is not tied to BMC or any other named company.

BMC appears only when returned by the database/API.

---

# 23. OPPORTUNITIES BACKEND QUERY

The public opportunity feed uses active records only.

The earlier incorrect form:

```sql
WHERE is_active = 'True' OR is_active IS NULL
```

was replaced.

Current intended semantics:

```sql
WHERE is_active = 'True'
ORDER BY first_seen_at DESC, id DESC
LIMIT 5
```

This prevents `NULL` test/historical records from entering the public live feed.

---

# 24. PUBLIC OPPORTUNITIES API

Endpoint:

`GET /api/v1/opportunities`

Current verified production response count:

`4`

Current verified production records:

1. CITI
2. Technip Energies
3. Visteon
4. BMC Software

Not returned:

- Siemens
- Mastercard
- synthetic/test records

Current endpoint is read-only.

---

# 25. IMPORTANT COUNT SEMANTICS LIMITATION

The Live Opportunities header currently renders:

```tsx
String(opportunities.length).padStart(2, '0')
```

and displays:

`NN ACTIVE`

The backend API currently limits results to 5.

Therefore:

`04 ACTIVE`

currently equals:

> number of opportunities returned/displayed

It does **not** represent an independently queried total active count if the source ever contains more than five active opportunities.

This is a known product-semantics limitation.

Do not silently claim it is a true global total.

No change has been made yet because the current production feed has only four active records, so the displayed count currently happens to equal the real active total.

---

# 26. AUTHORITATIVE FEED RECONCILIATION

The major lifecycle bug fixed in `dd2cb76` was:

> The watcher previously upserted records it saw but did not deactivate previously active records that disappeared from the current authoritative feed.

That caused stale “ghost” records such as Siemens and Mastercard to remain active.

Current reconciliation behavior:

1. Successfully fetch current VIT TPO feed.
2. Upsert incoming records.
3. Build current incoming ID set.
4. Find database records currently marked `is_active='True'` whose IDs are not in the incoming feed.
5. Mark those stale records `is_active='False'`.
6. Preserve history; do not delete rows.

Reconciliation identity key:

**VIERP record ID**

Never company name.

---

# 27. RECONCILIATION REPOSITORY METHOD

A method equivalent to:

```python
deactivate_missing_companies(current_active_ids)
```

was added to `DatabaseRepository`.

Important semantics:

- only deactivates rows currently marked `True`
- only uses stable IDs
- never deletes history
- returns deactivated IDs
- uses a transaction
- logs deactivation actions
- refuses to act on an empty ID set

---

# 28. EMPTY-FEED SAFETY RULE

A critical guardrail exists:

If the current fetched company list is empty:

**do not deactivate every active database record.**

Why:

The client cannot conclusively distinguish a legitimate authoritative zero-company feed from certain anomalous empty-source conditions.

Therefore:

```text
empty fetched list
    ↓
log warning
    ↓
preserve existing DB active state
```

The repository also independently refuses to deactivate on an empty ID set.

---

# 29. FAILED-FETCH SAFETY

If any of the following occurs:

- authentication failure
- session expiration failure
- Playwright failure
- Altcha failure
- network failure
- non-200 status
- malformed JSON
- Pydantic validation failure
- browser/API fetch failure

then `fetch_companies()` raises and reconciliation is not reached.

Existing active DB state is preserved.

---

# 30. COMPLETE-FEED AUDIT RESULT

A final pre-commit audit concluded the client ingestion path is safe enough for current use:

- VIERP returns the company schedule list in one response.
- No pagination was observed.
- No continuation token/offset/page parameter exists in the client path.
- `expect_response` waits for the response.
- `response.text()` reads the full HTTP body.
- `json.loads()` fails on truncated invalid JSON.
- Pydantic validates the entire `company_list`.
- A malformed item causes validation failure rather than silently dropping only that item.

Residual risk:

The upstream VIERP server does not expose a total count/checksum. Therefore a hypothetical valid HTTP 200 response containing a server-side truncated but syntactically valid subset cannot be independently detected by the client.

This was classified as a **LOW / server-side residual risk**.

Do not add arbitrary “50% drop” heuristics without a separate design decision.

---

# 31. PRODUCTION RECONCILIATION VERIFICATION

At the first scheduled run after deployment:

- VIERP authentication succeeded.
- Current feed returned 4 records.
- Mastercard was absent and was marked False.
- Siemens was absent and was marked False.

Verified log behavior:

```text
Feed reconciliation: company 'Mastercard' (ID: 5610) absent from authoritative source feed. Marked is_active = 'False'.

Feed reconciliation: company 'Siemens' (ID: 5812) absent from authoritative source feed. Marked is_active = 'False'.

Reconciled/deactivated companies absent from feed (2): ['5610', '5812']
```

No manual SQL correction was used.

This is important: the state lifecycle was proven through the real watcher pipeline.

---

# 32. TEST CONTAMINATION INCIDENT: `Old Corp`

During Live Opportunities development, the local dev DB displayed:

`Old Corp`

Investigation proved it was synthetic test data.

The test suite had a fixture with approximately:

```python
CompanyRecord(id="100", company="Old Corp")
```

The fixture had contaminated the local default `watcher.sqlite`.

The frontend was correctly rendering database truth; it was not a frontend-hardcoding bug.

Correct fix:

- identify test DB contamination
- isolate tests from the default application DB
- remove the synthetic local record
- tighten public active query

Incorrect fix that was deliberately avoided:

- hide `"Old Corp"` in React
- blacklist names
- hardcode production companies

---

# 33. TEST DATABASE ISOLATION

Tests were updated so integration tests cannot accidentally write fixture data to the default development/production-style `watcher.sqlite`.

The synthetic test fixtures remain useful, but they now use isolated test DB state.

Relevant areas:

- `tests/conftest.py`
- `tests/integration/test_pipeline.py`
- `tests/integration/test_watcher_v1_pipeline.py`
- `tests/unit/test_feed_reconciliation.py`

---

# 34. SQLITE WAL/SHM GIT HYGIENE

`.gitignore` was updated to include:

```gitignore
*.sqlite-wal
*.sqlite-shm
```

Runtime files such as:

- `watcher.sqlite-wal`
- `watcher.sqlite-shm`

must never be committed.

Before the release commit/push, both were verified ignored with `git check-ignore`.

---

# 35. CURRENT AUTOMATED TEST BASELINE

Before `dd2cb76`, Sprint 1 had:

`76 passed`

After feed reconciliation/test expansion:

`84 passed`

Current verified test result:

```text
84 passed
0 failed
0 skipped
28 warnings
```

The final committed-tree run passed before push.

---

# 36. FRONTEND QUALITY GATES

Final pre-push frontend lint:

```text
npm run lint
Found 0 warnings and 0 errors.
```

Final production build:

```text
npm run build
tsc -b && vite build
```

Result:

successful.

Vite version observed:

`8.3.1`

Modules transformed:

`2314`

---

# 37. CURRENT PRODUCTION STATIC BUNDLE

Current generated production bundle includes:

- `/assets/index-BuFY8_Gw.js`
- `/assets/index-dY7UP4d6.css`
- `/assets/icons-CftBXZnI.js`
- `/assets/motion-CXcH43XN.js`
- `/assets/vendor-CmVsbtkv.js`
- `/assets/rolldown-runtime-CbXtAM7H.js`

Observed sizes during build:

- `index.html`: ~1.66 KB
- CSS: ~31.48 KB
- runtime: ~0.58 KB
- icons: ~20.19 KB
- main JS: ~110.97 KB
- motion: ~127.87 KB
- vendor: ~210.61 KB

Old bundle references such as:

- `index-Bu8HUBSn.js`
- `index-CYh2KHFt.css`
- `icons-md-XOHHw.js`

are no longer referenced by public HTML.

---

# 38. STATIC BUILD OUTPUT POLICY

`app/static/` is intentionally tracked in Git.

The project historically commits built frontend assets because FastAPI serves them in production.

Current relevant commit:

`e2d548c build(static): regenerate production frontend bundle`

Do not treat `app/static/` as disposable untracked build output unless deployment architecture is intentionally redesigned later.

---

# 39. CURRENT FRONTEND DESIGN

The old V1 UI has been replaced by a major cinematic redesign.

Current visual direction:

- dark institutional/cinematic aesthetic
- high-contrast typography
- restrained technical styling
- animated but not “Web3/neon”
- production-oriented visual polish

Current page title:

`TPO WATCHER — Autonomous Placement Intelligence`

Primary typography:

- Anton
- Onest
- JetBrains Mono

Motion:

- Framer Motion
- custom motion wrappers/components

---

# 40. CURRENT FRONTEND COMPONENT ARCHITECTURE

Important current components include:

## Core shell
- `Navbar.tsx`
- `Footer.tsx`
- `Preloader.tsx`

## Motion utilities
- `motion/FadeUp.tsx`
- `motion/LetterReveal.tsx`
- `motion/LineReveal.tsx`
- `motion/Marquee.tsx`

## Landing-page sections
- `sections/HeroSection.tsx`
- `sections/LiveOpportunities.tsx`
- `sections/CoreCapabilities.tsx`
- `sections/MonitoringPipeline.tsx`
- `sections/RecentDetections.tsx`
- `sections/TechnicalStory.tsx`

## Shared student controls
- `BranchSelector.tsx`
- `PreferenceCards.tsx`

---

# 41. CURRENT FRONTEND ROUTES / PAGES

Known public SPA routes include:

- `/`
- `/signup`
- `/preferences`
- `/verify`
- `/unsubscribe`

Current page source files include:

- `ErrorPage.tsx`
- `PreferencesPage.tsx`
- `SignupPage.tsx`
- `UnsubscribePage.tsx`
- `VerifyPage.tsx`

Frontend API helper:

`frontend/src/lib/api.ts`

---

# 42. LIVE OPPORTUNITIES UI BEHAVIOR

`LiveOpportunities.tsx` is dynamic and API-driven.

No hardcoded company arrays.

Current behaviors include:

- loading state
- empty state
- error state
- compact stacked cards
- desktop hover expansion
- mobile/tablet tap/click expansion
- selected-card emphasis
- compressed/receded non-selected cards
- missing fields omitted cleanly
- packages shown only when present
- deadlines shown only when present
- eligible programmes shown from real data
- category/type shown from real data

Hero left-side narrative/content was preserved during the surgical Live Opportunities integration.

---

# 43. FRONTEND HARDCODING RULE

There must be no hardcoded live opportunity companies in:

- `LiveOpportunities.tsx`
- `HeroSection.tsx`
- frontend fallback arrays
- sample opportunity objects
- name allowlists/denylists

Searches for BMC and other known companies were performed during validation.

The dynamic company data source is:

`GET /api/v1/opportunities`

---

# 44. CURRENT API / UI VERIFIED PRODUCTION RESULT

Production `/api/v1/opportunities` returns:

```text
CITI
Technip Energies
Visteon
BMC Software
```

The production UI displays the same four cards.

Current header:

`04 ACTIVE`

Console during browser smoke:

`0 errors / 0 warnings`

No stale Siemens or Mastercard card appears.

---

# 45. AUTHENTICATION ROBUSTNESS CHANGES IN dd2cb76

Two internal watcher auth robustness changes were added.

## `app/auth/manager.py`

`is_authenticated()` was improved to recognize:

- redirect to root login page `https://tpo.vierp.in/`
- presence of password input

Reason:

VIERP’s login page can be the root URL rather than a path containing `"login"`.

Old logic could incorrectly treat an expired session as authenticated.

## `app/tpo/client.py`

`fetch_companies()` error handling was improved to recognize login-page redirects/password input and raise `AuthenticationError`.

This enables automatic re-login recovery.

These changes:

- do not bypass Altcha
- do not weaken authentication
- improve session-expiry detection
- preserve the normal real login flow

Post-deployment production logs showed routine 401/session expiry recovery working without crash loops.

---

# 46. SPRINT 1 SECURITY BASELINE

Sprint 1:

**Identity & Session Security Hardening**

Historical baseline commit:

`64e6a33`

Status:

**COMPLETE + DEPLOYED + VERIFIED**

The current release `e2d548c` builds on this baseline.

No evidence from the UI/reconciliation release indicated regression of Sprint 1 controls.

---

# 47. SPRINT 1 SECURITY FEATURES

## Action tokens
- CSPRNG
- `secrets.token_urlsafe(32)`
- ~256-bit entropy
- SHA-256 hash at rest
- explicit expiry
- single-use
- replay protection
- supersession where applicable

## Sessions
Format:

`user_id:expires_at:nonce:signature`

Nonce:

- `secrets.token_hex(32)`
- 256-bit random nonce
- 64 hex characters

Signing:

- HMAC-SHA256
- `hmac.compare_digest`

Legacy 3-part sessions rejected.

## Revocation
- process-local revoked-session cache
- TTL pruning
- lock/thread safety
- persistent user `is_active` check remains across restart

## Cookies
- HttpOnly
- Secure
- SameSite=Lax
- Path=/
- Max-Age=3600

## Rate limiting
Process-local sliding window.

Known limits:

- signup IP: 30 / 60 seconds
- signup email: 5 / 15 minutes
- verify IP: 30 / 60 seconds
- unsubscribe IP: 30 / 60 seconds
- magic-link request IP: 30 / 60 seconds
- magic-link request email: 5 / 15 minutes
- magic-link exchange IP: 30 / 60 seconds
- preference update IP: 30 / 60 seconds

## Enumeration protection
Equivalent behavior for existing vs non-existing email in sensitive auth flows.

## Validation
- strict Pydantic
- `extra="forbid"`
- length limits
- control-character rejection
- token validation
- branch ID validation

## Client IP handling
Hardened for current topology:

`Client → Caddy → Watcher`

If Cloudflare/ALB/another proxy is added, revisit trusted-hop logic.

## Credential isolation
Student routes must not access:

- TPO_USERNAME
- TPO_PASSWORD
- AuthManager
- Playwright state
- watcher scraping operations

---

# 48. SPRINT 1 VERIFIED OBJECTIVES

The Sprint 1 production security report verified all 19 objectives:

1. CSPRNG action tokens
2. SHA-256 token hashing
3. explicit token expiry
4. single-use/replay protection
5. token supersession
6. secure 4-part session format
7. 256-bit session nonce
8. HMAC-SHA256 signatures
9. constant-time `compare_digest`
10. legacy session rejection
11. session revocation + TTL pruning
12. persistent `is_active`
13. secure cookie attributes
14. rate limiting
15. email enumeration mitigation
16. strict input validation
17. hardened client IP handling
18. TPO credential isolation
19. regression suite

Historical test result:

`76/76 passed`

Current overall suite:

`84/84 passed`

---

# 49. SMTP TEST INCIDENT AND FIX

Earlier local integration tests accidentally reached real Gmail SMTP.

Fake test addresses included examples such as:

- `verify_me@vit.edu`
- `student2028@vit.edu`
- `flow_test_2028@vit.edu`

Read-only production audit proved:

- these fake addresses were not in production
- production users were legitimate
- no production notification deliveries existed at that time

Fix implemented in:

`c0e12e1 fix(test): isolate email delivery from live SMTP`

Key changes:

- `get_email_service()` dependency factory
- `FakeEmailService` in integration tests
- autouse SMTP guard in `tests/conftest.py`
- monkeypatches `smtplib.SMTP` and `SMTP_SSL`
- test execution fails if real SMTP is attempted

Production EmailService behavior remained unchanged.

During the controlled real local VIT ingestion for feed-reconciliation testing:

- real SMTP was not invoked
- delivery rows remained 0
- local test/dev DB had 0 users

During production stale-record reconciliation:

- production notification deliveries remained 0

---

# 50. CURRENT PRODUCTION USER / DELIVERY COUNTS

Latest verified production counts:

- users: `4`
- notification deliveries: `0`

Do not expose subscriber identities during audits.

Do not create fake production users merely for testing.

---

# 51. CURRENT B.TECH BRANCH CATALOG

Current selectable B.Tech programs:

1. Computer Engineering
2. Computer Science and Engineering (Data Science)
3. Information Technology
4. Computer Science and Engineering (Internet of Things and Cyber Security Including Blockchain Technology)
5. Computer Science and Engineering (Artificial Intelligence)
6. Electronics and Telecommunication Engineering
7. Computer Science and Engineering (Artificial Intelligence and Machine Learning)
8. Instrumentation and Control Engineering
9. Artificial Intelligence and Data Science
10. Mechanical Engineering
11. Computer Engineering (Software Engineering)
12. Civil Engineering

UI grouping:

## Computing / Computer Science / IT
8 programs

## Electronics & Control
2 programs

## Mechanical
1 program

## Civil
1 program

Canonical IDs:

Existing:

- `VIT_CE`
- `VIT_IT`
- `VIT_AIDS`
- `VIT_CSE_AI`
- `VIT_CSE_AIML`
- `VIT_ENTC`
- `VIT_MECH`

Added:

- `VIT_CSE_DS`
- `VIT_CSE_IOT_CS_BC`
- `VIT_ICE`
- `VIT_CE_SE`
- `VIT_CIVIL`

Historical/backward-compatible but not selectable:

- `VIT_CS_AI`
- `VIT_ELEC`

Matching audit previously tested 54 patterns with 0 cross-branch leakage.

Important collision protections:

- `VIT_CE` must not match `VIT_CE_SE`
- `VIT_CSE_AI` must not collide with `VIT_CSE_AIML`

---

# 52. CURRENT PUBLIC WEBSITE DESIGN PRINCIPLES

The redesign should remain:

- institutional
- technically credible
- cinematic but restrained
- polished
- readable
- responsive
- not fake-dashboard heavy
- not neon/Web3
- not misleading about monitoring frequency

Avoid claims such as:

- instant
- real-time
- continuous

unless the underlying system changes to support them.

Truthful monitoring language should reflect scheduled/periodic checks.

---

# 53. CURRENT PRODUCTION DEPLOYMENT PROCEDURE

The proven safe deployment procedure for application releases is:

1. Read-only production audit.
2. Verify exact current production HEAD.
3. Verify `origin/main`.
4. Verify incoming commits do not overlap local infra drift.
5. Verify health, volumes, disk.
6. Create timestamped SQLite backup using SQLite online backup API.
7. Record rollback baseline.
8. `git merge --ff-only origin/main`
9. Verify local Compose/Caddy still preserved.
10. `docker compose build watcher`
11. `docker compose up -d --no-deps watcher`
12. Keep Caddy running.
13. Inspect watcher startup logs.
14. Verify local health.
15. Verify public health.
16. Verify API.
17. Verify public frontend bundle.
18. Verify volume/state preservation.
19. Allow normal scheduler to perform real reconciliation.
20. Perform read-only post-reconciliation audit.

Do not use `git pull` blindly.

Do not restart the entire stack unless specifically necessary.

---

# 54. e2d548c DEPLOYMENT DETAILS

Deployment date:

2026-10-03

Pre-deployment HEAD:

`64e6a33`

Target:

`e2d548c`

Fast-forward:

```text
Updating 64e6a33..e2d548c
Fast-forward
46 files changed
3066 insertions
1101 deletions
```

Build:

successful

New image ID:

`3e76fb199380`

Watcher recreation:

successful

Caddy:

remained running continuously

Local health:

200

Public health:

200

Production frontend:

new cinematic bundle served

Opportunities API:

HTTP 200

Rollback:

not required

---

# 55. PRE-DEPLOYMENT PRODUCTION COMPANY STATE

Before deploying feed reconciliation, production had six records marked active:

- Mastercard
- BMC Software
- Visteon
- Siemens
- Technip Energies
- CITI

Reason:

old production code did not reconcile missing feed records.

This was expected.

The public API immediately after deployment returned 5 due to the API `LIMIT 5`, including stale Siemens.

No manual DB edits were made.

The scheduled watcher then corrected state naturally.

---

# 56. POST-DEPLOYMENT VERIFICATION RESULT

Final release verification was performed on 2026-10-04.

Verified:

- scheduled watcher ran
- authentication succeeded
- Altcha flow/re-auth succeeded
- four live companies returned
- Mastercard deactivated
- Siemens deactivated
- BMC remained active
- Visteon remained active
- Technip Energies remained active
- CITI remained active
- API returned exactly four records
- UI displayed exactly four records
- `04 ACTIVE`
- no browser errors/warnings
- notification deliveries remained 0
- local health good
- HTTPS health good
- watcher running
- Caddy running
- backup intact

Final release verdict:

**RELEASE FULLY VERIFIED**

---

# 57. DISK CONSTRAINT

EC2 root disk:

approximately `8.0 GB`

Latest observed:

- used: ~`6.4 GB`
- available: ~`1.7 GB`
- utilization: ~`80%`

Docker image size is large because of Playwright/Chromium.

Do not run blind cleanup.

Never casually run:

```text
docker system prune
docker volume prune
```

Inspect first.

Preserve the previous image when practical until deployment is verified.

---

# 58. DOCKERFILE / BASE IMAGE

Known production base image:

`mcr.microsoft.com/playwright/python:v1.44.0-jammy`

The image includes browser/runtime dependencies needed for Playwright.

ARM64 deployment is confirmed working.

---

# 59. PRODUCTION SAFETY RULES

Never casually:

- delete `/app/data/watcher.sqlite`
- delete `/app/data/playwright_state.json`
- reset the database
- delete `watcher_data`
- delete Caddy volumes
- run `docker compose down -v`
- run `docker system prune`
- run `docker volume prune`
- overwrite `.env`
- print `.env.save`
- expose secrets
- print Playwright storage state
- expose private keys
- force-push
- blindly reset production Git
- clean untracked production files
- remove Caddy
- expose port 8000 publicly
- bypass Altcha
- send real emails for testing
- mutate real subscriber preferences for smoke tests
- unsubscribe real users for testing
- consume real user tokens

Always follow:

**inspect → backup → exact commit verification → minimal change → health/data verification**

---

# 60. DATABASE BACKUP RULE

For live SQLite, prefer SQLite’s online backup API.

Do not rely on raw `shutil.copy2()` of the live database if concurrent writes/WAL may exist.

Proven pattern:

```python
src = sqlite3.connect("/app/data/watcher.sqlite")
dst = sqlite3.connect("/app/data/backup.sqlite")
src.backup(dst)
dst.close()
src.close()
```

This was used successfully before deploying `e2d548c`.

---

# 61. ROLLBACK PRINCIPLES

Rollback must preserve:

- `/app/data`
- SQLite
- Playwright state
- Caddy
- Docker volumes
- local Compose/Caddy drift

Do not use:

`docker compose down -v`

If application rollback is needed:

- identify prior good Git commit
- preserve local infra files
- rebuild watcher only
- recreate watcher only
- keep Caddy running

Database restore is only needed if data mutation itself was unacceptable.

Do not improvise destructive restores.

---

# 62. CURRENT SECURITY-HARDENING ROADMAP

Overall security target is **not complete**.

## Sprint 1 — COMPLETE + VERIFIED
Identity/session hardening.

## Sprint 2 — NEXT
Web/API/application security.

Expected scope:

- authorization
- IDOR/object-level authorization
- function-level authorization
- CSRF
- API hardening
- content-type enforcement
- request-size limits
- malformed payload handling
- mass-assignment review
- output/XSS review
- browser security headers
- CSP
- HSTS
- X-Content-Type-Options
- Referrer-Policy
- frame protections
- CORS
- safe error behavior
- docs/OpenAPI exposure review if relevant

## Sprint 3
Data/secrets/logging/database safety.

Expected scope:

- data minimization
- secret handling
- logging without second-order leakage
- audit events
- error information leakage
- TPO credential isolation re-verification
- DB safety
- backup retention
- encrypted/offsite backup
- restore testing
- token lifecycle cleanup/pruning if not already done

## Sprint 4
Infrastructure / Docker / EC2 / supply chain.

Expected scope:

- non-root container where feasible
- dropped capabilities
- no-new-privileges
- read-only FS feasibility
- Docker socket exposure audit
- minimal/pinned base images
- EC2 SSH/key-only hardening
- SG review
- OS patching
- unnecessary services
- IMDSv2
- dependency scanning
- `pip-audit`
- `npm audit`
- container scanning
- Dependabot
- SBOM/provenance

## Final release gate
Comprehensive security audit after all sprints.

---

# 63. SPRINT 1 FOLLOW-UPS STILL OPEN

Earlier verified follow-ups included:

## Browser security headers
- Content-Security-Policy
- HSTS
- X-Content-Type-Options
- Referrer-Policy
- frame protections

## Backups
- encrypted/offsite SQLite backups
- S3 or similar isolated destination
- versioning/lifecycle
- restore testing

## Token lifecycle
- automated cleanup/pruning of expired action tokens
- earlier report suggested around 30-day cleanup retention

These are not fully implemented merely because the current release is verified.

---

# 64. RATE-LIMITER / REVOCATION LIMITATION

Current rate limiter is process-local.

Current revoked-session cache is process-local.

Therefore state resets on process/container restart.

Persistent `users.is_active` remains in SQLite and survives restart.

This is acceptable for the current single-container deployment.

If scaling to multiple application instances:

use shared state or redesign.

---

# 65. REVERSE-PROXY TRUST LIMITATION

Client-IP parsing assumes:

```text
Client → Caddy → Watcher
```

If adding:

- Cloudflare
- AWS ALB
- another proxy layer

revisit forwarded-IP trust logic.

Do not preserve the current hop assumptions blindly.

---

# 66. ELASTIC IP — NOT YET DONE

No Elastic IP has been confirmed.

The EC2 public IP changed in the past after stop/start.

Future reliability improvement:

- allocate/attach Elastic IP
- verify DuckDNS
- verify HTTPS
- verify Caddy
- verify health
- verify SSH rules

Treat this separately from application-security work.

---

# 67. CURRENT SSH OPERATIONAL NOTE

The user’s public IP changes periodically.

Recent sequence:

- `58.84.60.36`
- `58.84.61.177`
- current known: `58.84.62.92`

When SSH times out while HTTPS still works:

1. check current public IP
2. update only the personal SSH `/32` SG rule
3. retry SSH
4. do not open SSH globally

---

# 68. PUBLIC UI / BACKEND SAME-ORIGIN MODEL

Current production serves frontend and API from the same domain:

`https://tpowatcher.duckdns.org`

This is preferable to unnecessary cross-origin complexity.

Any future CORS change should be evidence-driven.

Do not add wildcard credentialed CORS.

---

# 69. CURRENT IMPORTANT SOURCE FILES

Backend/app:

- `app/api/app.py`
- `app/api/routes_opportunities.py`
- `app/auth/manager.py`
- `app/database/repository.py`
- `app/monitoring/detector.py`
- `app/monitoring/watcher.py`
- `app/tpo/client.py`
- `app/tpo/models.py`

Frontend:

- `frontend/src/App.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/index.css`
- `frontend/src/components/Navbar.tsx`
- `frontend/src/components/Footer.tsx`
- `frontend/src/components/Preloader.tsx`
- `frontend/src/components/BranchSelector.tsx`
- `frontend/src/components/PreferenceCards.tsx`
- `frontend/src/components/motion/*`
- `frontend/src/components/sections/*`
- `frontend/src/pages/*`

Tests:

- `tests/conftest.py`
- `tests/integration/test_api_v1.py`
- `tests/integration/test_pipeline.py`
- `tests/integration/test_scheduler_execution.py`
- `tests/integration/test_security_sprint1.py`
- `tests/integration/test_watcher_v1_pipeline.py`
- `tests/unit/test_branch_structure.py`
- `tests/unit/test_detector.py`
- `tests/unit/test_feed_reconciliation.py`
- `tests/unit/test_matcher_fanout_worker.py`
- `tests/unit/test_repository_v1.py`
- `tests/unit/test_scheduler.py`

Deployment:

- `Dockerfile`
- `docker-compose.yml`
- `Caddyfile`
- `app/static/`

---

# 70. CURRENT TEST SUITE COMPOSITION

Latest observed test suite:

```text
tests/integration/test_api_v1.py
tests/integration/test_pipeline.py
tests/integration/test_scheduler_execution.py
tests/integration/test_security_sprint1.py
tests/integration/test_watcher_v1_pipeline.py
tests/unit/test_branch_structure.py
tests/unit/test_detector.py
tests/unit/test_feed_reconciliation.py
tests/unit/test_matcher_fanout_worker.py
tests/unit/test_repository_v1.py
tests/unit/test_scheduler.py
```

Total:

`84 tests`

All passed before release.

---

# 71. FEED RECONCILIATION TESTS

`tests/unit/test_feed_reconciliation.py` added eight focused tests.

Coverage includes:

A. missing active company becomes False  
B. new source company gets inserted/upserted  
C. identical feed causes no unnecessary change  
D. fetch failure preserves active state  
E. malformed response preserves active state  
F. empty feed preserves active state  
G. inactive historical records remain inactive and are not deleted  
H. NULL/test records are not accidentally activated

---

# 72. PRODUCTION STATIC SERVING

FastAPI production deployment serves built SPA files from:

`app/static/`

The current public HTML references the `e2d548c` bundle.

This is why frontend source changes must be followed by a production build and committed static asset refresh under the current deployment model.

---

# 73. CURRENT LOCAL DEVELOPMENT RELEASE STATE

Immediately after the controlled commit/push:

- local branch: `main`
- local working tree: clean
- origin/main: `e2d548c`
- runtime SQLite WAL/SHM ignored
- 84 tests passed
- lint passed
- build passed

No production deployment happened until after a separate pre-deployment read-only audit.

This separation is the preferred workflow.

---

# 74. PROVEN CHANGE-MANAGEMENT WORKFLOW

Use this lifecycle:

```text
READ-ONLY AUDIT
      ↓
evidence-based plan
      ↓
local implementation
      ↓
tests/lint/build
      ↓
pre-commit audit
      ↓
logical commits
      ↓
push
      ↓
STOP
      ↓
production read-only audit
      ↓
backup
      ↓
fast-forward
      ↓
build watcher only
      ↓
recreate watcher only
      ↓
health/UI/API verification
      ↓
allow real scheduled reconciliation
      ↓
post-reconciliation read-only audit
```

Do not collapse all stages into one broad agent task.

---

# 75. CURRENT PRODUCTION RELEASE VERDICT

Current release:

`e2d548c`

Status:

**RELEASE FULLY VERIFIED**

This means:

- code committed
- code pushed
- production deployed
- watcher healthy
- Caddy healthy
- static bundle live
- API live
- database preserved
- Playwright state preserved
- scheduler functioning
- auth/session recovery functioning
- feed reconciliation functioning
- stale companies deactivated naturally
- UI reflects current source
- no notification side effect from deactivation
- backup intact

---

# 76. CURRENT “SOURCE OF TRUTH” INVARIANT

The intended invariant is now:

```text
A company shown in Live Opportunities
        ↓
must be returned by /api/v1/opportunities
        ↓
must exist in SQLite with is_active='True'
        ↓
must reflect current authoritative VIT TPO ingestion state
```

No frontend exceptions.

No company-name blacklist.

No screenshot-driven hardcoding.

No hand-curated “live” array.

---

# 77. WHAT NOT TO DO WITH CURRENT OPPORTUNITIES

Do not:

- hide stale companies by name in React
- filter on registration date in the frontend without explicit business semantics
- manually set active flags just to make UI look correct
- manually insert portal companies
- treat `registration_end < today` as equivalent to inactive unless the official source semantics say so

The current lifecycle is source-driven.

---

# 78. WHY REGISTRATION END DATE IS NOT THE ACTIVE FLAG

During investigation, it was noted that the TPO dashboard may retain companies beyond registration closure for later placement stages.

Therefore an expired registration date is not automatically equivalent to “remove from live source.”

Current authoritative rule is based on successful current feed membership / source state.

---

# 79. PRODUCTION NOTIFICATION SAFETY

Production verification after stale-record deactivation:

`notification_deliveries = 0`

This is expected.

Deactivation of stale opportunities should not notify subscribers as if something new appeared.

If future reconciliation changes delivery behavior, treat as a regression.

---

# 80. CURRENT PRODUCTION WEBSITE STATUS

Public website:

`https://tpowatcher.duckdns.org`

Current experience:

- cinematic landing page
- preloader
- new navbar/footer
- live dynamic opportunities
- redesigned signup
- redesigned preferences
- redesigned verify
- redesigned unsubscribe
- error page
- motion effects
- responsive behavior

Browser smoke after deployment:

- page loaded
- typography loaded
- preloader dismissed
- Hero rendered
- Live Opportunities rendered
- 4 current company cards rendered
- no console errors
- no console warnings

---

# 81. CURRENT PUBLIC LIVE OPPORTUNITIES

At latest verification:

```text
04 ACTIVE

CITI
Technip Energies
Visteon
BMC Software
```

This matched:

- current VIT TPO source
- production SQLite active set
- public API
- frontend UI

---

# 82. CURRENT PRODUCTION INFRASTRUCTURE STATUS

Latest verified:

## Watcher
Running

Port:

`127.0.0.1:8000`

## Caddy
Running

Ports:

`80`, `443`

## Local health
Healthy

## Public health
Healthy

## SQLite
Present

## Playwright state
Present

## Pre-release backup
Present

## Scheduler
Running

## Git
At `e2d548c`

## Rollback required
No

---

# 83. CURRENT EC2 DISK / IMAGE CAUTION

Current watcher image:

`3e76fb199380`

Size:

~2.74 GB

Root disk free:

~1.7 GB

Do not repeatedly rebuild large images unnecessarily without monitoring disk.

Never prune volumes.

---

# 84. CURRENT ENVIRONMENT VARIABLE NAMES KNOWN

Known production/runtime variable names include:

- `TPO_USERNAME`
- `TPO_PASSWORD`
- `DB_PATH`
- `STATE_FILE`

Do not print values.

Other secret/config values may exist in `.env`; inspect names only when needed and never expose values in handoffs or logs.

---

# 85. CURRENT PRODUCT / SECURITY DECISIONS TO PRESERVE

1. VIT TPO is authoritative.
2. SQLite is the persisted source representation.
3. Public API is a read-only projection.
4. Frontend is presentation only.
5. Student auth and watcher auth remain separate.
6. Students never provide VIERP password.
7. Altcha is not bypassed.
8. No fake OAuth.
9. Production port 8000 remains localhost-only.
10. Caddy remains the public edge.
11. Persistent volume survives container recreation.
12. Production tests should be read-only where possible.
13. Real-user mutation is avoided during verification.
14. SMTP is isolated during tests.
15. Feed reconciliation never treats an anomalous empty response as “everything inactive.”
16. Historical companies remain in DB rather than being deleted.
17. UI must not compensate for data-layer integrity bugs.

---

# 86. NEXT RECOMMENDED MAJOR TASK

The next major engineering/security task should be:

## Sprint 2 — READ-ONLY Web/API/Application Security Gap Audit

Do not immediately code generic security changes.

Audit the current `e2d548c` codebase first.

Focus:

- authorization
- object-level authorization / IDOR
- function-level authorization
- CSRF
- API methods
- content type handling
- request size limits
- malformed body behavior
- mass assignment
- error leakage
- XSS
- URL handling
- email rendering
- CSP
- HSTS
- nosniff
- Referrer-Policy
- frame protections
- Permissions-Policy if relevant
- CORS
- docs/OpenAPI exposure
- action-token cleanup
- current same-origin assumptions

Classify:

- PASS
- PARTIAL
- FAIL
- NOT APPLICABLE
- NEEDS DECISION

Separate Sprint 2 blockers from Sprint 3/4 items.

---

# 87. NEXT SMALL PRODUCT ISSUE

Known non-blocking issue:

`NN ACTIVE` currently represents displayed returned count, not guaranteed global active total beyond API limit 5.

Possible future solutions:

A. Change copy to “NN SHOWN”  
B. Return `{items, total_active}` from API  
C. Remove artificial limit if product wants every live opportunity  
D. Keep as-is if source never exceeds 5 and semantics are explicitly accepted

Do not change without product decision.

---

# 88. BACKUP / DISASTER RECOVERY FUTURE WORK

Current backups are local to EC2/volume.

Longer-term work:

- encrypted offsite backup
- S3 or equivalent
- isolated bucket
- versioning
- lifecycle policy
- documented retention
- restore drill
- periodic consistency checks

Do not assume local volume backups are sufficient disaster recovery.

---

# 89. INFRASTRUCTURE FUTURE WORK

Potential later improvements:

- Elastic IP
- disk expansion
- container hardening
- OS patching
- IMDSv2 confirmation
- SSH prefix-list understanding
- stricter filesystem permissions
- log retention
- monitoring/alerts
- dependency/container vulnerability scanning

Do not mix these into unrelated application changes.

---

# 90. PRODUCTION STATUS SUMMARY

```text
PROJECT
TPO-Watcher

PRODUCTION RELEASE
e2d548c

RELEASE STATUS
DEPLOYED + RECONCILED + FULLY VERIFIED

DOMAIN
https://tpowatcher.duckdns.org

AWS
EC2 ap-south-1
i-03328ee0e60347254
ARM64 / aarch64

CURRENT PUBLIC IP
65.0.66.80
(ephemeral; no Elastic IP confirmed)

CURRENT PERSONAL SSH SOURCE
58.84.62.92/32
(can change)

WATCHER
running
127.0.0.1:8000

CADDY
running
80/443 public

DATABASE
/app/data/watcher.sqlite
persistent
~96 KB

PLAYWRIGHT STATE
/app/data/playwright_state.json
persistent
~2 KB

BACKUP
/app/data/watcher_backup_pre_e2d548c_20261003_082126.sqlite

SCHEDULER
Asia/Kolkata
00:00
10:00
17:00

CURRENT ACTIVE COMPANIES
BMC Software
Visteon
Technip Energies
CITI

CURRENT ACTIVE COUNT
4

HISTORICAL / INACTIVE
Mastercard
Siemens

USERS
4

NOTIFICATION DELIVERIES
0

TESTS
84 passed

FRONTEND LINT
0 errors
0 warnings

FRONTEND BUILD
successful

SPRINT 1
VERIFIED

OVERALL SECURITY PROGRAM
NOT COMPLETE

NEXT MAJOR TASK
Sprint 2 read-only gap audit
```

---

# 91. CRITICAL DO-NOT-FORGET LIST FOR A NEW AI

1. Current production baseline is `e2d548c`, not `64e6a33`.
2. `64e6a33` remains the historical verified Sprint 1 security baseline.
3. Current release is fully deployed and fully reconciled.
4. Current active TPO companies are BMC Software, Visteon, Technip Energies, and CITI.
5. Siemens and Mastercard are historical/inactive.
6. Live Opportunities are fully data-driven.
7. Do not hardcode company names.
8. VIT TPO is the source of truth.
9. Do not filter by company name in React.
10. Do not infer active state solely from registration end date.
11. Reconciliation uses stable VIERP IDs.
12. Empty feed never deactivates everything.
13. Failed fetch never triggers reconciliation.
14. Residual valid-partial-server-response risk exists but is currently accepted.
15. Test DB contamination with `Old Corp` was fixed.
16. Integration tests are isolated from the default DB.
17. `.sqlite-wal` and `.sqlite-shm` are ignored.
18. Current suite is 84 passing tests.
19. Caddy must remain running.
20. Watcher port 8000 must remain localhost-only.
21. Production Compose/Caddy drift is intentional.
22. Do not clean/reset production worktree blindly.
23. Do not expose `.env.save`.
24. Do not delete Docker volumes.
25. Use SQLite online backup API before risky releases.
26. Pre-e2d548c backup exists and is verified.
27. Students must never provide VIERP passwords.
28. Do not bypass Altcha.
29. Do not fake OAuth.
30. Current personal SSH SG source is `58.84.62.92/32`, but it may change.
31. Do not open SSH to `0.0.0.0/0`.
32. An additional SSH prefix-list rule exists; do not remove it without understanding it.
33. No Elastic IP is confirmed.
34. Disk space is tight (~1.7 GB free).
35. No blind Docker prune.
36. Production users = 4.
37. Notification deliveries = 0 at latest verification.
38. Current Live Opportunities count text is a returned-slice count.
39. Overall security hardening is not complete.
40. Next major task is Sprint 2 read-only security audit on `e2d548c`.

---

# 92. ONE-PARAGRAPH CURRENT STATE

TPO-Watcher is a live VIT Pune placement-opportunity monitoring service deployed on an ARM64 AWS EC2 instance in Mumbai and served through Caddy at `https://tpowatcher.duckdns.org`. The current production release is `e2d548c229f036dc7d67b85b7bdd4a346d254ac4`, built on the previously verified Sprint 1 security baseline `64e6a33`. The release added a read-only Live Opportunities API, authoritative VIT TPO feed reconciliation, improved VIERP session-expiry recovery, a major cinematic React/Vite frontend redesign, a fully dynamic Live Opportunities hero, isolated test databases, and updated static production assets. The release passed 84 backend tests, frontend lint with zero warnings/errors, and a clean production build. It was deployed with a transactional SQLite backup, watcher-only container recreation, uninterrupted Caddy, preserved SQLite and Playwright state, healthy local/public endpoints, and successful production browser smoke tests. Subsequent scheduled watcher runs successfully reconciled stale Mastercard and Siemens records to inactive while preserving BMC Software, Visteon, Technip Energies, and CITI as the four current active opportunities. The public API and website now match the authoritative VIT TPO feed 1:1. The release is **FULLY VERIFIED**. The overall security-hardening roadmap remains incomplete; the next major task is a read-only Sprint 2 application/web/API security gap audit against the `e2d548c` baseline.

---

# END OF CANONICAL HANDOFF
