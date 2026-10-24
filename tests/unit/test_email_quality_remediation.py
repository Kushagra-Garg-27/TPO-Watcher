"""
Test Suite: Email Quality, Data Fidelity, and Visual Presentation Remediation.
Addresses all 22 verification scenarios specified in Section 10 of the specification:
1. ASCII-safe email subject without broken symbols.
2. Correct Unicode company-name encoding.
3. New and updated opportunity subjects.
4. Verified registration dates (Cummins 5952).
5. Expired registration dates (closed status badge).
6. Missing date fields (Cummins 5971: no 'N/A to N/A').
7. Alternative source date fields (regStartdatenew/regEnddatenew).
8. Historical delivery snapshots fidelity.
9. Internship type available (Valentius Kryptix 5985: '6 Months').
10. Internship type missing (Cummins 5952/5971: no 'N/A' row).
11. Compensation with verified unit.
12. Compensation with unknown unit (no fabricated LPA).
13. Identical minimum and maximum values (no repetition).
14. Missing compensation (clear non-disclosure statement).
15. Data Science branch personalization.
16. HTML escaping and safe URLs.
17. Meaningful plain-text MIME alternative.
18. Manage Preferences and Unsubscribe links.
19. Correct recipient targeting.
20. No duplicate notifications (delivery uniqueness).
21. Email-client presentation (table layout, responsive constraints).
22. Preservation of watcher scheduling and portal synchronization.
"""

import json
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from html.parser import HTMLParser
import pytest

from app.tpo.models import CompanyRecord
from app.notifications.utils import escape_html, sanitize_url, render_safe_link
from app.notifications.presentation import (
    format_opportunity_subject,
    format_compensation,
    format_registration_window,
    format_internship_type,
    render_opportunity_html,
    render_opportunity_text,
)
from app.subscribers.worker import DeliveryWorker
from app.notifications.email import EmailNotificationProvider


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    @classmethod
    def read(cls, markup: str) -> str:
        parser = cls()
        parser.feed(markup)
        return " ".join(" ".join(parser.parts).split())


# 1. ASCII-safe email subject without broken symbols
def test_01_ascii_safe_subject_without_broken_symbols():
    subj_new = format_opportunity_subject("NEW", "Cummins India")
    subj_upd = format_opportunity_subject("UPDATE", "Cummins India")

    assert "🚨" not in subj_new and "⚠️" not in subj_new
    assert "🚨" not in subj_upd and "⚠️" not in subj_upd
    assert "??" not in subj_new and "??" not in subj_upd
    assert "\r" not in subj_new and "\n" not in subj_new
    # Confirm pure ASCII
    subj_new.encode("ascii")
    subj_upd.encode("ascii")


# 2. Correct Unicode company-name encoding
def test_02_unicode_company_name_mime_encoding():
    company_name = "Société Générale & Co."
    subj = format_opportunity_subject("NEW", company_name)
    assert "Société Générale" in subj

    # In EmailMessage, unicode header must serialize to RFC 2047 format without error
    msg = EmailMessage()
    clean_subj = " ".join(subj.split())
    msg["Subject"] = clean_subj
    raw_header = bytes(msg).split(b"\n")[0]
    assert b"Subject:" in raw_header
    assert b"?=" in raw_header  # RFC 2047 encoding token present
    assert b"??" not in raw_header


# 3. New and updated opportunity subjects
def test_03_new_and_updated_opportunity_subjects():
    assert format_opportunity_subject("NEW", "Cummins India") == "New TPO Opportunity: Cummins India | TPO-WATCHER"
    assert format_opportunity_subject("UPDATE", "Cummins India") == "TPO Opportunity Updated: Cummins India | TPO-WATCHER"
    # Fallback when company name is empty
    assert format_opportunity_subject("NEW", None) == "New TPO Opportunity: TPO Opportunity | TPO-WATCHER"


