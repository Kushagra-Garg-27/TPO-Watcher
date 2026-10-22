import asyncio
import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from app.config import settings
from app.database.interfaces import DeliveryRepositoryProtocol, TokenRepositoryProtocol
from app.tpo.models import CompanyRecord
from app.notifications.email import EmailNotificationProvider
from app.notifications.utils import escape_html, sanitize_url, render_safe_link

logger = logging.getLogger(__name__)

class DeliveryWorker:
    def __init__(
        self,
        delivery_repo: DeliveryRepositoryProtocol,
        token_repo: TokenRepositoryProtocol,
        email_notifier: Optional[EmailNotificationProvider] = None,
        batch_size: int = 25,
        lease_seconds: int = 300,
        send_interval_seconds: float = 1.5,
        *, notification_pipeline=None
    ):
        self.delivery_repo = delivery_repo
        self.token_repo = token_repo
        self.notification_pipeline = notification_pipeline
        self.notifier = None if notification_pipeline is not None else email_notifier or EmailNotificationProvider()
        from app.notifications.transport_arbiter import AuthorityGateway
        if notification_pipeline is None and isinstance(getattr(self.notifier,"transport",None),AuthorityGateway):
            raise ValueError("gated fictional subscriber transport requires explicit v2 pipeline")
        self._v2_channel = "ADMIN"
        self.last_v2_attempts = 0
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.send_interval_seconds = send_interval_seconds
        self._is_running = False

    def _generate_token(self, user_id: int, token_type: str, expiry_delta: timedelta) -> str:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = datetime.now(timezone.utc) + expiry_delta
        self.token_repo.create_token(
            user_id=user_id,
            token_hash=token_hash,
            token_type=token_type,
            expires_at=expires_at
        )
        return raw_token

    def _format_opportunity_email(
        self, 
        company_record: CompanyRecord, 
        notif_type: str, 
        user_branch: str,
        unsub_url: str,
        pref_url: str
    ) -> str:
        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")

        programs = company_record.tpoprogram or company_record.programnew or "Not Specified"
        header_color = "#d32f2f" if notif_type == "NEW" else "#f57c00"
        title = "🚨 NEW VIT TPO OPPORTUNITY" if notif_type == "NEW" else "⚠️ VIT TPO OPPORTUNITY UPDATED"

        company_name = escape_html(company_record.company)
        company_code = escape_html(company_record.company_code or "N/A")
        placement_type = escape_html(company_record.placementtype or "N/A")
        internship_type = escape_html(company_record.internshiptype or "N/A")
        max_pkg = escape_html(company_record.maxPackage or "N/A")
        min_pkg = escape_html(company_record.minPackage or "N/A")
        reg_start = escape_html(f"{company_record.regStartdate or 'N/A'} {company_record.regStarttime or ''}".strip())
        reg_end = escape_html(f"{company_record.regEnddate or 'N/A'} {company_record.regEndtime or ''}".strip())
        academic_year = escape_html(company_record.academicyear or "N/A")
        user_branch_esc = escape_html(user_branch)
        programs_esc = escape_html(programs)
        now_ist_esc = escape_html(now_ist)

        safe_pref_url = sanitize_url(pref_url) or "#"
        safe_unsub_url = sanitize_url(unsub_url) or "#"
        portal_button = render_safe_link(
            "https://tpo.vierp.in/company-dashboard",
            "Open Official TPO Portal",
            style="background-color: #0366d6; color: #ffffff; text-decoration: none; padding: 12px 28px; border-radius: 6px; font-weight: bold; font-size: 15px; display: inline-block;"
        )

        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #222; background-color: #f7f9fa; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e1e4e8; box-shadow: 0 2px 8px rgba(0,0,0,0.05);">
        <div style="background-color: {header_color}; color: #ffffff; padding: 18px 24px;">
            <h2 style="margin: 0; font-size: 20px;">{title}</h2>
            <p style="margin: 4px 0 0 0; font-size: 13px; opacity: 0.9;">Exclusive Alert for VIT Pune Class of 2028</p>
        </div>
        <div style="padding: 24px;">
            <p style="font-size: 16px; margin-top: 0;"><strong>Company:</strong> <span style="font-size: 18px; color: #111;">{company_name}</span> ({company_code})</p>
            
            <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
                <tr style="border-bottom: 1px solid #eee;">
                    <td style="padding: 8px 0; color: #666; font-size: 14px;"><strong>Placement Type</strong></td>
                    <td style="padding: 8px 0; font-size: 14px; text-align: right;">{placement_type}</td>
                </tr>
                <tr style="border-bottom: 1px solid #eee;">
                    <td style="padding: 8px 0; color: #666; font-size: 14px;"><strong>Internship Type</strong></td>
                    <td style="padding: 8px 0; font-size: 14px; text-align: right;">{internship_type}</td>
                </tr>
                <tr style="border-bottom: 1px solid #eee;">
                    <td style="padding: 8px 0; color: #666; font-size: 14px;"><strong>Package (LPA)</strong></td>
                    <td style="padding: 8px 0; font-size: 14px; text-align: right; color: #2e7d32; font-weight: bold;">
                        {max_pkg} (Max) / {min_pkg} (Min)
                    </td>
                </tr>
                <tr style="border-bottom: 1px solid #eee;">
                    <td style="padding: 8px 0; color: #666; font-size: 14px;"><strong>Registration Window</strong></td>
                    <td style="padding: 8px 0; font-size: 14px; text-align: right;">
                        {reg_start}<br/>
                        to<br/>
                        {reg_end}
                    </td>
                </tr>
                <tr style="border-bottom: 1px solid #eee;">
                    <td style="padding: 8px 0; color: #666; font-size: 14px;"><strong>Academic Cycle</strong></td>
                    <td style="padding: 8px 0; font-size: 14px; text-align: right;">{academic_year}</td>
                </tr>
                <tr style="border-bottom: 1px solid #eee;">
                    <td style="padding: 8px 0; color: #666; font-size: 14px;"><strong>Your Matched Branch</strong></td>
                    <td style="padding: 8px 0; font-size: 14px; text-align: right; color: #1976d2; font-weight: bold;">{user_branch_esc}</td>
                </tr>
            </table>

            <div style="background-color: #f1f8ff; border-left: 4px solid #0366d6; padding: 12px; margin: 18px 0; border-radius: 4px; font-size: 13px;">
                <strong>Eligible Programs:</strong><br/>
                <span style="color: #444;">{programs_esc}</span>
            </div>

            <div style="text-align: center; margin: 24px 0 10px 0;">
                {portal_button}
            </div>
            
            <p style="font-size: 12px; color: #777; text-align: center; margin-top: 10px;">Detected at: {now_ist_esc}</p>
        </div>

        <div style="background-color: #fafbfc; border-top: 1px solid #eaecef; padding: 14px 24px; font-size: 12px; color: #6a737d; text-align: center;">
            <p style="margin: 0 0 6px 0;">You are receiving this because you subscribed with your VIT Pune 2028 email.</p>
            <p style="margin: 0;">
                <a href="{safe_pref_url}" style="color: #0366d6; text-decoration: none;">Manage Preferences</a>
                &nbsp;|&nbsp;
                <a href="{safe_unsub_url}" style="color: #d73a49; text-decoration: none;">Unsubscribe</a>
            </p>
        </div>
    </div>
