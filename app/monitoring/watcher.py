import os
import asyncio
import logging
import json
from datetime import datetime, timezone
from typing import Optional
from app.config import settings
from app.auth.manager import (
    AuthManager,
    AuthState,
    AuthenticationError,
    AuthStateError,
    MFAEnrollmentRequiredError,
    MFAVerificationRequiredError,
    CredentialsRejectedError,
    AuthenticationTimeoutError,
    UnknownAuthenticationStateError,
    ReauthenticationRequiredError,
    get_state_file_path,
)
from app.tpo.client import TPOClient
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
                # F10: the synchronous provider call runs off the shared event
                # loop; transport latency cannot stall API/scheduler progress.
                if notif["notification_type"] == "NEW":
                    target_match = self._matches_target(company_dict)
                    event_at_iso = company_dict.get("first_seen_at")
                    try:
                        success = await asyncio.to_thread(
                            self.notifier.send_new_company_notification,
                            record, target_match, event_at=event_at_iso)
                    except TypeError:
                        success = await asyncio.to_thread(
                            self.notifier.send_new_company_notification, record, target_match)
                elif notif["notification_type"] == "UPDATE":
                    success = await asyncio.to_thread(
                        self.notifier.send_update_notification,
                        record, json.loads(notif["changes_json"]))
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

    def is_mfa_paused(self) -> bool:
        """Returns True if automated checks are paused awaiting MFA resolution."""
        if not hasattr(self.db, "get_system_state"):
            return False
        return self.db.get_system_state("mfa_pending") == "true"

    def has_session_file_been_refreshed(self) -> bool:
        """Checks if session file mtime is strictly newer than the mtime when MFA pause was engaged."""
        if not hasattr(self.db, "get_system_state"):
            return False
        paused_mtime = self.db.get_system_state("mfa_paused_state_mtime")
        if not paused_mtime:
            return False
        state_file = self.auth.state_file if (self.auth and hasattr(self.auth, "state_file")) else get_state_file_path()
        if os.path.exists(state_file):
            try:
                curr_mtime = os.path.getmtime(state_file)
                return curr_mtime > float(paused_mtime)
            except (OSError, ValueError):
                return False
        return False

    def reset_auth_pause(self):
        """Explicit authorized reset of the MFA pause state."""
        if hasattr(self.db, "set_system_state"):
            self.db.set_system_state("mfa_pending", "false")
            self.db.set_system_state("auth_status", "RESET_REQUESTED")
        logger.info("MFA pause state explicitly reset.")

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

        # Guard: check if automated polling is paused awaiting human MFA resolution
        if self.is_mfa_paused():
            if self.has_session_file_been_refreshed():
                logger.info("Newer session state file detected during MFA pause; verifying session...")
                state_path = self.auth.state_file if (self.auth and hasattr(self.auth, "state_file")) else get_state_file_path()
                curr_mtime = str(os.path.getmtime(state_path)) if os.path.exists(state_path) else "0"
                
                is_valid = False
                try:
                    if self.auth and (not getattr(self.auth, "page", None) or self.auth.page.is_closed()):
                        if hasattr(self.auth, "start"):
                            await self.auth.start()
                    is_valid = await self.auth.is_authenticated()
                except Exception:
                    is_valid = False

                if is_valid:
                    logger.info("Refreshed session successfully verified as authenticated. Clearing MFA pause.")
                    self.db.set_system_state("mfa_pending", "false")
                    self.db.set_system_state("auth_status", AuthState.AUTHENTICATED.value)
                else:
                    logger.warning("Refreshed session file failed authentication verification. Remaining paused.")
                    self.db.set_system_state("mfa_paused_state_mtime", curr_mtime)
                    if self.auth and hasattr(self.auth, "stop"):
                        await self.auth.stop()
                    self.record_heartbeat_failure()
                    send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=False)
                    return False
            else:
                logger.warning("Scheduled check skipped: MFA authentication remains pending human action.")
                self.record_heartbeat_failure()
                send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=False)
                return False

        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                logger.info(f"Starting check iteration (attempt {attempt + 1})...")
                
                # 1. Ensure authentication
                try:
                    is_auth = await self.auth.is_authenticated()
                    logger.info(f"Authentication status: {'Authenticated' if is_auth else 'Session unauthenticated / expired'}")
                    page = await self.auth.get_valid_page()
                except (MFAEnrollmentRequiredError, MFAVerificationRequiredError) as exc:
                    logger.warning("Source check halted: %s. Pausing automated login attempts.", exc.state.value)
                    if hasattr(self.db, "set_system_state"):
                        self.db.set_system_state("auth_status", exc.state.value)
                        self.db.set_system_state("mfa_pending", "true")
                        state_path = self.auth.state_file if (self.auth and hasattr(self.auth, "state_file")) else get_state_file_path()
                        current_mtime = str(os.path.getmtime(state_path)) if os.path.exists(state_path) else "0"
                        self.db.set_system_state("mfa_paused_state_mtime", current_mtime)
                    if self.auth and hasattr(self.auth, "stop"):
                        await self.auth.stop()
                    self.record_heartbeat_failure()
                    send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=False)
                    return False
                except CredentialsRejectedError as exc:
                    logger.error("Source check halted: Credentials rejected by portal.")
                    if hasattr(self.db, "set_system_state"):
                        self.db.set_system_state("auth_status", exc.state.value)
                    if self.auth and hasattr(self.auth, "stop"):
                        await self.auth.stop()
                    self.record_heartbeat_failure()
                    send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=False)
                    return False
                except AuthenticationTimeoutError as exc:
                    logger.error("Source check halted: Authentication timed out.")
                    if hasattr(self.db, "set_system_state"):
                        self.db.set_system_state("auth_status", exc.state.value)
                    if self.auth and hasattr(self.auth, "stop"):
                        await self.auth.stop()
                    self.record_heartbeat_failure()
                    send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=False)
                    return False
                except UnknownAuthenticationStateError as exc:
                    logger.warning("Source check halted: Unrecognized authentication state.")
                    if hasattr(self.db, "set_system_state"):
                        self.db.set_system_state("auth_status", exc.state.value)
                    if self.auth and hasattr(self.auth, "stop"):
                        await self.auth.stop()
                    self.record_heartbeat_failure()
                    send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=False)
                    return False
                except ReauthenticationRequiredError as exc:
                    raise AuthenticationError("Session expired; re-authentication required") from exc
                
                # 2. Fetch data
                logger.info("API request started: Fetching companies from TPO API...")
                client = TPOClient(self.auth.context, page)
                companies = await client.fetch_companies()
                logger.info(f"API request succeeded. Retrieved {len(companies)} companies.")
                # F12: surface accepted-but-empty observations instead of only
                # a green heartbeat.
                if hasattr(self.db, "set_system_state"):
                    self.db.set_system_state("last_observation_company_count", str(len(companies)))
                
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
                if hasattr(self.db, "set_system_state"):
                    self.db.set_system_state(
                        "last_admin_drain_complete", "true" if notifications_complete else "false")

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
                if hasattr(self.db, "set_system_state"):
                    self.db.set_system_state("auth_status", AuthState.AUTHENTICATED.value)
                    self.db.set_system_state("mfa_pending", "false")
                self.record_heartbeat_success()
                send_healthcheck_ping(settings.HEALTHCHECK_WATCHER_URL, success=True)
                return True
                
            except AuthenticationError:
                logger.warning("Authentication failure detected during check.")
                if attempt < max_attempts - 1:
                    logger.info("Recovering session: re-authenticating with fresh session context...")
                    if self.auth and hasattr(self.auth, "stop"):
                        await self.auth.stop()
                    # Do not delete session file here; save_storage_state_safe atomically replaces
                    # on successful login. If re-auth fails or hits MFA, original file is preserved.
                    self.auth = AuthManager()
                    logger.info("Retrying API request with fresh session...")
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
            except Exception:
                logger.error("Unexpected error in pruning scheduling loop.")
                await asyncio.sleep(60)

    async def drain_once(self) -> int:
        """F10: one bounded drain tick, independent of source-check success.

        Existing obligations keep progressing while VIERP is unavailable. All
        claim fencing, consent, cohort and freshness checks still apply per
        delivery; nothing here contacts the source."""
        try:
            return await self.delivery_worker.process_batch_once()
        except Exception as e:
            logger.error("Independent delivery drain iteration failed (%s).", type(e).__name__)
            return 0

    @staticmethod
    def _delivery_drain_interval(explicit=None):
        """F04/F10 deployment gate: the independent drain is default-OFF (0).
        A positive interval opts in; None means disabled."""
        value = explicit if explicit is not None else getattr(
            settings, "DELIVERY_DRAIN_INTERVAL_SECONDS", 0.0)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    async def run_delivery_loop(self, *, interval_seconds: float = None):
        """Bounded independent queue lifecycle: at most one batch per tick.

        Disabled unless DELIVERY_DRAIN_INTERVAL_SECONDS (or an explicit
        interval) is positive — the stale-NEW age policy is undecided, so the
        safe default keeps dispatch coupled to scheduled checks."""
        interval = self._delivery_drain_interval(interval_seconds)
        if interval is None:
            logger.info(
                "Independent subscriber delivery drain is disabled"
                " (DELIVERY_DRAIN_INTERVAL_SECONDS<=0); the queue drains only"
                " within scheduled checks.")
            return
        logger.info("Independent subscriber delivery drain active (interval %.0fs).", interval)
        while True:
            try:
                await self.drain_once()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info("Independent delivery drain stopped.")
                break

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
                # F12: durable missed-start ledger. A gap here distinguishes
                # "scheduler never started a cycle" from "cycle started and
                # failed" without inducing any catch-up polling. A ledger
                # failure must never prevent the check itself.
                try:
                    self.db.set_system_state(
                        "last_scheduled_check_started",
                        datetime.now(timezone.utc).isoformat())
                except Exception:
                    logger.warning("Could not record the scheduled-start ledger entry.")
                succeeded = await self.check_once()
                if succeeded:
                    logger.info("TPO check completed successfully.")
                else:
                    logger.error("TPO check failed.")
                
            except asyncio.CancelledError:
                logger.info("Watcher service stopped.")
                break
            except Exception:
                logger.error("Unexpected error in main scheduling loop.")
                await asyncio.sleep(60)

    async def shutdown(self):
        if self.notification_pipeline is not None:
            self.notification_pipeline.shutdown()
        else:
            await self.auth.stop()
