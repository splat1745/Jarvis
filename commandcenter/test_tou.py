"""Smallest check that fails if TOU/holiday logic breaks: python test_tou.py"""
from datetime import date, datetime

from server import easter, ontario_holidays, tou_period

assert easter(2026) == date(2026, 4, 5)
assert easter(2025) == date(2025, 4, 20)

h = ontario_holidays(2026)
assert date(2026, 2, 16) in h            # Family Day, 3rd Mon Feb
assert date(2026, 5, 18) in h            # Victoria Day, Mon before May 25
assert date(2026, 4, 3) in h             # Good Friday
assert date(2026, 12, 28) in h           # Boxing Day (Sat) observed Monday

assert tou_period(datetime(2026, 7, 8, 12)) == "on"    # summer weekday noon
assert tou_period(datetime(2026, 7, 8, 8)) == "mid"    # summer weekday 8am
assert tou_period(datetime(2026, 1, 7, 8)) == "on"     # winter weekday 8am
assert tou_period(datetime(2026, 1, 7, 12)) == "mid"   # winter weekday noon
assert tou_period(datetime(2026, 7, 11, 12)) == "off"  # Saturday
assert tou_period(datetime(2026, 7, 1, 12)) == "off"   # Canada Day
assert tou_period(datetime(2026, 1, 7, 3)) == "off"    # overnight

print("TOU checks OK")
