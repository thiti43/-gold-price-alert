#!/usr/bin/env python3
"""Ready-to-run XAU/USD 1D SMA20 crossing alerts (XAUS price-reference feed).

NOT a GOLD NOW broker feed. Data providers may differ; never place trades with this.
Python 3.10+; no dependencies beyond standard library.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gold_alert import (
    AlertService, BASE_DIR, CrossingDetector, Quote, fmt, load_env, parse_timestamp,
    send_push, setup, utc_now
)

LOG = logging.getLogger("xau-default")
SPOT_URL = "https://xaus.com/api/v1/spot?compact=1"
HISTORY_URL = "https://xaus.com/api/v1/history"
SOURCE = "XAUS.com indicative XAU/USD (not GOLD NOW)"
DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_HISTORY_REFRESH_SECONDS = 6 * 3600
DEFAULT_MAX_SPOT_AGE_SECONDS = 300
RESET_GAP_SECONDS = 600


def read_json(url: str, *, max_bytes: int = 3_000_000) -> dict:
    if url not in (SPOT_URL, HISTORY_URL):
        raise ValueError("Only documented XAUS API URLs allowed")
    req = Request(url, headers={"User-Agent": "Gold-XAU-1D-SMA20-PersonalMonitor/1.0",
                                "Accept": "application/json", "Cache-Control": "no-cache"})
    with urlopen(req, timeout=18) as response:
        if response.status != 200:
            raise RuntimeError(f"Source returned HTTP {response.status}")
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("Source response too large")
    result = json.loads(raw.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object")
    return result


def valid_number(value: object, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"Invalid {label}")
    try:
        num = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"Missing or invalid {label}") from exc
    if not num.is_finite() or num <= 0:
        raise ValueError(f"Non-positive or invalid {label}")
    return num


def spot_from_data(data: dict, *, now: datetime | None = None) -> tuple[Decimal, datetime]:
    """Reject stale/last-known-good values; never relabel fetch time as quote time."""
    state = data.get("data_state", {})
    if not isinstance(state, dict) or state.get("status") != "fresh":
        raise ValueError("Price is not marked fresh by XAUS; no alert")
    observed = state.get("as_of") or data.get("updated_at")
    ts = parse_timestamp(observed)
    now = now or utc_now()
    age = (now - ts).total_seconds()
    if age > DEFAULT_MAX_SPOT_AGE_SECONDS:
        raise ValueError(f"Spot price is {age:.0f}s old; skipping alerts")
    if age < -60:
        raise ValueError("Spot quote timestamp is in the future")
    xau = data.get("xau")
    if not isinstance(xau, dict) or xau.get("currency") != "USD" or xau.get("unit") not in ("oz", "troy_oz"):
        raise ValueError("Incorrect spot quote unit: expected USD/troy oz")
    price = valid_number(data.get("spot_usd_oz"), "spot_usd_oz")
    # Cross-check the underlying USD/oz quote before alerting.
    if abs(price - valid_number(xau.get("price"), "xau.price")) > Decimal("0.01"):
        raise ValueError("Inconsistent XAU data fields")
    return price, ts


def completed_closes(data: dict, *, spot_time: datetime) -> list[tuple[date, Decimal]]:
    """Get 19 latest COMPLETED UTC-date daily bars before spot's current date.

    XAUS history may be from Yahoo while spot is another upstream: a proxy SMA,
    never asserted to be a broker-specific indicator value.
    """
    if data.get("symbol") not in (None, "XAUUSD", "XAU/USD"):
        raise ValueError("Historical instrument must be XAU/USD")
    if data.get("interval") not in (None, "daily", "1d"):
        raise ValueError("Historical interval must be daily")
    state = data.get("data_state")
    if isinstance(state, dict) and state.get("status") == "unavailable":
        raise ValueError("Daily history unavailable")
    rows = data.get("points")
    if not isinstance(rows, list):
        raise ValueError("Missing daily history points")
    unique = {}
    cutoff = spot_time.date()  # UTC-day approximation, not guaranteed broker daily boundary.
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            d = date.fromisoformat(item["d"])
            if d >= cutoff:
                continue
            p = valid_number(item["c"], "history.close")
        except (TypeError, KeyError, ValueError):
            continue
        if d in unique and unique[d] != p:
            raise ValueError(f"Conflicting historical close for {d}")
        unique[d] = p
    filtered = sorted(unique.items())
    if len(filtered) < 19:
        raise ValueError("Need at least 19 completed daily closes")
    last19 = filtered[-19:]
    if (spot_time.date() - last19[-1][0]).days > 6:
        raise ValueError("Most recent daily close is too old")
    return last19


def calculate_quote(spot_data: dict, history_data: dict, *, now: datetime | None = None):
    price, seen_at = spot_from_data(spot_data, now=now)
    prior = completed_closes(history_data, spot_time=seen_at)
    average = (sum((x for _, x in prior), Decimal(0)) + price) / Decimal(20)
    quote = Quote(price=price, average=average, observed_at=seen_at,
                  symbol="XAU/USD · 1D SMA20 (reference)", unit="USD/oz", source=SOURCE)
    anchor = ",".join(d.isoformat() for d, _ in prior)
    return quote, anchor


class HistoryCache:
    def __init__(self, *, refresh_seconds: int = DEFAULT_HISTORY_REFRESH_SECONDS):
        self.refresh_seconds = refresh_seconds
        self.data: dict | None = None
        self.fetched_at: datetime | None = None

    def get(self) -> dict:
        now = utc_now()
        if self.data is not None and self.fetched_at is not None:
            age = (now - self.fetched_at).total_seconds()
            if 0 <= age < self.refresh_seconds:
                return self.data
        # Do not return stale history if refresh failed. Fail closed.
        fresh = read_json(HISTORY_URL)
        if not isinstance(fresh.get("points"), list):
            raise ValueError("History response missing points")
        self.data, self.fetched_at = fresh, now
        return fresh


def status_line(q: Quote) -> str:
    difference = q.price - q.average
    sign = "เหนือ" if difference > 0 else "ใต้" if difference < 0 else "เท่ากับ"
    return (f"XAU/USD {fmt(q.price)} USD/oz  |  SMA20 {fmt(q.average)} USD/oz  "
            f"|  {sign}เส้นเฉลี่ย  |  quote: {q.observed_at.isoformat()}")


def reset_on_gap(service: AlertService, quote: Quote, *, anchor: str) -> None:
    last_anchor = service.state.get("xau_anchor")
    last_ts = service.state.setdefault("detector", {}).get("last_seen_at")
    gap = None
    if last_ts:
        gap = (quote.observed_at - parse_timestamp(last_ts)).total_seconds()
    if (last_anchor is not None and anchor != last_anchor) or (gap is not None and gap > RESET_GAP_SECONDS):
        # A change in daily candle set or a monitoring gap must not create an
        # artificial cross when restarting or on a new daily candle boundary.
        service.state["detector"].clear()
        LOG.info("Re-established baseline after new 1D candle or monitoring gap")
    service.state["xau_anchor"] = anchor


def run_once(cache: HistoryCache, *, service: AlertService | None = None,
             notify: bool = False) -> Quote:
    spot = read_json(SPOT_URL)
    history = cache.get()
    quote, anchor = calculate_quote(spot, history)
    print(status_line(quote), flush=True)
    if notify:
        if service is None:
            raise ValueError("Service missing")
        reset_on_gap(service, quote, anchor=anchor)
        service.accept(quote)
    return quote


def run_demo() -> None:
    """Use fake XAU values and send a few test pushes to the phone."""
    print("DEMO ONLY: Fake XAU/USD values. Not real market prices.", flush=True)
    now = utc_now()
    detector = CrossingDetector(cooldown=0, max_quote_age=3600)
    samples = [(4001, 4005), (4003, 4005), (4006, 4005),
               (4005, 4005), (4004, 4005), (4003, 4005)]
    count = 0
    for idx, (price, avg) in enumerate(samples):
        q = Quote(Decimal(price), Decimal(avg), now + timedelta(seconds=idx),
                  "XAU/USD DEMO (fake)", "USD/oz", "SIMULATION NOT MARKET")
        event = detector.evaluate(q, check_freshness=False)
        print(f"{idx + 1}. fake spot={price}, fake SMA={avg}")
        if event:
            send_push(event)
            count += 1
            print("   Test notification accepted by ntfy")
    print(f"Demo pushes: {count}; check your phone.")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(description="XAU/USD 1D SMA20 -> ntfy push (NOT broker-specific)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Generate secret ntfy topic")
    sub.add_parser("test-push", help="Send notification test")
    sub.add_parser("check", help="Check spot/SMA20 without push")
    sub.add_parser("run", help="Monitor price and notify on crossing")
    sub.add_parser("demo", help="Simulated XAU/USD crossing alerts, no market feed")
    args = parser.parse_args(argv)
    load_env()
    try:
        if args.command == "setup":
            setup()
        elif args.command == "test-push":
            send_push(is_test=True)
            print("ntfy accepted test message. Check phone subscription + notification settings.")
        elif args.command == "demo":
            run_demo()
        elif args.command == "check":
            run_once(HistoryCache())
        elif args.command == "run":
            if not os.getenv("NTFY_TOPIC", "").strip():
                raise ValueError("Run setup first to generate ntfy topic")
            interval = int(os.getenv("XAU_POLL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)))
            if interval < 60:
                raise ValueError("XAU_POLL_SECONDS must be >= 60 (XAUS fair-use)")
            cache = HistoryCache()
            service = AlertService()
            print("Monitoring reference XAU/USD (not GOLD NOW). 1D/SMA20, Ctrl+C to stop.")
            while True:
                try:
                    run_once(cache, service=service, notify=True)
                except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError,
                        json.JSONDecodeError, OSError) as exc:
                    LOG.warning("No valid quote/notification this round: %s", exc)
                time.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped. No more alerts until this program runs again.")
    except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError,
            json.JSONDecodeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
