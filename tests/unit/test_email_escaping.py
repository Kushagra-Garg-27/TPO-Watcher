import pytest
from app.tpo.models import CompanyRecord
from app.notifications.email import EmailNotificationProvider
from app.notifications.utils import escape_html, sanitize_url, render_safe_link
from app.subscribers.worker import DeliveryWorker
from app.api.email_service import EmailService
from app.config import settings


MALICIOUS_PAYLOADS = [
    "<script>alert(1)</script>",
    "<b>Fake Bold</b>",
    '& < > " \'',
    '"><img src=x onerror=alert(1)>',
    '<svg/onload=alert("xss")>',
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "vbscript:msgbox(1)",
]


def test_escape_html_replaces_all_special_chars():
    assert escape_html('& < > " \'') == "&amp; &lt; &gt; &quot; &#x27;"
    assert escape_html("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert escape_html('"><img src=x onerror=alert(1)>') == "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"
    assert escape_html(None) == ""


def test_sanitize_url_accepts_only_http_and_https():
    # Valid schemes accepted
    assert sanitize_url("https://tpo.vierp.in/company-dashboard") == "https://tpo.vierp.in/company-dashboard"
    assert sanitize_url("http://example.com/test") == "http://example.com/test"

    # Unsafe schemes rejected
    assert sanitize_url("javascript:alert(1)") is None
    assert sanitize_url("data:text/html,<script>alert(1)</script>") is None
    assert sanitize_url("file:///etc/passwd") is None
    assert sanitize_url("vbscript:msgbox(1)") is None
    assert sanitize_url("") is None
    assert sanitize_url(None) is None


def test_render_safe_link_neutralizes_unsafe_schemes():
    # Safe link renders <a>
    rendered_safe = render_safe_link("https://example.com", "Click Here")
    assert '<a href="https://example.com">Click Here</a>' in rendered_safe

    # Unsafe link does NOT render clickable <a>, only escaped text
    rendered_unsafe = render_safe_link("javascript:alert(1)", "<Evil Link>")
    assert "<a href=" not in rendered_unsafe
    assert "javascript:" not in rendered_unsafe
    assert "&lt;Evil Link&gt;" in rendered_unsafe


def test_internal_new_company_email_escapes_all_upstream_fields(monkeypatch):
    captured_emails = []

    provider = EmailNotificationProvider()
    monkeypatch.setattr(
        provider,
        "_send_email_to",
        lambda to_email, subject, html: captured_emails.append((to_email, subject, html)) or True
    )

    company = CompanyRecord(
        id="9999",
        company='<script>alert("company")</script>',
        company_code='"><img src=x onerror=alert(1)>',
        placementtype="<b>Full Time</b>",
        internshiptype="<i>Summer</i>",
        maxPackage='<pkg>15</pkg>',
        minPackage='<pkg>10</pkg>',
        regStartdate='<start>01-Jan</start>',
        regStarttime="<time>10:00</time>",
        regEnddate="<end>05-Jan</end>",
        regEndtime="<time>17:00</time>",
        academicyear='<year>2027-28</year>',
        tpoprogram='<prog>BTech CS</prog>',
        organization='<org>VIT TPO</org>',
    )

    success = provider.send_new_company_notification(company, target_match=True)
    assert success is True
    assert len(captured_emails) == 1

    to_email, subject, html = captured_emails[0]

    # Verify no raw dangerous HTML elements survive in email body
    assert "<script>" not in html
    assert "<img" not in html
    assert "<svg" not in html
    assert "<b>Full Time</b>" not in html
    assert "<i>Summer</i>" not in html
    assert "<pkg>" not in html
    assert "<start>" not in html
    assert "<year>" not in html
    assert "<prog>" not in html
    assert "<org>" not in html

    # Verify entities are present
    assert "&lt;script&gt;alert(&quot;company&quot;)&lt;/script&gt;" in html
    assert "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;b&gt;Full Time&lt;/b&gt;" in html
    assert "&lt;pkg&gt;15&lt;/pkg&gt;" in html


def test_internal_update_notification_escapes_changes(monkeypatch):
    captured_emails = []
    provider = EmailNotificationProvider()
    monkeypatch.setattr(
        provider,
        "_send_email_to",
        lambda to_email, subject, html: captured_emails.append((to_email, subject, html)) or True
    )

    company = CompanyRecord(id="8888", company="Acme Corp <script>")
    changes = [
        "<script>alert('change')</script>",
        'Package changed from "8" to "12" & bonus',
        '"><img src=x onerror=alert(2)>',
    ]

    success = provider.send_update_notification(company, changes)
    assert success is True
    assert len(captured_emails) == 1

    _, _, html = captured_emails[0]
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;alert(&#x27;change&#x27;)&lt;/script&gt;" in html
    assert "&quot;8&quot; to &quot;12&quot; &amp; bonus" in html
    assert "&quot;&gt;&lt;img src=x onerror=alert(2)&gt;" in html


def test_subscriber_worker_format_opportunity_email_escaping():
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)

    company = CompanyRecord(
        id="7777",
        company='Malicious Corp <script>alert("company")</script>',
        company_code='CODE"><svg/onload=alert(1)>',
        placementtype="<p>Placement</p>",
        internshiptype="<p>Internship</p>",
        maxPackage="25<script>",
        minPackage="15<script>",
        regStartdate="<b>Start</b>",
        regEnddate="<b>End</b>",
        academicyear="<year>2028</year>",
        tpoprogram="BTech <alert>",
    )

    # Test with malicious inputs in branch and action URLs
    unsub_url = f"{settings.BASE_URL}/api/v1/unsubscribe?token=safe_token"
    pref_url = f"{settings.BASE_URL}/api/v1/preferences/request?token=safe_token"
    malicious_branch = "CS & IT <script>alert(1)</script>"

    html = worker._format_opportunity_email(
        company_record=company,
        notif_type="NEW",
        user_branch=malicious_branch,
        unsub_url=unsub_url,
        pref_url=pref_url,
    )

    # Verify no raw injection
    assert "<script>" not in html
    assert "<svg" not in html
    assert "<p>" not in html
    assert "<b>Start</b>" not in html
    assert "<year>" not in html
    assert "<alert>" not in html

    # Verify escaped values
    assert "&lt;script&gt;alert(&quot;company&quot;)&lt;/script&gt;" in html
    assert "CODE&quot;&gt;&lt;svg/onload=alert(1)&gt;" in html
    assert "CS &amp; IT &lt;script&gt;alert(1)&lt;/script&gt;" in html

    # Verify URLs build from BASE_URL
    assert unsub_url in html
    assert pref_url in html


def test_subscriber_worker_neutralizes_malicious_action_urls():
    worker = DeliveryWorker(delivery_repo=None, token_repo=None)
    company = CompanyRecord(id="123", company="Test Corp")

    # If an attacker attempts to supply a javascript: URI as an action link
    html = worker._format_opportunity_email(
        company_record=company,
        notif_type="NEW",
        user_branch="CS",
        unsub_url="javascript:alert('unsub')",
        pref_url="data:text/html,<script>alert('pref')</script>",
    )

    assert "javascript:" not in html
    assert "data:text/html" not in html


def test_email_service_escapes_user_input(monkeypatch):
    captured = []
    service = EmailService()
    monkeypatch.setattr(
        service.notifier,
        "_send_email_to",
        lambda to_email, subject, html: captured.append((to_email, subject, html)) or True
    )

    malicious_email = 'student<script>alert(1)</script>@vit.edu'
    service.send_verification_email(malicious_email, "dummy_token_123")
    service.send_preference_link_email(malicious_email, "dummy_token_456")

    assert len(captured) == 2

    # Verification email escapes to_email
    _, _, verify_html = captured[0]
    assert "<script>alert(1)</script>" not in verify_html
    assert "student&lt;script&gt;alert(1)&lt;/script&gt;@vit.edu" in verify_html
    assert f"{settings.BASE_URL}/api/v1/auth/verify?token=dummy_token_123" in verify_html

    # Preference email does not leak raw script
    _, _, pref_html = captured[1]
    assert "<script>alert(1)</script>" not in pref_html
    assert f"{settings.BASE_URL}/api/v1/preferences/request?token=dummy_token_456" in pref_html
