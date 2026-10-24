import os
import asyncio
import logging
import json
from datetime import datetime, timezone
from typing import Optional
from app.config import settings
from app.auth.manager import AuthManager
from app.tpo.client import TPOClient, AuthenticationError
from app.database.repository import DatabaseRepository
from app.monitoring.detector import CompanyDetector
from app.monitoring.scheduler import TimezoneScheduler
from app.notifications.email import EmailNotificationProvider
from app.tpo.models import CompanyRecord
from app.subscribers.fanout import FanoutEngine
from app.subscribers.worker import DeliveryWorker
from app.monitoring.checkin import send_healthcheck_ping

logger = logging.getLogger(__name__)

class PlacementWatcher:
    def __init__(self, db: Optional[DatabaseRepository] = None, db_path: Optional[str] = None,
                 *, notification_pipeline=None, source_client=None):
        from app.monitoring.runtime_supervisor import current_runtime
        from app.database.repository import DB_PATH
        from app.database.event_repository import PolicyDenied
        runtime=current_runtime(db.db_path if db is not None else db_path or DB_PATH)
        if runtime is not None and (notification_pipeline is None or notification_pipeline is not runtime.pipeline):
            raise PolicyDenied('guarded runtime excludes the legacy writer')
        self.notification_pipeline = notification_pipeline
        self.source_client = source_client
        if notification_pipeline is not None:
            from app.monitoring.notification_pipeline import NotificationPipeline
            if not isinstance(notification_pipeline, NotificationPipeline) or db is None:
                raise ValueError("explicit local v2 pipeline and existing disposable legacy repository required")
            notification_pipeline.bind_legacy(db)
            if getattr(source_client, "synthetic", False) is not True:
                raise ValueError("explicit fictional feed required for local v2 watcher")
            self.db = db
            self.auth = None
            self.delivery_worker = DeliveryWorker(db.deliveries, db.tokens, notification_pipeline=notification_pipeline)
            return
        if db is not None:
            self.db = db
        elif db_path is not None:
            self.db = DatabaseRepository(db_path)
        else:
            self.db = DatabaseRepository()
        self.auth = AuthManager()
        self.detector = CompanyDetector(self.db)
        self.notifier = EmailNotificationProvider()
        self.scheduler = TimezoneScheduler(
            check_times=settings.get_check_times_list(),
            timezone_name=settings.TIMEZONE
        )
        self.prune_scheduler = TimezoneScheduler(
            check_times=["01:00"],
            timezone_name=settings.TIMEZONE
        )
        
        self.target_programs = settings.get_target_programs_list()
        self.fanout = FanoutEngine(self.db.users, self.db.deliveries)
        self.delivery_worker = DeliveryWorker(self.db.deliveries, self.db.tokens, self.notifier)

    def _matches_target(self, company: dict) -> bool:
        if not self.target_programs:
            return False
            
        programs = company.get("eligible_programs") or ""
        programs_lower = programs.lower()
        
        for tp in self.target_programs:
            if tp.lower() in programs_lower:
                return True
        return False

    @staticmethod
    def _notification_record(company_dict, event_snapshot=None):
        """F07: render from the durable event-time snapshot first. Legacy
        obligations without a snapshot keep the established legacy-column
        fallback to the current stored row."""
        if event_snapshot:
            try:
                raw = json.loads(event_snapshot)
                if isinstance(raw, dict):
                    return CompanyRecord(**{**raw, "id": company_dict["id"], "company": company_dict["company"]})
            except (TypeError, ValueError):
                pass
        try:
            raw = json.loads(company_dict.get("raw_data_json") or "null")
            if not isinstance(raw, dict):
                raise ValueError("source object unavailable")
            return CompanyRecord(**{**raw, "id": company_dict["id"], "company": company_dict["company"]})
        except (TypeError, ValueError):
            fields = {
                "company_code": "company_code", "regStartdate": "registration_start",
                "regEnddate": "registration_end", "maxPackage": "max_package",
                "minPackage": "min_package", "placementtype": "placement_type",
                "academicyear": "academic_year", "companytype": "company_type",
                "internshiptype": "internship_type", "skill": "skills",
                "tpoprogram": "eligible_programs", "organization": "organizations", "isactive": "is_active",
            }
            return CompanyRecord(id=company_dict["id"], company=company_dict["company"],
                **{field: company_dict.get(column) for field, column in fields.items()})

    async def _process_notifications(self):
        from app.monitoring.runtime_supervisor import current_runtime
        from app.database.event_repository import PolicyDenied
        if current_runtime(self.db.db_path) is not None:
            raise PolicyDenied('guarded runtime excludes legacy administrative drain')
        pending = self.db.get_pending_notifications()
        complete = True
        for notif in pending:
            try:
                company_dict = self.db.get_company(notif["company_id"])
                if not company_dict:
                    self.db.remove_pending_notification(notif["id"])
                    continue
                record = self._notification_record(company_dict, notif.get("event_snapshot_json"))
                receipt_key = f"notification:v1:fanout:{notif['id']}"
                if self.db.get_system_state(receipt_key) != "complete":
                    # Existing persisted obligations are replayed even when the
                    # next source poll has no DetectionResult changes. Admin
                    # acknowledgment cannot delete work before fanout commits.
                    self.fanout.dispatch_opportunity(record, notif["notification_type"])
                    self.db.set_system_state(receipt_key, "complete")

                success = False
                if notif["notification_type"] == "NEW":
                    target_match = self._matches_target(company_dict)
                    event_at_iso = company_dict.get("first_seen_at")
                    try:
                        success = self.notifier.send_new_company_notification(record, target_match, event_at=event_at_iso)
                    except TypeError:
                        success = self.notifier.send_new_company_notification(record, target_match)
                elif notif["notification_type"] == "UPDATE":
                    success = self.notifier.send_update_notification(record, json.loads(notif["changes_json"]))
                if success:
                    self.db.mark_notified(record.id)
                    self.db.remove_pending_notification(notif["id"])
                    logger.info("Provider accepted admin notification %s; acknowledgment recorded.", notif["id"])
                else:
                    logger.warning("Admin notification %s remains pending.", notif["id"])
            except Exception as exc:
                # A fanout/admin/ack failure keeps this durable obligation and
                # permits other admins and the student drain to make progress.
                complete = False
                logger.error("Notification %s remains pending (%s).", notif["id"], type(exc).__name__)
        return complete

    async def check_once(self):
        """Performs a single check iteration with automatic re-authentication recovery."""
        from app.monitoring.runtime_supervisor import current_runtime
        from app.database.event_repository import PolicyDenied
        runtime=current_runtime(self.db.db_path)
        if runtime is not None:
            if self.notification_pipeline is not runtime.pipeline:raise PolicyDenied('legacy writer excluded')
            runtime.require_running('writer')
        if self.notification_pipeline is not None:
            try:
                await self.notification_pipeline.poll(self.source_client)
                return True
            except Exception as exc:
                logger.warning("Local v2 observation requires recovery (%s)", type(exc).__name__)
                return False
        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                logger.info(f"Starting check iteration (attempt {attempt + 1})...")
                
                # 1. Ensure authentication
                is_auth = await self.auth.is_authenticated()
                logger.info(f"Authentication status: {'Authenticated' if is_auth else 'Session unauthenticated / expired'}")
                page = await self.auth.get_valid_page()
                
                # 2. Fetch data
                logger.info("API request started: Fetching companies from TPO API...")
                client = TPOClient(self.auth.context, page)
                companies = await client.fetch_companies()
                logger.info(f"API request succeeded. Retrieved {len(companies)} companies.")
                
                # 3. Process data (Detects and adds to pending_notifications)
                result = self.detector.process_fetched_companies(companies)
                if result.new_companies:
                    logger.info(f"Newly detected companies ({len(result.new_companies)}): {[f'{c.company} (ID: {c.id})' for c in result.new_companies]}")
                if result.updated_companies:
                    logger.info(f"Updated companies ({len(result.updated_companies)}): {[f'{c[0].company} (ID: {c[0].id})' for c in result.updated_companies]}")
                if result.deactivated_companies:
                    logger.info(f"Reconciled/deactivated companies absent from feed ({len(result.deactivated_companies)}): {result.deactivated_companies}")
                if not result.new_companies and not result.updated_companies and not result.deactivated_companies:
                    logger.info("No company changes detected in this check.")
                
                # 4. Process pending admin notifications (Independent safety channel preserved)
                pending_count = len(self.db.get_pending_notifications())
                logger.info(f"Pending admin notifications in queue: {pending_count}")
                notifications_complete = await self._process_notifications()

                # 5. Process student subscriber deliveries via recoverable lease worker
                delivered_subs = await self.delivery_worker.process_batch_once()
                if delivered_subs > 0:
                    logger.info(f"Dispatched {delivered_subs} student subscriber notifications.")
                
                # Legacy overrides return None after completing normally;
                # the recoverable drain reports an explicit False on failure.
                if notifications_complete is False:
                    self.record_heartbeat_failure()
                    send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=False)
                    return False
                logger.info("Check iteration complete.")
                self.record_heartbeat_success()
                send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=True)
                return True
                
            except AuthenticationError as e:
                logger.warning(f"Authentication failure detected during check: {e}")
                if attempt < max_attempts - 1:
                    logger.info("Recovering session: clearing expired state and re-authenticating...")
                    await self.auth.stop()
                    if os.path.exists("playwright_state.json"):
                        try:
                            os.remove("playwright_state.json")
                        except Exception:
                            pass
                    self.auth = AuthManager()
                    logger.info("Retrying API request with fresh authenticated session...")
                    continue
                else:
                    logger.error("Authentication recovery failed after re-authentication attempt.")
                    self.record_heartbeat_failure()
                    send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=False)
                    return False
            except Exception as e:
                logger.error("Error during check iteration (%s).", type(e).__name__)
                self.record_heartbeat_failure()
                send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=False)
                return False
        self.record_heartbeat_failure()
        send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=False)
        return False

    def record_heartbeat_success(self):
        """Records UTC timestamp of successful scheduled watcher execution."""
        now_utc = datetime.now(timezone.utc).isoformat()
        self.db.set_system_state("last_successful_watcher_run", now_utc)
        self.db.set_system_state("consecutive_watcher_failures", "0")

    def record_heartbeat_failure(self):
        """Records failure timestamp and increments failure count."""
        now_utc = datetime.now(timezone.utc).isoformat()
        self.db.set_system_state("last_failed_watcher_run", now_utc)
        curr = self.db.get_system_state("consecutive_watcher_failures")
        try:
            count = int(curr or 0) + 1
        except ValueError:
            count = 1
        self.db.set_system_state("consecutive_watcher_failures", str(count))

    async def prune_stale_tokens_job(self) -> int:
        """
        Runs daily action-token retention pruning.
        Completely isolated: exceptions are logged and never impact watcher ingestion.
        """
        try:
            count = self.db.tokens.prune_stale_tokens()
            return count
        except Exception as e:
            logger.error("Action-token retention pruning encountered an error: %s", e)
            return 0

    async def run_pruning_loop(self):
        """
        Schedules and executes token retention pruning daily at 01:00 IST.
        Kept strictly separate from watcher ingestion schedules.
        """
        logger.info(f"Token retention pruning scheduler active (01:00 {self.prune_scheduler.timezone_name}).")
        while True:
            try:
                next_prune = self.prune_scheduler.get_next_run()
                now = datetime.now(self.prune_scheduler.tz)
                wait_seconds = (next_prune - now).total_seconds()

                logger.info(f"Next token retention pruning scheduled for {next_prune.strftime('%Y-%m-%d %H:%M:%S')} IST")

                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

                logger.info("Starting daily action-token retention pruning...")
                await self.prune_stale_tokens_job()
                logger.info("Daily action-token retention pruning completed.")

            except asyncio.CancelledError:
                logger.info("Pruning schedule loop stopped.")
                break
            except Exception as e:
                logger.error(f"Unexpected error in pruning scheduling loop: {e}")
                await asyncio.sleep(60)

    async def run_forever(self):
        if self.notification_pipeline is not None:
            from app.database.event_repository import PolicyDenied
            raise PolicyDenied('use the explicit bounded supervisor poll and drain calls')
        logger.info("VIT TPO Placement Watcher service started.")
        logger.info(f"Timezone: {settings.TIMEZONE}")
        check_times_display = ", ".join(settings.get_check_times_list())
        logger.info(f"Configured check times: {check_times_display}")
        
        while True:
            try:
                next_run = self.scheduler.get_next_run()
                now = datetime.now(self.scheduler.tz)
                wait_seconds = (next_run - now).total_seconds()
                
                logger.info(f"Next TPO check scheduled for {next_run.strftime('%Y-%m-%d %H:%M:%S')} IST")
                
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                    
                logger.info("Starting scheduled TPO check...")
                succeeded = await self.check_once()
                if succeeded:
                    logger.info("TPO check completed successfully.")
                else:
                    logger.error("TPO check failed.")
                
            except asyncio.CancelledError:
                logger.info("Watcher service stopped.")
                break
            except Exception as e:
                logger.error(f"Unexpected error in main scheduling loop: {e}")
                await asyncio.sleep(60)

    async def shutdown(self):
        if self.notification_pipeline is not None:
            self.notification_pipeline.shutdown()
        else:
            await self.auth.stop()