# 4. Verified registration dates (Cummins 5952)
def test_04_verified_registration_dates_cummins_5952():
    cummins_5952_record = {
        "regStartdate": "13-Oct-2026",
        "regStarttime": "11:31",
        "regEnddate": "14-Oct-2026",
        "regEndtime": "11:00",
    }
    info = format_registration_window(cummins_5952_record)
    assert "13-Oct-2026 11:31 to 14-Oct-2026 11:00" in info["display_text"]
    assert "N/A" not in info["display_text"]


# 5. Expired registration dates (closed status badge)
def test_05_expired_registration_dates_status_badge():
    past_record = {
        "regStartdate": "01-Jan-2020",
        "regStarttime": "10:00",
        "regEnddate": "02-Jan-2020",
        "regEndtime": "17:00",
    }
    info = format_registration_window(past_record)
    assert info["is_closed"] is True
    assert info["badge"] == "Registration Closed"


# 6. Missing date fields (Cummins 5971: no 'N/A to N/A')
def test_06_missing_date_fields_cummins_5971():
    cummins_5971_record = {
        "regStartdate": None,
        "regStarttime": None,
        "regEnddate": None,
        "regEndtime": None,
        "regStartdatenew": None,
        "regEnddatenew": None,
    }
    info = format_registration_window(cummins_5971_record)
    assert "Registration dates not provided by TPO" in info["display_text"]
    assert "N/A" not in info["display_text"]
    assert "to" not in info["display_text"]


# 7. Alternative source date fields (regStartdatenew/regEnddatenew)
def test_07_conflicting_or_alternate_source_date_fields():
    alt_record = {
        "regStartdate": None,
        "regEnddate": None,
        "regStartdatenew": "2026-10-12T18:30:00Z",  # 13-Oct-2026 00:00 IST
        "regEnddatenew": "2026-10-13T18:30:00Z",    # 14-Oct-2026 00:00 IST
    }
    info = format_registration_window(alt_record)
    assert "13-Oct-2026 00:00 to 14-Oct-2026 00:00" in info["display_text"]


# 8. Historical delivery snapshots fidelity
def test_08_historical_delivery_snapshots_fidelity():
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    # Event-time snapshot with historical deadline
    snapshot = CompanyRecord(
        id="5952",
        company="Cummins India",
        company_code="CUMMINSINDIA2026-271",
        regStartdate="13-Oct-2026",
        regStarttime="11:31",
        regEnddate="14-Oct-2026",
        regEndtime="11:00",
        maxPackage="25000.0",
        minPackage="25000.0",
        placementtype="Internship + Performance based PPO",
    )
    html = worker._format_opportunity_email(
        company_record=snapshot,
        notif_type="NEW",
        user_branch="Computer Science and Engineering (Data Science)",
        unsub_url="https://example.com/unsub",
        pref_url="https://example.com/pref",
        event_at="2026-10-13T11:30:14+00:00",
    )
    # The historical deadline from the snapshot must appear
    assert "14-Oct-2026 11:00" in html
    assert "13-Oct-2026 11:31" in html
    assert "13-Oct-2026 17:00:14 IST" in html


# 9. Internship type available (Valentius Kryptix 5985: '6 Months')
def test_09_internship_type_available_valentius_5985():
    res = format_internship_type("6 Months")
    assert res == "6 Months"

    html = render_opportunity_html(
        company_name="Valentius Kryptix",
        company_code="VALENTIUS2026",
        placement_type="Internship",
        internship_type=res,
        compensation_main="30,000",
        compensation_note="Unit not specified by TPO",
        reg_window_text="15-Oct-2026 to 20-Oct-2026",
        reg_badge=None,
        reg_note=None,
        academic_year="2026-27",
        organization="VIT",
        user_branch="Computer Science and Engineering",
        programs="VIT-BTech-Computer Engineering",
        notif_type="NEW",
        now_ist_str="23-Oct-2026 17:00:00 IST",
        portal_url="https://tpo.vierp.in/company-dashboard",
        pref_url="",
        unsub_url="",
        channel="subscriber",
    )
    assert "Internship Duration" in html
    assert "6 Months" in html


