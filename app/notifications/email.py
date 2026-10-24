import smtplib
import logging
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from app.config import settings
from app.tpo.models import CompanyRecord
from app.notifications.base import NotificationProvider
from app.notifications.transport_protocol import MailOutcome

from app.notifications.utils import escape_html, render_safe_link
from app.notifications.presentation import (
    format_opportunity_subject,
    format_compensation,
    format_registration_window,
    format_internship_type,
    render_opportunity_html,
    render_opportunity_text,
)

logger = logging.getLogger(__name__)

class EmailNotificationProvider(NotificationProvider):
    def __init__(self,*,transport=None):
        if transport is not None:
            from app.notifications.transport_arbiter import AuthorityGateway
            if not isinstance(transport,AuthorityGateway):raise ValueError("explicit gated fictional adapter required")
        self.transport=transport

    def _send_email(self, subject: str, html_content: str, plain_content: Optional[str] = None) -> bool:
        try:
            return self._send_email_to(settings.EMAIL_TO, subject, html_content, plain_content=plain_content)
        except TypeError:
            return self._send_email_to(settings.EMAIL_TO, subject, html_content)

    def _send_email_to(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        extra_headers: Optional[dict] = None,
        *,
        plain_content: Optional[str] = None,
        expires_ms=None,
    ) -> bool | MailOutcome:
        from app.monitoring.runtime_supervisor import current_runtime
        from app.database.event_repository import PolicyDenied
        if self.transport is None and current_runtime() is not None:
            raise PolicyDenied('guarded runtime excludes direct SMTP')
        if self.transport is not None:
            if extra_headers:raise ValueError("subscriber messages require a durable authorized delivery")
            if expires_ms is None:raise ValueError("finite ACCOUNT request expiry required")
            return self.transport.send_account(to_email,subject,html_content,expires_ms)
        if not settings.SMTP_HOST or not settings.SMTP_USERNAME or not to_email:
            logger.warning("Email configuration or recipient missing. Skipping email notification.")
            return False

        clean_subject = " ".join(str(subject).split())
        msg = EmailMessage()
        msg['Subject'] = clean_subject
        msg['From'] = settings.EMAIL_FROM or settings.SMTP_USERNAME
        msg['To'] = to_email

        if plain_content:
            msg.set_content(plain_content)
        else:
            msg.set_content("Please enable HTML to view this email.")
        msg.add_alternative(html_content, subtype='html')

        if extra_headers:
            for k, v in extra_headers.items():
                msg[k] = v

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.send_message(msg)
            # F08: shared logs carry outcome only — never recipient, subject
            # or arbitrary provider/transport error text.
            logger.info("Email dispatch accepted by provider.")
            return True
        except Exception as exc:
            logger.error("Email dispatch failed (%s).", type(exc).__name__)
            return False

    def send_new_company_notification(self, company: CompanyRecord, target_match: bool, event_at=None) -> bool | MailOutcome:
        if self.transport is not None:return self.transport.send_admin(company.id)
        subject = format_opportunity_subject("NEW", company.company)

        programs = company.tpoprogram or company.programnew or "Not Specified"

        IST = timezone(timedelta(hours=5, minutes=30))
        # F11: use event-time (first_seen_at / explicit event_at) not render-time.
        # This ensures the email shows when the opportunity was first detected,
        # not when the email was composed (which may be minutes later).
        def _parse_event_at(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            try:
                # ISO string from DB (e.g. "2026-10-13T11:30:00+00:00")
                return datetime.fromisoformat(str(val))
            except Exception:
                return None

        detected_dt = _parse_event_at(event_at) or datetime.now(IST)
        if detected_dt.tzinfo is None:
            detected_dt = detected_dt.replace(tzinfo=timezone.utc)
        detected_time = detected_dt.astimezone(IST).strftime("%d-%b-%Y %H:%M:%S IST")

        comp_main, comp_note = format_compensation(
            company.maxPackage,
            company.minPackage,
            getattr(company, "compensation_unit", None),
            getattr(company, "compensation_unit_evidence", None),
        )
        reg_info = format_registration_window(company)
        intern_type = format_internship_type(company.internshiptype)

        html = render_opportunity_html(
            company_name=company.company,
            company_code=company.company_code or "N/A",
            placement_type=company.placementtype or "N/A",
            internship_type=intern_type,
            compensation_main=comp_main,
            compensation_note=comp_note,
            reg_window_text=reg_info["display_text"],
            reg_badge=reg_info["badge"],
            reg_note=reg_info["note"],
            academic_year=company.academicyear or "N/A",
            organization=company.organization or "N/A",
            user_branch="",
            programs=programs,
            notif_type="NEW",
            now_ist_str=detected_time,
            portal_url="https://tpo.vierp.in/company-dashboard",
            pref_url="",
            unsub_url="",
            channel="admin",
        )
        text = render_opportunity_text(
            company_name=company.company,
            company_code=company.company_code or "N/A",
            placement_type=company.placementtype or "N/A",
            internship_type=intern_type,
            compensation_main=comp_main,
            compensation_note=comp_note,
            reg_window_text=reg_info["display_text"],
            reg_badge=reg_info["badge"],
            academic_year=company.academicyear or "N/A",
            organization=company.organization or "N/A",
            user_branch="",
            programs=programs,
            notif_type="NEW",
            now_ist_str=detected_time,
            portal_url="https://tpo.vierp.in/company-dashboard",
            pref_url="",
            unsub_url="",
            channel="admin",
        )

        try:
            return self._send_email(subject, html, plain_content=text)
        except TypeError:
            return self._send_email(subject, html)

    def send_update_notification(self, company: CompanyRecord, changes: List[str]) -> bool | MailOutcome:
        if self.transport is not None:return self.transport.send_admin(company.id)
        subject = format_opportunity_subject("UPDATE", company.company)

        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
        programs = company.tpoprogram or company.programnew or "Not Specified"

        comp_main, comp_note = format_compensation(
            company.maxPackage,
            company.minPackage,
            getattr(company, "compensation_unit", None),
            getattr(company, "compensation_unit_evidence", None),
        )
        reg_info = format_registration_window(company)
        intern_type = format_internship_type(company.internshiptype)

        html = render_opportunity_html(
            company_name=company.company,
            company_code=company.company_code or "N/A",
            placement_type=company.placementtype or "N/A",
            internship_type=intern_type,
            compensation_main=comp_main,
            compensation_note=comp_note,
            reg_window_text=reg_info["display_text"],
            reg_badge=reg_info["badge"],
            reg_note=reg_info["note"],
            academic_year=company.academicyear or "N/A",
            organization=company.organization or "N/A",
            user_branch="",
            programs=programs,
            notif_type="UPDATE",
            now_ist_str=now_ist,
            portal_url="https://tpo.vierp.in/company-dashboard",
            pref_url="",
            unsub_url="",
            changes=changes,
            is_active_raw=company.isactive,
            channel="admin",
        )
        text = render_opportunity_text(
            company_name=company.company,
            company_code=company.company_code or "N/A",
            placement_type=company.placementtype or "N/A",
            internship_type=intern_type,
            compensation_main=comp_main,
            compensation_note=comp_note,
            reg_window_text=reg_info["display_text"],
            reg_badge=reg_info["badge"],
            academic_year=company.academicyear or "N/A",
            organization=company.organization or "N/A",
            user_branch="",
            programs=programs,
            notif_type="UPDATE",
            now_ist_str=now_ist,
            portal_url="https://tpo.vierp.in/company-dashboard",
            pref_url="",
            unsub_url="",
            changes=changes,
            channel="admin",
        )

        try:
            return self._send_email(subject, html, plain_content=text)
        except TypeError:
            return self._send_email(subject, html)
