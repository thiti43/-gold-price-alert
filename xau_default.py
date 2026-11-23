#!/usr/bin/env python3
"""XAU/USD 1D SMA2 TEST configuration (XAUS reference feed).

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
from zoneinfo import ZoneInfo
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
SMA_PERIOD = 2  # TEST ONLY: 1 live price + 1 prior completed daily close


def read_json(url: str, *, max_bytes: int = 3_000_000) -> dict:
    if url not in (SPOT_URL, HISTORY_URL):
        raise ValueError("Only documented XAUS API URLs allowed")
    req = Request(url, headers={"User-Agent": f"Gold-XAU-1D-SMA{SMA_PERIOD}-PersonalMonitor/1.0",
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
    """Get the previous completed UTC daily closes used in the SMA.

    XAUS history may be from Yahoo while spot is another upstream: a proxy SMA,
    never asserted to be a broker-specific indicator value.
    """
    if data.get("symbol") not in (None, "XAUUSD", "XAU/USD"):
        raise ValueError("Historical instrument must be XAU/USD")