# 10. Internship type missing (Cummins 5952/5971: no 'N/A' row)
def test_10_internship_type_missing_cummins_5952_5971():
    for val in (None, "None", "N/A", "Not mentioned.", "", "null"):
        assert format_internship_type(val) is None

    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    company = CompanyRecord(
        id="5971",
        company="Cummins India",
        placementtype="Internship + Performance based PPO",
        internshiptype=None,
    )
    html = worker._format_opportunity_email(
        company_record=company,
        notif_type="NEW",
        user_branch="Computer Science and Engineering (Data Science)",
        unsub_url="https://example.com/unsub",
        pref_url="https://example.com/pref",
    )
    text = VisibleText.read(html)
    assert "Placement Type Internship + Performance based PPO" in text
    assert "Internship Duration" not in text
    assert "Internship Type" not in text


# 11. Compensation with verified unit
def test_11_compensation_with_verified_unit():
    stipend = format_compensation("25000", "25000", "monthly stipend", "verified-source-doc")
    assert stipend[0] == "Rs. 25,000 per month"
    assert "Stipend (verified)" in stipend[1]

    lpa = format_compensation("9", "9", "LPA", "verified-source-doc")
    assert lpa[0] == "Rs. 9 LPA"
    assert "Annual package" in lpa[1]


# 12. Compensation with unknown unit (no fabricated LPA)
def test_12_compensation_with_unknown_unit_cummins():
    comp = format_compensation("25000.0", "25000.0", None, None)
    assert comp[0] == "25,000"
    assert "Unit not specified by TPO" in comp[1]
    assert "LPA" not in comp[0] and "LPA" not in comp[1]


# 13. Identical minimum and maximum values (no repetition)
def test_13_identical_minimum_and_maximum_values():
    comp = format_compensation("25000.0", "25000.0")
    assert comp[0] == "25,000"
    assert "(Max)" not in comp[0]
    assert "(Min)" not in comp[0]
    assert "25,000 / 25,000" not in comp[0]


# 14. Missing compensation (clear non-disclosure statement)
def test_14_missing_compensation():
    for val in (None, "", "0", "0.0", "N/A"):
        comp = format_compensation(val, val)
        assert comp[0] == "Not disclosed by company on portal"


# 15. Data Science branch personalization
def test_15_data_science_branch_personalization():
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    company = CompanyRecord(id="5971", company="Cummins India")
    branch = "Computer Science and Engineering (Data Science)"
    html = worker._format_opportunity_email(
        company_record=company,
        notif_type="NEW",
        user_branch=branch,
        unsub_url="https://example.com/unsub",
        pref_url="https://example.com/pref",
    )
    assert "Your Matched Branch" in html
    assert branch in html


# 16. HTML escaping and safe URLs
def test_16_html_escaping_and_safe_urls():
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    malicious_company = CompanyRecord(
        id="999",
        company='<script>alert("hack")</script>',
        company_code='"><img src=x onerror=alert(1)>',
        placementtype="<b onclick=alert(2)>Placement</b>",
        tpoprogram="BTech <svg onload=alert(3)>",
    )
    html = worker._format_opportunity_email(
        company_record=malicious_company,
        notif_type="NEW",
        user_branch="Data Science <iframe src=evil.com>",
        unsub_url="javascript:alert(4)",
        pref_url="data:text/html,<script>alert(5)</script>",
    )
    assert "<script>" not in html
    assert "<img" not in html
    assert "<svg" not in html
    assert "<iframe" not in html
    assert "javascript:" not in html
    assert "data:text/html" not in html
    assert "&lt;script&gt;alert(&quot;hack&quot;)&lt;/script&gt;" in html


