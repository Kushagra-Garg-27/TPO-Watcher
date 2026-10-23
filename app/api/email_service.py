import logging
from enum import Enum
from app.config import settings
from app.notifications.email import EmailNotificationProvider
from app.notifications.utils import escape_html, sanitize_url
from app.notifications.transport_protocol import MailOutcome

logger = logging.getLogger(__name__)

class AccountMailResult(str,Enum):
    REQUEST_RECEIVED="REQUEST_RECEIVED"
    BUSY="BUSY"
    EXPIRED="EXPIRED"
    NOT_INVOKED="NOT_INVOKED"
    NOT_ACCEPTED="NOT_ACCEPTED"
    UNKNOWN="UNKNOWN"
    ACCEPTED="ACCEPTED"

class EmailService:
    def __init__(self, notifier: EmailNotificationProvider = None, *, transport=None, account_transport_required=False):
        if notifier is not None and transport is not None:raise ValueError("choose one explicit notifier binding")
        if type(account_transport_required) is not bool:raise ValueError("explicit account transport requirement")
        self.notifier = notifier if notifier is not None else EmailNotificationProvider(transport=transport)
        self.last_account_result=None
        self.account_transport_required=account_transport_required

    def _dispatch(self,to_email,subject,html,expires_ms,lifetime_ms):
        adapter=self._adapter()
        if adapter is None and self.account_transport_required:
            self.last_account_result=AccountMailResult.BUSY
            return MailOutcome("BUSY")
        if adapter is None:return self.notifier._send_email_to(to_email,subject,html)
        self.last_account_result=AccountMailResult.REQUEST_RECEIVED
        now=adapter.repository.now()
        expiry=now+lifetime_ms if expires_ms is None else expires_ms
        if type(expiry) is not int or expiry>now+lifetime_ms:
            self.last_account_result=AccountMailResult.NOT_INVOKED
            raise ValueError("bounded token lifetime required")
        try:result=self.notifier._send_email_to(to_email,subject,html,expires_ms=expiry)
        except Exception:
            self.last_account_result=AccountMailResult.UNKNOWN
            raise
        self.last_account_result=AccountMailResult(result.kind)
        return result

    def _adapter(self):
        from app.notifications.transport_arbiter import AuthorityGateway
        adapter=getattr(self.notifier,"transport",None)
        return adapter if isinstance(adapter,AuthorityGateway) else None

    def send_verification_email(self, to_email: str, raw_token: str, *, expires_ms=None) -> bool | MailOutcome:
        base=getattr(self._adapter(),"action_base",settings.BASE_URL)
        verify_url = f"{base}/verify#token={raw_token}"
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
        return self._dispatch(to_email,subject,html,expires_ms,86400000)

    def send_preference_link_email(self, to_email: str, raw_token: str, *, expires_ms=None) -> bool | MailOutcome:
        base=getattr(self._adapter(),"action_base",settings.BASE_URL)
        access_url = f"{base}/preferences/confirm#token={raw_token}"
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
        return self._dispatch(to_email,subject,html,expires_ms,900000)


def get_email_service() -> EmailService:
    from app.database.repository import DB_PATH
    from app.database.notification_compatibility import account_transport_required
    required=account_transport_required(DB_PATH)
    from app.monitoring.runtime_supervisor import current_runtime
    from app.database.key_provider import KeyUnavailable
    runtime=current_runtime(DB_PATH)
    if runtime is not None:
        try:adapter=runtime.account_gateway()
        except KeyUnavailable:adapter=None
        return EmailService(transport=adapter,account_transport_required=True)
    # Production/default authority wiring is not authorized by R2b-A. An
    # existing fictional runtime must therefore report unavailable, never SMTP.
    return EmailService(account_transport_required=required)
