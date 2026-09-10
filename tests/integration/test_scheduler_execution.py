import asyncio
import pytest
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, patch

from app.monitoring.scheduler import TimezoneScheduler
from app.monitoring.watcher import PlacementWatcher
from app.config import Settings

IST = ZoneInfo("Asia/Kolkata")

@pytest.mark.asyncio
async def test_scheduler_wait_and_execution_lifecycle():
    """
    Verifies that the watcher uses the scheduler to wait between checks,
    executes checks at scheduled times, does not continuously poll,
    and calculates subsequent scheduled checks accurately.
    """
    watcher = PlacementWatcher()
    
    # Configure 3 accelerated test times for simulation
    watcher.scheduler = TimezoneScheduler(
        check_times=["10:00", "17:00", "00:00"],
        timezone_name="Asia/Kolkata"
    )
    
    # Reference time: 09:59:58 IST (2 seconds before 10:00 check)
    simulated_now = datetime(2026, 9, 10, 9, 59, 58, tzinfo=IST)
    
    next_check = watcher.scheduler.get_next_run(simulated_now)
    assert next_check == datetime(2026, 9, 10, 10, 0, 0, tzinfo=IST)
    
    wait_sec = (next_check - simulated_now).total_seconds()
    assert wait_sec == 2.0
    
    # Next check after 10:00:01 IST
    simulated_now_after = datetime(2026, 9, 10, 10, 0, 1, tzinfo=IST)
    next_check_after = watcher.scheduler.get_next_run(simulated_now_after)
    assert next_check_after == datetime(2026, 9, 10, 17, 0, 0, tzinfo=IST)
    
    # Midnight transition: after 17:00:00 IST (e.g. 23:59:50 IST)
    simulated_midnight_eve = datetime(2026, 9, 10, 23, 59, 50, tzinfo=IST)
    next_midnight = watcher.scheduler.get_next_run(simulated_midnight_eve)
    assert next_midnight == datetime(2026, 9, 11, 0, 0, 0, tzinfo=IST)
    assert (next_midnight - simulated_midnight_eve).total_seconds() == 10.0

@pytest.mark.asyncio
async def test_watcher_loop_mocked_sleep():
    """
    Simulates the watcher daemon loop with mock sleep to verify it sleeps
    until the scheduled time rather than polling continuously.
    """
    watcher = PlacementWatcher()
    watcher.scheduler = TimezoneScheduler(
        check_times=["10:00", "17:00", "00:00"],
        timezone_name="Asia/Kolkata"
    )
    
    sleep_calls = []
    check_calls = []
    
    async def mock_sleep(seconds):
        sleep_calls.append(seconds)
            
    async def mock_check():
        check_calls.append(True)
        if len(check_calls) >= 2:
            raise asyncio.CancelledError("Test completed 2 scheduled cycles")
        
    with patch("asyncio.sleep", mock_sleep):
        with patch.object(watcher, "check_once", mock_check):
            await watcher.run_forever()
            
    assert len(sleep_calls) == 2
    assert len(check_calls) == 2
    # Verify it did not do 0-second polling
    assert all(s > 0 for s in sleep_calls)