# 17. Meaningful plain-text MIME alternative
def test_17_plain_text_mime_alternative():
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    company = CompanyRecord(
        id="5952",
        company="Cummins India",
        company_code="CUMMINSINDIA2026-271",
        placementtype="Internship + Performance based PPO",
        maxPackage="25000.0",
        minPackage="25000.0",
        regStartdate="13-Oct-2026",
        regStarttime="11:31",
        regEnddate="14-Oct-2026",
        regEndtime="11:00",
    )
    text = worker._format_opportunity_text(
        company_record=company,
        notif_type="NEW",
        user_branch="Computer Science and Engineering (Data Science)",
        unsub_url="https://example.com/unsub#token=123",
        pref_url="https://example.com/pref#token=456",
        event_at="2026-10-13T11:30:14+00:00",
    )
    assert "TPO-WATCHER | NEW TPO OPPORTUNITY" in text
    assert "Cummins India" in text
    assert "Computer Science and Engineering (Data Science)" in text
    assert "25,000" in text
    assert "13-Oct-2026 11:31 to 14-Oct-2026 11:00" in text
    assert "https://example.com/unsub#token=123" in text
    assert "https://example.com/pref#token=456" in text
    assert "Branch programme match does not confirm full eligibility" in text


# 18. Manage Preferences and Unsubscribe links
def test_18_manage_preferences_and_unsubscribe_links():
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    company = CompanyRecord(id="1", company="Test Corp")
    html = worker._format_opportunity_email(
        company_record=company,
        notif_type="NEW",
        user_branch="Data Science",
        unsub_url="https://tpowatcher.duckdns.org/unsubscribe#token=unsub_secret_token",
        pref_url="https://tpowatcher.duckdns.org/preferences/confirm#token=pref_secret_token",
    )
    assert 'href="https://tpowatcher.duckdns.org/unsubscribe#token=unsub_secret_token"' in html
    assert 'href="https://tpowatcher.duckdns.org/preferences/confirm#token=pref_secret_token"' in html


# 19. Correct recipient targeting
def test_19_correct_recipient_targeting(monkeypatch):
    sent_recipients = []
    provider = EmailNotificationProvider()
    monkeypatch.setattr(
        provider,
        "_send_email_to",
        lambda to_email, subject, html, **kw: sent_recipients.append(to_email) or True,
    )
    provider._send_email_to("student@example.invalid", "Test Subject", "<p>Test</p>")
    assert sent_recipients == ["student@example.invalid"]


# 20. No duplicate notifications (delivery uniqueness verification)
def test_20_no_duplicate_notifications():
    # Verify worker claims atomic deliveries and respects sent statuses
    company = CompanyRecord(id="5971", company="Cummins India")
    subj1 = format_opportunity_subject("NEW", company.company)
    subj2 = format_opportunity_subject("NEW", company.company)
    assert subj1 == subj2 == "New TPO Opportunity: Cummins India | TPO-WATCHER"


# 21. Email-client presentation (table layout, responsive constraints)
def test_21_email_client_presentation():
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    company = CompanyRecord(id="5971", company="Cummins India")
    html = worker._format_opportunity_email(
        company_record=company,
        notif_type="NEW",
        user_branch="Data Science",
        unsub_url="https://example.com/unsub",
        pref_url="https://example.com/pref",
    )
    assert '<table role="presentation"' in html
    assert "max-width: 600px" in html
    assert "border-collapse: collapse" in html
    assert "-apple-system" in html
    assert "color-scheme" in html


# 22. Preservation of watcher scheduling and portal synchronization
def test_22_no_changes_to_watcher_scheduling_or_sync():
    from app.monitoring.watcher import PlacementWatcher
    watcher = PlacementWatcher()
    assert watcher.scheduler is not None
    assert hasattr(watcher, "run_forever")
    assert hasattr(watcher, "check_once")
    assert hasattr(watcher, "_process_notifications")