</body>
</html>"""
        return html

    async def process_batch_once(self) -> int:
        """
        Processes a single batch of pending deliveries with recoverable lease.
        1. Recovers any stale leases.
        2. Claims a batch of deliveries atomically.
        3. Delivers each email with rate pacing.
        Returns the number of successfully delivered notifications.
        """
        if self.notification_pipeline is not None:
            owner=getattr(self.notification_pipeline,'runtime_owner',None)
            if owner is not None:owner.require_running('drain')
            return await self._process_v2_once()
        from app.monitoring.runtime_supervisor import current_runtime
        from app.database.event_repository import PolicyDenied
        if current_runtime(getattr(self.delivery_repo,'db_path',None)) is not None:
            raise PolicyDenied('guarded runtime excludes legacy subscriber claims')
        # 1. Recover stale leases from crashed workers
        recovered = self.delivery_repo.recover_stale_leases()
        if recovered > 0:
            logger.info(f"Recovered {recovered} stale delivery leases back to PENDING.")

        # 2. Claim pending batch
        deliveries = self.delivery_repo.claim_pending_batch(
            batch_size=self.batch_size,
            lease_seconds=self.lease_seconds
        )
        if not deliveries:
            return 0

        logger.info(f"Delivery worker claimed batch of {len(deliveries)} notifications.")
        delivered_count = 0
        base_url = getattr(settings, "BASE_URL", "http://127.0.0.1:8000")

        for d in deliveries:
            delivery_id = d["id"]
            user_id = d["user_id"]
            user_email = d["user_email"]
            user_branch = d.get("user_branch", "VIT")
            notif_type = d["notification_type"]

            try:
                # Parse raw company data
                raw_json = d.get("company_raw_json")
                company_dict = {}
                if raw_json:
                    try:
                        company_dict = json.loads(raw_json)
                    except Exception:
                        pass
                if not company_dict.get("id"):
                    company_dict["id"] = str(d["company_id"])
                if not company_dict.get("company"):
                    company_dict["company"] = d.get("company_name", "TPO Company")
                company = CompanyRecord(**company_dict)

                # Generate dedicated, finite-expiry, one-time tokens for security
                unsub_token = self._generate_token(user_id, "UNSUBSCRIBE", timedelta(days=30))
                pref_token = self._generate_token(user_id, "MANAGE_PREFS", timedelta(minutes=15))

                unsub_url = f"{base_url}/unsubscribe#token={unsub_token}"
                pref_url = f"{base_url}/preferences/confirm#token={pref_token}"
                one_click_url = f"{base_url}/api/v1/unsubscribe/one-click?token={unsub_token}"

                extra_headers = {
                    "List-Unsubscribe": f"<{one_click_url}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"
                }

                html_content = self._format_opportunity_email(
                    company_record=company,
                    notif_type=notif_type,
                    user_branch=user_branch,
                    unsub_url=unsub_url,
                    pref_url=pref_url
                )

                subject = f"{'🚨 NEW' if notif_type == 'NEW' else '⚠️ UPDATE'}: {company.company} on VIT TPO"

                # Send email directly to the student's email address
                success = False
                if hasattr(self.notifier, "_send_email_to"):
                    success = self.notifier._send_email_to(user_email, subject, html_content, extra_headers=extra_headers)
                else:
                    # Use fallback helper if method doesn't exist
                    success = self._send_smtp_email(user_email, subject, html_content, extra_headers=extra_headers)

                if success:
                    self.delivery_repo.mark_sent(delivery_id)
                    delivered_count += 1
                    logger.info(f"Delivered notification {delivery_id} to {user_email} for {company.company}")
                else:
                    self.delivery_repo.release_failed(delivery_id, "SMTP dispatch returned False")
                    logger.warning(f"Failed to deliver notification {delivery_id} to {user_email}")

            except Exception as e:
                logger.error(f"Error processing delivery {delivery_id}: {e}")
                self.delivery_repo.release_failed(delivery_id, str(e))

            # Pacing delay between emails to respect SMTP burst limits
            await asyncio.sleep(self.send_interval_seconds)

        return delivered_count

    async def _process_v2_once(self):
        from app.database.event_repository import CommitUnknown, Fenced, PolicyDenied
        from app.monitoring.notification_pipeline import TransportResult
        pipeline=self.notification_pipeline
        self.last_v2_attempts=0
        if pipeline.closed or not pipeline.options.drain or not pipeline.options.transport:
            return 0
        repo=pipeline.repository
        pipeline.recover_once()
        pipeline.fanout_once()
        sent=0
        for _ in range(min(25,self.batch_size)):
            if pipeline.production_binding is not None and pipeline.production_binding.state!='PILOT':break
            claim=None
            for channel in (self._v2_channel,"SUBSCRIBER" if self._v2_channel=="ADMIN" else "ADMIN"):
                try:
                    from app.notifications.transport_arbiter import AuthorityGateway
                    if isinstance(pipeline.transport,AuthorityGateway) and not pipeline.transport.prepare_slot(channel):
                        continue
                    claim=repo.claim_delivery(channel=channel,lease_ms=60000 if pipeline.production_binding is not None else 100,
                        lose_ack=bool(pipeline.faults.get("claim_ack")))
                except CommitUnknown as exc:
                    try:
                        claim=repo.inspect_claim(exc.key)
                    except Fenced:
                        claim=None
                except Fenced:
                    claim=None
                if claim is not None:break
            if claim is None:break
            self._v2_channel="SUBSCRIBER" if channel=="ADMIN" else "ADMIN"
            self.last_v2_attempts+=1
            try:
                row=repo._delivery(claim,("PROCESSING",))
                links={}
                headers={}
                if row["channel"]=="SUBSCRIBER":
                    association=repo._one("SELECT user_id FROM recipient_associations WHERE recipient_key=?",(row["recipient_key"],))
                    user_id=association["user_id"]
                    unsubscribe=self._generate_token(user_id,"UNSUBSCRIBE",timedelta(days=30))
                    preferences=self._generate_token(user_id,"ACCOUNT_LOGIN" if pipeline.production_binding is not None else "MANAGE_PREFS",timedelta(minutes=15))
                    base=getattr(settings,"BASE_URL","http://127.0.0.1:8000")
                    base=getattr(pipeline.transport,"action_base",base)
                    links={"unsubscribe":base+"/unsubscribe#token="+unsubscribe,
                           "preferences":base+"/preferences/confirm#token="+preferences}
                    headers={"List-Unsubscribe":"<"+base+"/api/v1/unsubscribe/one-click?token="+unsubscribe+">",
                             "List-Unsubscribe-Post":"List-Unsubscribe=One-Click"}
                message=pipeline.render_delivery(claim,links)
                message["headers"]=headers
            except Fenced:
                # Consent or a newer owner may already have retired this claim.
                # Leave its durable state intact; expired preparation is recovered
                # by the next pump quantum.
                continue
            except Exception:
                try:
                    repo.fail_preparation(claim,"RENDER_OR_LINK_PREPARATION")
                except Fenced:
                    pass
                continue
            try:
                permit=repo.authorize_send(claim,lose_ack=bool(pipeline.faults.get("send_ack")))
            except CommitUnknown:
                repo.recover_delivery(claim.key,unknown_commit=True)
                continue
            except Fenced:
                continue
            if permit is None:continue
            try:
                if pipeline.production_binding is not None:
                    result=await pipeline.production_binding.submit_delivery(permit,message)
                else:result=repo.submit_fake(permit,pipeline.transport,message=message)
                if not isinstance(result,TransportResult):
                    raise PolicyDenied("typed fictional transport result required")
                if pipeline.production_binding is None:
                    repo.record_fact(permit.invocation_id,result.kind,fact_key="finalized-message-result")
                if result.ended and pipeline.production_binding is None:
                    repo.record_fact(permit.invocation_id,"ENDED",fact_key="positive-fake-message-end")
                if result.kind=="NOT_ACCEPTED" and not result.ended:
                    repo.recover_delivery(claim.key,unknown_commit=True)
                    continue
                try:
                    status=repo.acknowledge(claim,lose_ack=bool(pipeline.faults.get("ack")))
                except CommitUnknown:
                    status=repo._one("SELECT status FROM notification_deliveries_v2 WHERE delivery_id=?",(claim.key,))["status"]
                sent+=int(status=="SENT")
                if pipeline.production_binding is not None and pipeline.production_binding.state!='PILOT':
                    break
            except Exception:
                # T6 is durable. An exception is never definitive nonacceptance or
                # physical END, and must never create an automatic physical resend.
                if pipeline.production_binding is not None:
                    repo.record_fact(permit.invocation_id,"UNKNOWN",fact_key="message-exception",
                        evidence_type='PRODUCTION_SMTP_ADAPTER',evidence_reference='exception:'+permit.invocation_id)
                else:repo.record_fact(permit.invocation_id,"UNKNOWN",fact_key="message-exception")
                repo.recover_delivery(claim.key,unknown_commit=True)
        return sent

    async def run_v2_loop(self,stop_event,*,wait=asyncio.sleep):
        """Independent injectable pump; no source adapter is called here."""
        if self.notification_pipeline is None:
            raise ValueError("explicit local v2 pipeline required")
        try:
            while not stop_event.is_set() and not self.notification_pipeline.closed:
                await self.process_batch_once()
                await wait(self.notification_pipeline.next_wait_ms()/1000)
        finally:
            self.notification_pipeline.shutdown()

    def _send_smtp_email(self, to_email: str, subject: str, html_content: str, extra_headers: Optional[dict] = None) -> bool:
        """Fallback direct SMTP sender using settings."""
        from app.monitoring.runtime_supervisor import current_runtime
        from app.database.event_repository import PolicyDenied
        if current_runtime(getattr(self.delivery_repo,'db_path',None)) is not None:
            raise PolicyDenied('guarded runtime excludes fallback SMTP')
        from app.notifications.transport_arbiter import AuthorityGateway
        if self.notification_pipeline is not None or isinstance(getattr(self.notifier,"transport",None),AuthorityGateway):
            raise ValueError("fictional gated transport cannot fall back to SMTP")
        import smtplib
        from email.message import EmailMessage

        if not settings.SMTP_HOST or not settings.SMTP_USERNAME:
            logger.warning("SMTP configuration missing. Cannot send delivery.")
            return False

        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = settings.EMAIL_FROM or settings.SMTP_USERNAME
        msg['To'] = to_email
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
            return True
        except Exception as e:
            logger.error(f"Direct SMTP send failed to {to_email}: {e}")
            return False
