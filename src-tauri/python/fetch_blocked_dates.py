"""
Fetch market event dates from Finnhub economic calendar API.
Generates blocked_dates.json for the backtest engine.

Usage:
  python fetch_blocked_dates.py --from 2025-01-01 --to 2026-12-31
  python fetch_blocked_dates.py --from 2025-01-01 --to 2026-12-31 --output blocked_dates.json

The output JSON has categories of blocked dates:
  fomc, nfp, cpi, triple_witching, monthly_opex, early_close, post_holiday
"""

import json
import os
import sys
import argparse
from datetime import datetime, timedelta
import urllib.request
import urllib.error

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "d3seg11r01qvii72tlf0d3seg11r01qvii72tlfg")

# ── Finnhub economic calendar fetch ──────────────────────────────

def fetch_economic_calendar(from_date: str, to_date: str) -> list:
    """Fetch economic calendar events from Finnhub API."""
    url = f"https://finnhub.io/api/v1/calendar/economic?from={from_date}&to={to_date}&token={FINNHUB_API_KEY}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("economicCalendar", [])
    except urllib.error.HTTPError as e:
        print(f"  Finnhub API error: {e.code} {e.reason}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  Fetch error: {e}", file=sys.stderr)
        return []


def fetch_all_events(from_date: str, to_date: str) -> list:
    """Fetch events in 3-month chunks to stay within Finnhub limits."""
    all_events = []
    start = datetime.strptime(from_date, "%Y-%m-%d")
    end = datetime.strptime(to_date, "%Y-%m-%d")
    
    while start < end:
        chunk_end = min(start + timedelta(days=90), end)
        from_str = start.strftime("%Y-%m-%d")
        to_str = chunk_end.strftime("%Y-%m-%d")
        print(f"  Fetching {from_str} → {to_str}...")
        events = fetch_economic_calendar(from_str, to_str)
        all_events.extend(events)
        start = chunk_end + timedelta(days=1)
    
    return all_events


# ── Event classification ─────────────────────────────────────────

def classify_events(events: list) -> dict:
    """Classify Finnhub events into blocked date categories."""
    categories = {
        "fomc": set(),          # FOMC meeting / rate decision days
        "nfp": set(),           # Non-Farm Payrolls / Employment Situation
        "pre_nfp": set(),       # Day before NFP
        "cpi": set(),           # Consumer Price Index
        "early_close": set(),   # Early close days (known schedule)
        "post_holiday": set(),  # Days after major holiday closures
    }
    
    # Filter US high/medium impact events
    for event in events:
        country = event.get("country", "")
        if country != "US":
            continue
        
        event_name = (event.get("event", "") or "").lower()
        impact = (event.get("impact", "") or "").lower()
        event_time = event.get("time", "")
        
        # Extract date from time field (format: "2025-03-07 13:30:00" or "2025-03-07")
        if not event_time:
            continue
        event_date = event_time[:10]  # YYYY-MM-DD
        
        # FOMC: Interest Rate Decision, Fed Funds Rate, FOMC Statement
        if any(kw in event_name for kw in [
            "interest rate decision", "fed funds rate", "fomc",
            "federal funds rate", "fed interest rate"
        ]):
            categories["fomc"].add(event_date)
        
        # NFP: Non-Farm Payrolls, Employment Situation, Jobs Report
        if any(kw in event_name for kw in [
            "non farm payroll", "nonfarm payroll", "employment situation",
            "nonfarm employment", "jobs report", "non-farm payroll"
        ]):
            categories["nfp"].add(event_date)
            # Pre-NFP: day before
            nfp_date = datetime.strptime(event_date, "%Y-%m-%d")
            pre_nfp = nfp_date - timedelta(days=1)
            categories["pre_nfp"].add(pre_nfp.strftime("%Y-%m-%d"))
        
        # CPI: Consumer Price Index
        if any(kw in event_name for kw in [
            "consumer price index", "cpi ", "cpi,", "core cpi"
        ]):
            categories["cpi"].add(event_date)
    
    return categories


# ── Static calendar dates ────────────────────────────────────────