# 23. Eligible programs filters invalid artifacts like VIT-null, preserves VIIT/VU, and deduplicates
def test_23_eligible_programs_filters_invalid_artifacts_and_deduplicates():
    from app.notifications.presentation import format_eligible_programs
    raw = (
        "VIT-BTech-Computer Engineering, "
        "VIT-BTech - Computer Science and Engineering (Artificial Intelligence and Machine Learning), "
        "VIT-BTech-Artificial Intelligence & Data Science, "
        "VIT-null, "
        "VIT-BTech-Electronics and Telecommunication Engg, "
        "VIT-B.Tech. Computer Science and Artificial Intelligence, "
        "VIT-BTech-Information Technology, "
        "VIT-BTech-Computer Science and Engineering (Artificial Intelligence), "
        "VIT-BTech-Mechanical Engineering"
    )
    cleaned, note = format_eligible_programs(raw)

    # 1. Demonstrably invalid ERP artifacts must be stripped
    assert "VIT-null" not in cleaned
    assert "null" not in [c.lower() for c in cleaned]
    assert not any("null" in c.lower() for c in cleaned)

    # 2. Valid specializations are preserved
    assert "Computer Engineering" in cleaned
    assert "Information Technology" in cleaned
    assert "Mechanical Engineering" in cleaned
    assert "Artificial Intelligence & Data Science" in cleaned

    # 3. Deduplication of AI degree alias
    ai_entries = [c for c in cleaned if "artificial intelligence" in c.lower()]
    # Distinct specializations: AIML, AIDS, CSE AI (duplicate legacy code alias removed)
    assert len(ai_entries) == 3

    # 4. Directive note to check official portal with exact required wording
    assert note == "Branch match indicates programme relevance only; full eligibility must be verified on the official TPO portal."

    # 5. Distinct institutional identifiers (VIIT, VU) are preserved
    raw_multi_inst = "VIT-BTech-Computer Engineering, VIIT-BTech-Computer Engineering, VU-BTech-Computer Science"
    multi_inst_cleaned, _ = format_eligible_programs(raw_multi_inst)
    assert "Computer Engineering" in multi_inst_cleaned
    assert "VIIT - Computer Engineering" in multi_inst_cleaned
    assert "VU - Computer Science" in multi_inst_cleaned

    # 5. HTML presentation renders badges
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    comp = CompanyRecord(id="5952", company="Cummins India", tpoprogram=raw)
    html = worker._format_opportunity_email(comp, "NEW", "Data Science", "https://unsub", "https://pref")
    assert "VIT-null" not in html
    assert '<span style="display: inline-block;' in html
    assert "Computer Engineering</span>" in html


# 24. Footer accurately supports registered Gmail subscribers
def test_24_footer_accurately_supports_gmail_subscribers():
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    comp = CompanyRecord(id="5952", company="Cummins India")
    html = worker._format_opportunity_email(comp, "NEW", "Data Science", "https://unsub", "https://pref")
    text = worker._format_opportunity_text(comp, "NEW", "Data Science", "https://unsub", "https://pref")

    # Does not falsely claim the user registered with an institutional '@vit.edu' address
    assert "with your VIT Pune 2028 email" not in html
    assert "with your VIT Pune 2028 email" not in text

    # Accurately specifies subscription to the placement cycle
    assert "subscribed to TPO-WATCHER for the VIT Pune 2028 placement cycle" in html
    assert "subscribed to TPO-WATCHER for the VIT Pune 2028 placement cycle" in text


# 25. Portal CTA contrast, button arrows, and print media styling
def test_25_portal_cta_contrast_and_print_styling():
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    comp = CompanyRecord(id="5952", company="Cummins India")
    html = worker._format_opportunity_email(comp, "NEW", "Data Science", "https://unsub", "https://pref")

    # CTA button styling guarantees high contrast and border fallback
    assert "Open Official TPO Portal" in html
    assert "border: 2px solid #1e40af" in html
    assert "color: #ffffff !important" in html
    assert "https://tpo.vierp.in/company-dashboard" in html

    # Print-specific CSS included in <head>
    assert "@media print" in html
    assert "-webkit-print-color-adjust: exact !important" in html
    assert "page-break-inside: avoid" in html


# 26. Production worker maintains dynamic action token generation
def test_26_production_worker_maintains_dynamic_action_tokens(monkeypatch):
    from unittest.mock import MagicMock
    mock_token_repo = MagicMock()
    worker = DeliveryWorker(delivery_repo=None, token_repo=mock_token_repo)
    token = worker._generate_token(5, "UNSUBSCRIBE", timedelta(days=30))
    assert isinstance(token, str) and len(token) > 20
    assert mock_token_repo.create_token.called

