import logging
from app.config import settings
from app.notifications.email import EmailNotificationProvider
from app.notifications.utils import escape_html, sanitize_url

logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self, notifier: EmailNotificationProvider = None):
        self.notifier = notifier or EmailNotificationProvider()

    def send_verification_email(self, to_email: str, raw_token: str) -> bool:
        verify_url = f"{settings.BASE_URL}/api/v1/auth/verify?token={raw_token}"
        safe_verify_url = escape_html(verify_url)
        safe_email = escape_html(to_email)
        subject = "Verify your email for VIT TPO Watcher (Batch 2028)"
        
        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #222; background-color: #f7f9fa; padding: 20px;">
    <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e1e4e8; padding: 28px;">
        <h2 style="color: #0366d6; margin-top: 0;">Welcome to VIT TPO Watcher</h2>
        <p>You recently registered with <strong>{safe_email}</strong> to receive automatic placement and internship alerts for the <strong>VIT Pune Class of 2028</strong>.</p>
        <p>Please click the button below to verify your email address. This verification link is valid for <strong>24 hours</strong> and can only be used once.</p>
        
        <div style="text-align: center; margin: 30px 0;">
            <a href="{safe_verify_url}" style="background-color: #2ea44f; color: #ffffff; text-decoration: none; padding: 12px 28px; border-radius: 6px; font-weight: bold; font-size: 15px; display: inline-block;">
                Verify My Email
            </a>
        </div>

        <p style="font-size: 13px; color: #6a737d;">If the button does not work, copy and paste this link into your browser:<br/>
        <a href="{safe_verify_url}" style="color: #0366d6; word-break: break-all;">{safe_verify_url}</a></p>

        <hr style="border: 0; border-top: 1px solid #eaecef; margin: 24px 0;"/>
        <p style="font-size: 12px; color: #959da5; margin: 0;">If you did not request this registration, please ignore this email.</p>
    </div>
</body>
</html>"""
        return self.notifier._send_email_to(to_email, subject, html)

    def send_preference_link_email(self, to_email: str, raw_token: str) -> bool:
        access_url = f"{settings.BASE_URL}/api/v1/preferences/request?token={raw_token}"
        safe_access_url = escape_html(access_url)
        safe_email = escape_html(to_email)
        subject = "Your Preference Management Link - VIT TPO Watcher"

        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #222; background-color: #f7f9fa; padding: 20px;">
    <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e1e4e8; padding: 28px;">
        <h2 style="color: #0366d6; margin-top: 0;">Manage Your Preferences</h2>
        <p>You requested a secure link to manage your TPO Watcher notification preferences.</p>
        <p>This single-use access link is valid for <strong>15 minutes</strong>:</p>

        <div style="text-align: center; margin: 30px 0;">
            <a href="{safe_access_url}" style="background-color: #0366d6; color: #ffffff; text-decoration: none; padding: 12px 28px; border-radius: 6px; font-weight: bold; font-size: 15px; display: inline-block;">
                Manage Notification Preferences
            </a>
        </div>

        <p style="font-size: 13px; color: #6a737d;">Or copy and paste this link:<br/>
        <a href="{safe_access_url}" style="color: #0366d6; word-break: break-all;">{safe_access_url}</a></p>

        <hr style="border: 0; border-top: 1px solid #eaecef; margin: 24px 0;"/>
        <p style="font-size: 12px; color: #959da5; margin: 0;">If you did not request this link, you can safely ignore this email.</p>
    </div>
</body>
</html>"""
        return self.notifier._send_email_to(to_email, subject, html)


def get_email_service() -> EmailService:
    return EmailService()
