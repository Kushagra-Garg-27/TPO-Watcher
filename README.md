# VIT TPO Placement Watcher

A reliable, 24/7 personal monitoring service that monitors the VIT TPO (Training and Placement Office) portal and automatically emails you the moment a new company or internship opportunity appears on your dashboard.

---

## 1. What the Service Does
The VIT TPO Placement Watcher runs autonomously inside Docker to monitor the VIT TPO student portal (`https://tpo.vierp.in/`). When active:
- It maintains an authenticated browser session using headless Chromium via Playwright.
- It queries the official TPO API endpoint (`https://tpoapi.vierp.in/TPOCompanyScheduling/newschedulesdcopanies`).
- It tracks company records using their unique numeric IDs (e.g. `5610`, `5705`, `5785`, `5812`).
- It detects newly posted companies and critical updates (deadline changes, salary package changes, eligibility updates).
- It queues and dispatches HTML email notifications via SMTP.

---

## 2. When It Checks the TPO Portal
Rather than polling continuously every few minutes, the service remains running 24/7 in Docker and performs checks only at **configurable scheduled check windows** in the **`Asia/Kolkata` (IST)** timezone.

Between scheduled check times, the service sleeps quietly to conserve resources and avoid unnecessary requests to the TPO portal.

**Default Schedule:**
- **10:00 AM IST** (`10:00`)
- **5:00 PM IST** (`17:00`)
- **12:00 AM IST** (`00:00` / midnight)

---

## 3. How to Change `CHECK_TIMES`
You can customize the check schedule at any time by modifying the `CHECK_TIMES` variable in your `.env` file.

Use a comma-separated list of 24-hour `HH:MM` times:
```env
# Default: 10 AM, 5 PM, midnight IST
CHECK_TIMES=10:00,17:00,00:00
TIMEZONE=Asia/Kolkata
```

**Examples:**
- Check twice daily (9:00 AM and 6:00 PM IST):
  ```env
  CHECK_TIMES=09:00,18:00
  ```
- Check four times daily (8:00 AM, 12:00 PM, 4:00 PM, 8:00 PM IST):
  ```env
  CHECK_TIMES=08:00,12:00,16:00,20:00
  ```

After changing `.env`, restart the container using `docker compose restart`.

---

## 4. How Email Configuration Works
Email notifications are delivered over standard SMTP. To configure email alerts, provide your SMTP credentials in `.env`:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_16_character_app_password
EMAIL_FROM=your_email@gmail.com
EMAIL_TO=your_email@gmail.com
```

> **Note for Gmail users:** Regular Gmail passwords do not work with SMTP. You must enable 2-Step Verification on your Google Account and generate an **App Password** under Account Security.

If SMTP settings are left blank in `.env`, the service will still detect new companies and safely store notifications in the SQLite queue as `PENDING`, ready to be sent once SMTP credentials are provided.

---

## 5. How to Start Docker
To build and start the watcher in the background:
```bash
docker compose up -d --build
```
Verify the container is running:
```bash
docker compose ps
```

---

## 6. How to Stop Docker
To stop and remove the container:
```bash
docker compose down
```
*(Your SQLite database and baseline remain safe in the persistent `watcher_data` volume).*

---

## 7. How to Inspect Logs
To view live log output:
```bash
docker compose logs -f
```
To view the latest 50 lines:
```bash
docker compose logs --tail 50
```

The logs clearly show:
- Service initialization, timezone (`Asia/Kolkata`), and configured check times.
- The exact date and time of the next scheduled check.
- When a check starts and the authentication status.
- When the API request starts, succeeds, and the number of companies retrieved.
- Newly detected companies and pending notification dispatch status.

---

## 8. What Happens When the TPO Portal is Unavailable
If the TPO portal experiences downtime, network timeouts, or HTTP 5xx errors:
- **Zero-Company Safety Rule:** The service **never** assumes there are 0 companies available.
- Existing database records and baseline company IDs are preserved intact.
- No false alerts or deletions occur.
- An error is logged, and the watcher sleeps until the next scheduled check window to retry cleanly.

---

## 9. What Happens When Email Fails
The service uses a persistent pending notification queue:
1. When a new company is detected, a record is added to the `pending_notifications` table in SQLite.
2. If the email server is unreachable, credentials fail, or network drops, the notification remains in `PENDING` state.
3. At the next scheduled check, the service automatically retries all pending notifications.
4. Once email delivery succeeds, the notification is marked `SENT` and removed from the queue.
5. A company is never alerted twice, preventing duplicate spam.

---

## 10. What Happens When the Login Session Expires
Playwright stores session cookies and storage state in `playwright_state.json`. If the TPO session expires:
1. The watcher detects the expired session or 401/403 status.
2. It automatically launches the headless Chromium browser to perform a full re-authentication.
3. It fills credentials, solves the Altcha challenge, lands on the home dashboard, and updates the session storage.
4. It immediately retries the API request with the fresh authenticated session.