def compute_triple_witching(start_year: int, end_year: int) -> set:
    """Triple witching: 3rd Friday of March, June, September, December."""
    dates = set()
    for year in range(start_year, end_year + 1):
        for month in [3, 6, 9, 12]:
            # Find 3rd Friday
            first_day = datetime(year, month, 1)
            # Day of week: 0=Mon, 4=Fri
            first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
            third_friday = first_friday + timedelta(weeks=2)
            dates.add(third_friday.strftime("%Y-%m-%d"))
    return dates


def compute_monthly_opex(start_year: int, end_year: int) -> set:
    """Monthly options expiration: 3rd Friday of every month."""
    dates = set()
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            first_day = datetime(year, month, 1)
            first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
            third_friday = first_friday + timedelta(weeks=2)
            dates.add(third_friday.strftime("%Y-%m-%d"))
    return dates


def compute_early_close_days(start_year: int, end_year: int) -> set:
    """Known US market early close days (1 PM ET)."""
    dates = set()
    for year in range(start_year, end_year + 1):
        # July 3rd (if weekday, otherwise nearest)
        july3 = datetime(year, 7, 3)
        if july3.weekday() < 5:  # weekday
            dates.add(july3.strftime("%Y-%m-%d"))
        
        # Day after Thanksgiving (4th Friday of November)
        nov1 = datetime(year, 11, 1)
        first_thu = nov1 + timedelta(days=(3 - nov1.weekday()) % 7)
        fourth_thu = first_thu + timedelta(weeks=3)
        black_friday = fourth_thu + timedelta(days=1)
        dates.add(black_friday.strftime("%Y-%m-%d"))
        
        # Christmas Eve (Dec 24, if weekday)
        dec24 = datetime(year, 12, 24)
        if dec24.weekday() < 5:
            dates.add(dec24.strftime("%Y-%m-%d"))
    return dates


def compute_post_holiday(start_year: int, end_year: int) -> set:
    """Day after major holiday closures (markets reopen, often volatile/thin)."""
    dates = set()
    for year in range(start_year, end_year + 1):
        holidays = []
        
        # New Year's Day: Jan 1 (or Jan 2 if Jan 1 is Sunday)
        ny = datetime(year, 1, 1)
        if ny.weekday() == 6:  # Sunday
            holidays.append(datetime(year, 1, 2))
        elif ny.weekday() < 5:
            holidays.append(ny)
        
        # MLK Day: 3rd Monday of January
        jan1 = datetime(year, 1, 1)
        first_mon = jan1 + timedelta(days=(0 - jan1.weekday()) % 7)
        if first_mon < jan1:
            first_mon += timedelta(weeks=1)
        holidays.append(first_mon + timedelta(weeks=2))
        
        # Presidents Day: 3rd Monday of February
        feb1 = datetime(year, 2, 1)
        first_mon = feb1 + timedelta(days=(0 - feb1.weekday()) % 7)
        if first_mon < feb1:
            first_mon += timedelta(weeks=1)
        holidays.append(first_mon + timedelta(weeks=2))
        
        # Memorial Day: Last Monday of May
        may31 = datetime(year, 5, 31)
        last_mon = may31 - timedelta(days=(may31.weekday() - 0) % 7)
        holidays.append(last_mon)
        
        # Independence Day: July 4 (or nearest weekday)
        jul4 = datetime(year, 7, 4)
        if jul4.weekday() == 5:  # Saturday
            holidays.append(datetime(year, 7, 3))
        elif jul4.weekday() == 6:  # Sunday
            holidays.append(datetime(year, 7, 5))
        else:
            holidays.append(jul4)
        
        # Labor Day: 1st Monday of September
        sep1 = datetime(year, 9, 1)
        first_mon = sep1 + timedelta(days=(0 - sep1.weekday()) % 7)
        if first_mon < sep1:
            first_mon += timedelta(weeks=1)
        holidays.append(first_mon)
        
        # Thanksgiving: 4th Thursday of November
        nov1 = datetime(year, 11, 1)
        first_thu = nov1 + timedelta(days=(3 - nov1.weekday()) % 7)
        if first_thu < nov1:
            first_thu += timedelta(weeks=1)
        holidays.append(first_thu + timedelta(weeks=3))
        
        # Christmas: Dec 25 (or nearest weekday)
        dec25 = datetime(year, 12, 25)
        if dec25.weekday() == 5:
            holidays.append(datetime(year, 12, 24))
        elif dec25.weekday() == 6:
            holidays.append(datetime(year, 12, 26))
        else:
            holidays.append(dec25)
        
        # Post-holiday = next business day after each holiday
        for h in holidays:
            next_day = h + timedelta(days=1)
            while next_day.weekday() >= 5:  # skip weekends
                next_day += timedelta(days=1)
            dates.add(next_day.strftime("%Y-%m-%d"))
    
    return dates


