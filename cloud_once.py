
#!/usr/bin/env python3
"""XAU/USD SMA5 alerts plus market open/close notifications with price."""

import json
import os
import sys
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

STATE_PATH = Path(
    os.getenv("GOLD_STATE_FILE", ".gold_market_state.json")
)


def read_state():
    try:
        value = json.loads(
            STATE_PATH.read_text(encoding="utf-8")
        )
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(STATE_PATH)


def push(kind, price, observed_at, average=None):
    send_push({
        "kind": kind,
        "symbol": f"XAU/USD · SMA{SMA_PERIOD} (1D)",
        "price": f"{Decimal(str(price)):.2f}",
        "average": (
            f"{Decimal(str(average)):.2f}"
            if average is not None
            else "ไม่มี SMA ล่าสุด"
        ),
        "unit": "USD/oz",
        "at": observed_at,
        "source": (
            "XAUS.com indicative XAU/USD "
            "(reference, not official close)"
        ),
    })


def last_reference_quote():
    """Get last timestamped price even when the market is closed."""

    data = read_json(SPOT_URL)
    xau = data.get("xau")

    if (
        not isinstance(xau, dict)
        or xau.get("currency") != "USD"
        or xau.get("unit") not in ("oz", "troy_oz")
    ):
        raise ValueError("Unexpected XAU/USD unit")

    price = valid_number(
        data.get("spot_usd_oz"), "spot_usd_oz"
    )

    xau_price = valid_number(
        xau.get("price"), "xau.price"
    )

    if abs(price - xau_price) > Decimal("0.01"):
        raise ValueError("Spot price fields disagree")

    state = data.get("data_state")

    timestamp = (
        state.get("as_of")
        if isinstance(state, dict)
        else None
    ) or data.get("updated_at")

    observed = parse_timestamp(timestamp)

    if observed > datetime.now(timezone.utc):
        raise ValueError("Spot quote time is in the future")

    return price, observed.isoformat()


def main():
    load_env()

    if not os.getenv("NTFY_TOPIC", "").strip():
        print(
            "Missing NTFY_TOPIC GitHub Actions secret",
            file=sys.stderr,
        )
        return 2

    state = read_state()

    phase, reason = market_session(
        datetime.now(timezone.utc)
    )

    previous_phase = state.get("phase")

    # ----------------------------------
    # MARKET CLOSED
    # ----------------------------------

    if phase == "closed":

        if (
            state.get("last_price") is None
            or not state.get("last_quote_at")
        ):
            try:
                price, seen_at = last_reference_quote()

                state["last_price"] = str(price)
                state["last_quote_at"] = seen_at

            except Exception as exc:
                print(
                    f"Market closed; last price unavailable: {exc}",
                    flush=True,
                )

        if (
            previous_phase != "closed"
            or not state.get("closed_notified", False)
        ):
            if (
                state.get("last_price") is not None
                and state.get("last_quote_at")
            ):
                push(
                    f"🔴 ตลาดปิด ({reason}) | "
                    "ราคาอ้างอิงล่าสุด (ไม่ใช่ราคาสด)",
                    state["last_price"],
                    state["last_quote_at"],
                    state.get("last_average"),
                )

                state["closed_notified"] = True

                print(
                    "Market close notification sent: "
                    f"{state['last_price']} USD/oz "
                    f"at {state['last_quote_at']}",
                    flush=True,
                )

            else:
                print(
                    "Market closed; waiting for "
                    "a timestamped reference price",
                    flush=True,
                )

        else:
            print(
                f"Market closed: {reason}; "
                "already notified with last price",
                flush=True,
            )

        state["phase"] = "closed"
        save_state(state)
        return 0

    # ----------------------------------
    # MARKET OPEN
    # ----------------------------------

    spot = read_json(SPOT_URL)
    history = read_json(HISTORY_URL)

    quote, _anchor = calculate_quote(
        spot, history
    )

    print(status_line(quote), flush=True)

    if previous_phase == "closed":
        kind = (
            f"🟢 ตลาดเปิด ({reason}) | "
            "ราคาใหม่ล่าสุด"
        )

    elif quote.price > quote.average:
        kind = f"ราคาอยู่เหนือ SMA{SMA_PERIOD}"

    elif quote.price < quote.average:
        kind = f"ราคาอยู่ใต้ SMA{SMA_PERIOD}"

    else:
        kind = f"ราคาเท่ากับ SMA{SMA_PERIOD}"

    push(
        kind,
        quote.price,
        quote.observed_at.isoformat(),
        quote.average,
    )

    state.update({
        "phase": "open",
        "closed_notified": False,
        "last_price": str(quote.price),
        "last_average": str(quote.average),
        "last_quote_at": quote.observed_at.isoformat(),
    })

    save_state(state)

    print(
        "Notification accepted by ntfy",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
