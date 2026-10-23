"""
Generate sanitized local HTML preview files for inspection.
Includes:
- Cummins India ID 5952 (verified registration dates)
- Cummins India ID 5971 (dates not provided by TPO)
- Cummins India UPDATE notification (status changes)
"""

import os
from pathlib import Path
from app.tpo.models import CompanyRecord
from app.subscribers.worker import DeliveryWorker

ARTIFACT_DIR = Path(r"C:\Users\kusha\.gemini\antigravity-ide\brain\a82e09f9-b0e9-4c4f-98d5-74e4e039608e\previews")
LOCAL_DIR = Path(r"c:\Projects\TPO - Watcher\reports\previews")

ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_DIR.mkdir(parents=True, exist_ok=True)

worker = DeliveryWorker(delivery_repo=None, token_repo=None)

# 1. Cummins India ID 5952 (Complete historical dates)
comp_5952 = CompanyRecord(
    id="5952",
    company="Cummins India",
    company_code="CUMMINSINDIA2026-271",
    placementtype="Internship + Performance based PPO",
    internshiptype=None,
    maxPackage="25000.0",
    minPackage="25000.0",
    regStartdate="13-Oct-2026",
    regStarttime="11:31",
    regEnddate="14-Oct-2026",
    regEndtime="11:00",
    academicyear="2026-27",
    organization="VIT",
    tpoprogram="VIT-BTech-Computer Engineering, VIT-BTech - Computer Science and Engineering (Artificial Intelligence and Machine Learning), VIT-BTech-Artificial Intelligence & Data Science, VIT-BTech-Electronics and Telecommunication Engg, VIT-B.Tech. Computer Science and Artificial Intelligence, VIT-BTech-Instrumentation and Control Engineering, VIT-null, VIT-BTech-Information Technology, VIT-BTech-Computer Science and Engineering (Artificial Intelligence), VIT-BTech-Mechanical Engineering",
    isactive="True",
)

html_5952 = worker._format_opportunity_email(
    company_record=comp_5952,
    notif_type="NEW",
    user_branch="Computer Science and Engineering (Data Science)",
    unsub_url="https://tpowatcher.duckdns.org/unsubscribe#token=SANITIZED_UNSUB_TOKEN_SAMPLE",
    pref_url="https://tpowatcher.duckdns.org/preferences/confirm#token=SANITIZED_PREF_TOKEN_SAMPLE",
    event_at="2026-10-13T11:30:14+00:00",
)

# 2. Cummins India ID 5971 (Dates absent from TPO portal)
comp_5971 = CompanyRecord(
    id="5971",
    company="Cummins India",
    company_code="CUMMINSINDIA2026-272",
    placementtype="Internship + Performance based PPO",
    internshiptype=None,
    maxPackage="25000.0",
    minPackage="25000.0",
    regStartdate=None,
    regStarttime=None,
    regEnddate=None,
    regEndtime=None,
    academicyear="2026-27",
    organization="VIT",
    tpoprogram="VIT-BTech-Computer Engineering, VIT-BTech - Computer Science and Engineering (Artificial Intelligence and Machine Learning), VIT-BTech-Artificial Intelligence & Data Science, VIT-BTech-Electronics and Telecommunication Engg, VIT-B.Tech. Computer Science and Artificial Intelligence, VIT-BTech-Instrumentation and Control Engineering, VIT-null, VIT-BTech-Information Technology, VIT-BTech-Computer Science and Engineering (Artificial Intelligence), VIT-BTech-Mechanical Engineering",
    isactive="True",
)

html_5971 = worker._format_opportunity_email(
    company_record=comp_5971,
    notif_type="NEW",
    user_branch="Computer Science and Engineering (Data Science)",
    unsub_url="https://tpowatcher.duckdns.org/unsubscribe#token=SANITIZED_UNSUB_TOKEN_SAMPLE",
    pref_url="https://tpowatcher.duckdns.org/preferences/confirm#token=SANITIZED_PREF_TOKEN_SAMPLE",
    event_at="2026-10-15T11:30:13+00:00",
)

# 3. Cummins India UPDATE Notification
html_update = worker._format_opportunity_email(
    company_record=comp_5952,
    notif_type="UPDATE",
    user_branch="Computer Science and Engineering (Data Science)",
    unsub_url="https://tpowatcher.duckdns.org/unsubscribe#token=SANITIZED_UNSUB_TOKEN_SAMPLE",
    pref_url="https://tpowatcher.duckdns.org/preferences/confirm#token=SANITIZED_PREF_TOKEN_SAMPLE",
    changes=["Active Status: False (Registration Closed / Company Inactive)", "Registration deadline passed on 14-Oct-2026 11:00"],
    event_at="2026-10-14T11:05:00+00:00",
)

files = [
    ("cummins_5952_preview.html", html_5952),
    ("cummins_5971_preview.html", html_5971),
    ("cummins_update_preview.html", html_update),
]

for filename, content in files:
    local_path = LOCAL_DIR / filename
    local_path.write_text(content, encoding="utf-8")
    artifact_path = ARTIFACT_DIR / filename
    artifact_path.write_text(content, encoding="utf-8")
    print(f"Generated: {local_path} and {artifact_path}")

print("Successfully generated all email preview files.")