# ── Main ─────────────────────────────────────────────────────────

# Hardcoded known dates (fallback when Finnhub API is unavailable)
# FOMC: announcement day (2nd day of 2-day meeting)
KNOWN_FOMC = {
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
}

# NFP: First Friday of each month (Bureau of Labor Statistics release)
def compute_nfp_dates(start_year: int, end_year: int) -> set:
    dates = set()
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            first_day = datetime(year, month, 1)
            first_fri = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
            if first_fri < first_day:
                first_fri += timedelta(weeks=1)
            dates.add(first_fri.strftime("%Y-%m-%d"))
    return dates

# CPI: Known release dates (typically around 10th-15th of each month)
KNOWN_CPI = {
    # 2025
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
    "2025-09-10", "2025-10-14", "2025-11-12", "2025-12-10",
    # 2026
    "2026-01-13", "2026-02-11", "2026-03-11", "2026-04-14",
    "2026-05-12", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-11", "2026-10-13", "2026-11-10", "2026-12-10",
}

def main():
    parser = argparse.ArgumentParser(description="Fetch market event blocked dates")
    parser.add_argument("--from", dest="from_date", default="2025-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", default="2026-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", "-o", default=None, help="Output JSON path (default: blocked_dates.json next to script)")
    parser.add_argument("--api-key", default=None, help="Finnhub API key (or set FINNHUB_API_KEY env)")
    args = parser.parse_args()
    
    if args.api_key:
        global FINNHUB_API_KEY
        FINNHUB_API_KEY = args.api_key
    
    start_year = int(args.from_date[:4])
    end_year = int(args.to_date[:4])
    
    print("Fetching economic calendar from Finnhub...")
    events = fetch_all_events(args.from_date, args.to_date)
    print(f"  {len(events)} total events fetched")
    
    # Classify API events
    categories = classify_events(events)
    
    # Fallback to hardcoded dates if API returned nothing
    if len(events) == 0:
        print("  API unavailable — using hardcoded FOMC/NFP/CPI dates")
        date_range = set()
        d = datetime.strptime(args.from_date, "%Y-%m-%d")
        end_d = datetime.strptime(args.to_date, "%Y-%m-%d")
        while d <= end_d:
            date_range.add(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        
        categories["fomc"] = KNOWN_FOMC & date_range
        categories["cpi"] = KNOWN_CPI & date_range
        nfp_dates = compute_nfp_dates(start_year, end_year)
        categories["nfp"] = nfp_dates & date_range
        # Pre-NFP: day before each NFP
        pre_nfp = set()
        for nfp in categories["nfp"]:
            pre = datetime.strptime(nfp, "%Y-%m-%d") - timedelta(days=1)
            pre_nfp.add(pre.strftime("%Y-%m-%d"))
        categories["pre_nfp"] = pre_nfp & date_range
    
    # Add static computed dates
    categories["triple_witching"] = compute_triple_witching(start_year, end_year)
    categories["monthly_opex"] = compute_monthly_opex(start_year, end_year)
    categories["early_close"] = compute_early_close_days(start_year, end_year)
    categories["post_holiday"] = compute_post_holiday(start_year, end_year)
    
    # Convert sets to sorted lists
    output = {}
    for cat, dates in categories.items():
        output[cat] = sorted(dates)
    
    # Print summary
    print("\nBlocked dates summary:")
    for cat, dates in sorted(output.items()):
        print(f"  {cat}: {len(dates)} dates")
    
    # Save
    if args.output:
        output_path = args.output
    else:
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blocked_dates.json")
    
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"\nSaved to {output_path}")
    total_unique = len(set().union(*[set(d) for d in output.values()]))
    print(f"Total unique blocked dates: {total_unique}")


if __name__ == "__main__":
    main()
