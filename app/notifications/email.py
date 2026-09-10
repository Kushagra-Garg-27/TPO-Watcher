import smtplib
import logging
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from typing import List
from app.config import settings
from app.tpo.models import CompanyRecord
from app.notifications.base import NotificationProvider

logger = logging.getLogger(__name__)

class EmailNotificationProvider(NotificationProvider):
    def _send_email(self, subject: str, html_content: str) -> bool:
        if not settings.SMTP_HOST or not settings.SMTP_USERNAME:
            logger.warning("Email configuration missing. Skipping email notification.")
            return False
            
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = settings.EMAIL_FROM
        msg['To'] = settings.EMAIL_TO
        
        msg.set_content("Please enable HTML to view this email.")
        msg.add_alternative(html_content, subtype='html')

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.send_message(msg)
            logger.info(f"Email sent successfully: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False

    def send_new_company_notification(self, company: CompanyRecord, target_match: bool) -> bool:
        subject = f"🚨 NEW VIT TPO COMPANY: {company.company}"
        
        match_str = "YES" if target_match else "NO"
        programs = company.tpoprogram or company.programnew or "Not Specified"
        
        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST)
        detected_time = now_ist.strftime("%d-%b-%Y %H:%M:%S IST")
        
        html = f"""
        <html>
        <body style="font-family: sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #d32f2f;">🚨 NEW VIT TPO OPPORTUNITY</h2>
            
            <p><strong>Company:</strong> {company.company} ({company.company_code})</p>
            <p><strong>Placement Type:</strong> {company.placementtype or 'N/A'}</p>
            <p><strong>Internship Type:</strong> {company.internshiptype or 'N/A'}</p>
            
            <p><strong>Package:</strong> {company.maxPackage or 'N/A'} (Max) / {company.minPackage or 'N/A'} (Min)</p>
            
            <p><strong>Registration:</strong><br/>
               {company.regStartdate or 'N/A'} {company.regStarttime or ''}<br/>
               to<br/>
               {company.regEnddate or 'N/A'} {company.regEndtime or ''}
            </p>
            
            <p><strong>Academic Year:</strong> {company.academicyear or 'N/A'}</p>
            <p><strong>Eligibility:</strong> {programs}</p>
            <p><strong>Matches your target programs:</strong> {match_str}</p>
            <p><strong>Organization:</strong> {company.organization or 'N/A'}</p>
            
            <p><strong>Detected:</strong> {detected_time}</p>
            
            <p><a href="https://tpo.vierp.in/company-dashboard" style="display: inline-block; padding: 10px 20px; background-color: #1976d2; color: white; text-decoration: none; border-radius: 5px;">Open TPO Portal</a></p>
        </body>
        </html>
        """
        
        return self._send_email(subject, html)

    def send_update_notification(self, company: CompanyRecord, changes: List[str]) -> bool:
        subject = f"⚠️ VIT TPO OPPORTUNITY UPDATED: {company.company}"
        
        changes_html = "<ul>"
        for change in changes:
            changes_html += f"<li>{change}</li>"
        changes_html += "</ul>"
        
        html = f"""
        <html>
        <body style="font-family: sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #f57c00;">⚠️ VIT TPO OPPORTUNITY UPDATED</h2>
            
            <p><strong>Company:</strong> {company.company}</p>
            
            <h3>Changes Detected:</h3>
            {changes_html}
            
            <p><a href="https://tpo.vierp.in/company-dashboard" style="display: inline-block; padding: 10px 20px; background-color: #1976d2; color: white; text-decoration: none; border-radius: 5px;">Open TPO Portal</a></p>
        </body>
        </html>
        """
        
        return self._send_email(subject, html)
