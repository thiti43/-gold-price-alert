
#!/usr/bin/env python3
"""Gold XAU/USD: 1-second market monitoring and 60-second SMA5 checks."""

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
    old = state.get("last_quote_at")
    if old and parse_timestamp(old) > observed:
        return

    state["last_price"] = str(price)
    state["last_quote_at"] = observed.isoformat()
    save_state(state)


def check_market(state):
    phase, reason = market_session(datetime.now(timezone.utc))
    previous = state.get("phase")

    if phase == "closed":
        if previous != "closed":
            # Keep last saved price if available.
            notify(
                f"🔴 ตลาดทองปิด ({reason}) | "
                "ราคาอ้างอิงล่าสุด ไม่ใช่ราคาสด",
                state,
            )
            state["phase"] = "closed"
            save_state(state)
            print("Market CLOSED notification sent", flush=True)
        return phase

    if previous == "closed":
        notify(
            "🟢 ตลาดทองเปิดแล้ว | "
            "ราคาอ้างอิงล่าสุด รอราคาสดอัปเดต",
            state,
        )
        state["phase"] = "open"
        save_state(state)
        print("Market OPEN notification sent", flush=True)

    elif previous is None:
        state["phase"] = "open"
        save_state(state)

    return phase


def check_price(state, phase):
    if phase == "closed":
        if not state.get("last_price"):
            fetch_last_reference(state)
        return

    spot = read_json(SPOT_URL)
    history = read_json(HISTORY_URL)
    quote, _ = calculate_quote(spot, history)

    print(status_line(quote), flush=True)

    state["last_price"] = str(quote.price)
    state["last_average"] = str(quote.average)
    state["last_quote_at"] = quote.observed_at.isoformat()
    save_state(state)

    if quote.price > quote.average:
        kind = f"ราคาอยู่เหนือ SMA{SMA_PERIOD}"
    elif quote.price < quote.average:
        kind = f"ราคาอยู่ใต้ SMA{SMA_PERIOD}"
    else:
        kind = f"ราคาเท่ากับ SMA{SMA_PERIOD}"

    notify(kind, state)
    print("SMA notification sent", flush=True)


def main():
    load_env()

    if not os.getenv("NTFY_TOPIC", "").strip():
        print("Missing NTFY_TOPIC", file=sys.stderr)
        return 2

    state = read_state()
    started = time.monotonic()
    next_price_check = started

    print("Starting 1-second market monitor", flush=True)

    while True:
        now = time.monotonic()
        if now - started >= WATCH_SECONDS:
            break

        try:
            phase = check_market(state)
        except Exception as exc:
            print(f"Market check failed: {exc}", flush=True)
            phase = None

        if now >= next_price_check:
            next_price_check = now + CHECK_INTERVAL
            if phase is not None:
                try:
                    check_price(state, phase)
                except Exception as exc:
                    print(f"Price check failed: {exc}", flush=True)

        remaining = WATCH_SECONDS - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(min(1, remaining))

    print("Monitoring window completed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
