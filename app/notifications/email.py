import smtplib
import logging
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from typing import List
from app.config import settings
from app.tpo.models import CompanyRecord
from app.notifications.base import NotificationProvider

from app.notifications.utils import escape_html, render_safe_link

logger = logging.getLogger(__name__)

class EmailNotificationProvider(NotificationProvider):
    def _send_email(self, subject: str, html_content: str) -> bool:
        return self._send_email_to(settings.EMAIL_TO, subject, html_content)

    def _send_email_to(self, to_email: str, subject: str, html_content: str) -> bool:
        if not settings.SMTP_HOST or not settings.SMTP_USERNAME or not to_email:
            logger.warning("Email configuration or recipient missing. Skipping email notification.")
            return False
            
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = settings.EMAIL_FROM or settings.SMTP_USERNAME
        msg['To'] = to_email
        
        msg.set_content("Please enable HTML to view this email.")
        msg.add_alternative(html_content, subtype='html')

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.send_message(msg)
            logger.info(f"Email sent successfully to {to_email}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False

    def send_new_company_notification(self, company: CompanyRecord, target_match: bool) -> bool:
        subject = f"🚨 NEW VIT TPO COMPANY: {company.company}"
        
        match_str = "YES" if target_match else "NO"
        programs = company.tpoprogram or company.programnew or "Not Specified"
        
        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST)
        detected_time = now_ist.strftime("%d-%b-%Y %H:%M:%S IST")
        
        company_name = escape_html(company.company)
        company_code = escape_html(company.company_code or "N/A")
        placement_type = escape_html(company.placementtype or "N/A")
        internship_type = escape_html(company.internshiptype or "N/A")
        max_pkg = escape_html(company.maxPackage or "N/A")
        min_pkg = escape_html(company.minPackage or "N/A")
        reg_start = escape_html(f"{company.regStartdate or 'N/A'} {company.regStarttime or ''}".strip())
        reg_end = escape_html(f"{company.regEnddate or 'N/A'} {company.regEndtime or ''}".strip())
        academic_year = escape_html(company.academicyear or "N/A")
        eligibility = escape_html(programs)
        match_escaped = escape_html(match_str)
        organization = escape_html(company.organization or "N/A")
        detected_time_esc = escape_html(detected_time)
        portal_button = render_safe_link(
            "https://tpo.vierp.in/company-dashboard",
            "Open TPO Portal",
            style="display: inline-block; padding: 10px 20px; background-color: #1976d2; color: white; text-decoration: none; border-radius: 5px;"
        )

        html = f"""
        <html>
        <body style="font-family: sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #d32f2f;">🚨 NEW VIT TPO OPPORTUNITY</h2>
            
            <p><strong>Company:</strong> {company_name} ({company_code})</p>
            <p><strong>Placement Type:</strong> {placement_type}</p>
            <p><strong>Internship Type:</strong> {internship_type}</p>
            
            <p><strong>Package:</strong> {max_pkg} (Max) / {min_pkg} (Min)</p>
            
            <p><strong>Registration:</strong><br/>
               {reg_start}<br/>
               to<br/>
               {reg_end}
            </p>
            
            <p><strong>Academic Year:</strong> {academic_year}</p>
            <p><strong>Eligibility:</strong> {eligibility}</p>
            <p><strong>Matches your target programs:</strong> {match_escaped}</p>
            <p><strong>Organization:</strong> {organization}</p>
            
            <p><strong>Detected:</strong> {detected_time_esc}</p>
            
            <p>{portal_button}</p>
        </body>
        </html>
        """
        
        return self._send_email(subject, html)

    def send_update_notification(self, company: CompanyRecord, changes: List[str]) -> bool:
        subject = f"⚠️ VIT TPO OPPORTUNITY UPDATED: {company.company}"
        
        company_name = escape_html(company.company)
        changes_html = "<ul>"
        for change in changes:
            changes_html += f"<li>{escape_html(change)}</li>"
        changes_html += "</ul>"
        
        portal_button = render_safe_link(
            "https://tpo.vierp.in/company-dashboard",
            "Open TPO Portal",
            style="display: inline-block; padding: 10px 20px; background-color: #1976d2; color: white; text-decoration: none; border-radius: 5px;"
        )

        html = f"""
        <html>
        <body style="font-family: sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #f57c00;">⚠️ VIT TPO OPPORTUNITY UPDATED</h2>
            
            <p><strong>Company:</strong> {company_name}</p>
            
            <h3>Changes Detected:</h3>
            {changes_html}
            
            <p>{portal_button}</p>
        </body>
        </html>
        """
        
        return self._send_email(subject, html)
