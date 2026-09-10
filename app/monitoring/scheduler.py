import logging
from datetime import datetime, time, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

def parse_time_str(t_str: str) -> time:
    """Parses 'HH:MM' string into a datetime.time object."""
    parts = t_str.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format '{t_str}'. Expected 'HH:MM'.")
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Time out of range: {t_str}")
    return time(hour=hour, minute=minute)

class TimezoneScheduler:
    def __init__(self, check_times: Optional[List[str]] = None, timezone_name: str = "Asia/Kolkata"):
        self.timezone_name = timezone_name
        self.tz = ZoneInfo(timezone_name)
        
        raw_times = check_times or ["10:00", "17:00", "00:00"]
        self.check_times = sorted([parse_time_str(t) for t in raw_times])
        if not self.check_times:
            raise ValueError("Scheduler must have at least one check time configured.")
            
        logger.info(
            f"Initialized TimezoneScheduler [Timezone: {self.timezone_name}, "
            f"Configured Times: {[t.strftime('%H:%M') for t in self.check_times]}]"
        )

    def get_next_run(self, reference_time: Optional[datetime] = None) -> datetime:
        """
        Calculates the next scheduled check datetime in Asia/Kolkata timezone.
        Correctly transitions across midnight to the next calendar day.
        """
        if reference_time is None:
            ref = datetime.now(self.tz)
        else:
            if reference_time.tzinfo is None:
                ref = reference_time.replace(tzinfo=self.tz)
            else:
                ref = reference_time.astimezone(self.tz)

        # Candidates for today
        candidates_today = [
            datetime.combine(ref.date(), t, tzinfo=self.tz)
            for t in self.check_times
        ]
        
        # Look for upcoming candidates today (strictly greater than current reference time)
        upcoming_today = [c for c in candidates_today if c > ref]
        if upcoming_today:
            return min(upcoming_today)
            
        # If all checks for today have passed, advance to tomorrow
        tomorrow = ref.date() + timedelta(days=1)
        candidates_tomorrow = [
            datetime.combine(tomorrow, t, tzinfo=self.tz)
            for t in self.check_times
        ]
        return min(candidates_tomorrow)

    def get_seconds_until_next_run(self, reference_time: Optional[datetime] = None) -> float:
        """Returns the number of seconds until the next scheduled check."""
        if reference_time is None:
            ref = datetime.now(self.tz)
        else:
            if reference_time.tzinfo is None:
                ref = reference_time.replace(tzinfo=self.tz)
            else:
                ref = reference_time.astimezone(self.tz)
                
        next_run = self.get_next_run(ref)
        delta = (next_run - ref).total_seconds()
        return max(0.0, delta)
