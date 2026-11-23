#!/usr/bin/env python3
"""One GitHub Actions check: fetch XAU/USD and SMA5, then send one ntfy push."""
import os
import sys

from gold_alert import load_env, send_push
from xau_default import HISTORY_URL, SPOT_URL, SMA_PERIOD, calculate_quote, read_json, status_line


def main() -> int:
    load_env()
    if not os.getenv("NTFY_TOPIC", "").strip():
        print("Missing NTFY_TOPIC GitHub Actions secret", file=sys.stderr)
        return 2

    spot = read_json(SPOT_URL)
    history = read_json(HISTORY_URL)
    quote, _anchor = calculate_quote(spot, history)
    print(status_line(quote), flush=True)

    if quote.price > quote.average:
        status = f"ราคาอยู่เหนือ SMA{SMA_PERIOD}"
    elif quote.price < quote.average:
        status = f"ราคาอยู่ใต้ SMA{SMA_PERIOD}"
    else:
        status = f"ราคาเท่ากับ SMA{SMA_PERIOD}"

    event = {
        "kind": status,
        "symbol": f"XAU/USD · SMA{SMA_PERIOD} (1D)",
        "price": f"{quote.price:.2f}",
        "average": f"{quote.average:.2f}",
        "unit": quote.unit,
        "at": quote.observed_at.isoformat(),
        "source": quote.source,
    }
    send_push(event)
    print(f"Notification accepted by ntfy: XAU/USD SMA{SMA_PERIOD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
