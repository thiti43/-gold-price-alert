#!/usr/bin/env python3
"""Gold XAU/USD: 1-second market monitoring and 60-second SMA checks."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from gold_alert import load_env, parse_timestamp, send_push
from xau_default import (
    HISTORY_URL,
    SPOT_URL,
    SMA_PERIOD,
    calculate_quote,
    market_session,
    read_json,
    status_line,
    valid_number,
)

STATE_PATH = Path(".gold_market_state.json")
CHECK_INTERVAL = 60
WATCH_SECONDS = 300


def read_state():
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(STATE_PATH)


def notify(kind, state):
    price = state.get("last_price")
    observed = state.get("last_quote_at")

    send_push({
        "kind": kind,
        "symbol": f"XAU/USD · SMA{SMA_PERIOD} (1D)",
        "price": (
            f"{Decimal(str(price)):.2f}"
            if price is not None else "ยังไม่มีข้อมูล"
        ),
        "average": (
            f"{Decimal(str(state['last_average'])):.2f}"
            if state.get("last_average") is not None
            else "ยังไม่มีข้อมูล"
        ),
        "unit": "USD/oz",
        "at": observed or datetime.now(timezone.utc).isoformat(),
        "source": "XAUS.com indicative reference price",
    })


def fetch_last_reference(state):
    data = read_json(SPOT_URL)
    xau = data.get("xau")

    if (
        not isinstance(xau, dict)
        or xau.get("currency") != "USD"
        or xau.get("unit") not in ("oz", "troy_oz")
    ):
        raise ValueError("Invalid XAU/USD quote unit")

    price = valid_number(data.get("spot_usd_oz"), "spot_usd_oz")
    other = valid_number(xau.get("price"), "xau.price")

    if abs(price - other) > Decimal("0.01"):
        raise ValueError("Inconsistent XAU/USD price")

    feed_state = data.get("data_state")
    timestamp = (
        feed_state.get("as_of")
        if isinstance(feed_state, dict) else None
    ) or data.get("updated_at")

    observed = parse_timestamp(timestamp)

    if observed > datetime.now(timezone.utc):
        raise ValueError("Future quote timestamp")

    # Never replace a newer saved quote with an older quote.
