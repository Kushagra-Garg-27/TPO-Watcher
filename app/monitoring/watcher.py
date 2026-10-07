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

logger = logging.getLogger(__name__)

class PlacementWatcher:
    def __init__(self, db: Optional[DatabaseRepository] = None, db_path: Optional[str] = None):
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

    async def _process_notifications(self):
        pending = self.db.get_pending_notifications()
        if not pending:
            return
            
        logger.info(f"Processing {len(pending)} pending notifications...")
        
        for notif in pending:
            company_dict = self.db.get_company(notif["company_id"])
            if not company_dict:
                logger.error(f"Company {notif['company_id']} not found for notification {notif['id']}. Removing.")
                self.db.remove_pending_notification(notif["id"])
                continue
                
            # Convert dict back to something the notifier can use, or just use dict.
            # Notifier expects CompanyRecord. Let's adapt it.
            record = CompanyRecord(
                id=company_dict["id"],
                company=company_dict["company"],
                company_code=company_dict.get("company_code"),
                regStartdate=company_dict.get("registration_start"),
                regEnddate=company_dict.get("registration_end"),
                maxPackage=company_dict.get("max_package"),
                minPackage=company_dict.get("min_package"),
                placementtype=company_dict.get("placement_type"),
                academicyear=company_dict.get("academic_year"),
                companytype=company_dict.get("company_type"),
                internshiptype=company_dict.get("internship_type"),
                skill=company_dict.get("skills"),
                tpoprogram=company_dict.get("eligible_programs"),
                organization=company_dict.get("organizations"),
                isactive=company_dict.get("is_active")
            )
            
            success = False
            if notif["notification_type"] == "NEW":
                target_match = self._matches_target(company_dict)
                success = self.notifier.send_new_company_notification(record, target_match)
            elif notif["notification_type"] == "UPDATE":
                changes = json.loads(notif["changes_json"])
                success = self.notifier.send_update_notification(record, changes)
                
            if success:
                self.db.mark_notified(record.id)
                self.db.remove_pending_notification(notif["id"])
                logger.info(f"Notification successfully delivered for {record.company} (ID: {record.id}). Status: SENT.")
            else:
                logger.warning(f"Failed to deliver notification for {record.company} (ID: {record.id}). Remains PENDING.")

    async def check_once(self):
        """Performs a single check iteration with automatic re-authentication recovery."""
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
                    for nc in result.new_companies:
                        self.fanout.dispatch_opportunity(nc, "NEW")
                if result.updated_companies:
                    logger.info(f"Updated companies ({len(result.updated_companies)}): {[f'{c[0].company} (ID: {c[0].id})' for c in result.updated_companies]}")
                    for uc, changes in result.updated_companies:
                        self.fanout.dispatch_opportunity(uc, "UPDATE")
                if result.deactivated_companies:
                    logger.info(f"Reconciled/deactivated companies absent from feed ({len(result.deactivated_companies)}): {result.deactivated_companies}")
                if not result.new_companies and not result.updated_companies and not result.deactivated_companies:
                    logger.info("No company changes detected in this check.")
                
                # 4. Process pending admin notifications (Independent safety channel preserved)
                pending_count = len(self.db.get_pending_notifications())
                logger.info(f"Pending admin notifications in queue: {pending_count}")
                await self._process_notifications()

                # 5. Process student subscriber deliveries via recoverable lease worker
                delivered_subs = await self.delivery_worker.process_batch_once()
                if delivered_subs > 0:
                    logger.info(f"Dispatched {delivered_subs} student subscriber notifications.")
                
                logger.info("Check iteration complete.")
                break
                
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
            except Exception as e:
                logger.error(f"Error during check iteration: {e}")
                break

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
                await self.check_once()
                logger.info("TPO check completed successfully.")
                
            except asyncio.CancelledError:
                logger.info("Watcher service stopped.")
                break
            except Exception as e:
                logger.error(f"Unexpected error in main scheduling loop: {e}")
                await asyncio.sleep(60)

    async def shutdown(self):
        await self.auth.stop()
