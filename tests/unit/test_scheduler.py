import pytest
from datetime import datetime, time
from zoneinfo import ZoneInfo
from app.monitoring.scheduler import TimezoneScheduler, parse_time_str

IST = ZoneInfo("Asia/Kolkata")

def test_parse_time_str_valid():
    t = parse_time_str("10:00")
    assert t == time(10, 0)
    
    t_midnight = parse_time_str("00:00")
    assert t_midnight == time(0, 0)
    
    t_evening = parse_time_str("17:30")
    assert t_evening == time(17, 30)

def test_parse_time_str_invalid():
    with pytest.raises(ValueError):
        parse_time_str("invalid")
    with pytest.raises(ValueError):
        parse_time_str("25:00")
    with pytest.raises(ValueError):
        parse_time_str("10:60")

def test_scheduler_initialization():
    scheduler = TimezoneScheduler(check_times=["10:00", "17:00", "00:00"])
    assert scheduler.timezone_name == "Asia/Kolkata"
    # Should be sorted: 00:00, 10:00, 17:00
    assert scheduler.check_times == [time(0, 0), time(10, 0), time(17, 0)]

def test_scheduler_next_run_morning():
    scheduler = TimezoneScheduler(check_times=["10:00", "17:00", "00:00"])
    ref = datetime(2026, 9, 10, 8, 30, 0, tzinfo=IST)
    next_run = scheduler.get_next_run(ref)
    
    assert next_run == datetime(2026, 9, 10, 10, 0, 0, tzinfo=IST)

def test_scheduler_next_run_afternoon():
    scheduler = TimezoneScheduler(check_times=["10:00", "17:00", "00:00"])
    ref = datetime(2026, 9, 10, 11, 15, 0, tzinfo=IST)
    next_run = scheduler.get_next_run(ref)
    
    assert next_run == datetime(2026, 9, 10, 17, 0, 0, tzinfo=IST)

def test_scheduler_midnight_transition():
    scheduler = TimezoneScheduler(check_times=["10:00", "17:00", "00:00"])
    # After 17:00, e.g. 18:30 IST on Sept 10
    ref = datetime(2026, 9, 10, 18, 30, 0, tzinfo=IST)
    next_run = scheduler.get_next_run(ref)
    
    # Should transition to Sept 11, 00:00:00 IST
    assert next_run == datetime(2026, 9, 11, 0, 0, 0, tzinfo=IST)

def test_scheduler_just_before_midnight():
    scheduler = TimezoneScheduler(check_times=["10:00", "17:00", "00:00"])
    ref = datetime(2026, 9, 10, 23, 59, 45, tzinfo=IST)
    next_run = scheduler.get_next_run(ref)
    
    assert next_run == datetime(2026, 9, 11, 0, 0, 0, tzinfo=IST)
    delta = scheduler.get_seconds_until_next_run(ref)
    assert delta == 15.0

def test_scheduler_just_after_midnight():
    scheduler = TimezoneScheduler(check_times=["10:00", "17:00", "00:00"])
    ref = datetime(2026, 9, 11, 0, 0, 10, tzinfo=IST)
    next_run = scheduler.get_next_run(ref)
    
    # Next check should be 10:00 IST on Sept 11
    assert next_run == datetime(2026, 9, 11, 10, 0, 0, tzinfo=IST)

def test_scheduler_custom_check_times():
    scheduler = TimezoneScheduler(check_times=["08:00", "14:00", "20:00"])
    ref = datetime(2026, 9, 10, 9, 0, 0, tzinfo=IST)
    next_run = scheduler.get_next_run(ref)
    
    assert next_run == datetime(2026, 9, 10, 14, 0, 0, tzinfo=IST)
