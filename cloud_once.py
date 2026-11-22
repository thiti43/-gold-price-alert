#!/usr/bin/env python3
"""One XAU/USD check with market open/close alerts and SMA5 notifications."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from gold_alert import load_env, send_push
from xau_default import (
    HISTORY_URL, SPOT_URL, SMA_PERIOD, calculate_quote, read_json,
    status_line, market_session, spot_from_data,
)

STATE_PATH = Path(os.getenv('GOLD_STATE_FILE', '.gold_market_state.json'))


def read_state():
    try:
        data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_name(STATE_PATH.name + '.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(STATE_PATH)


def push(kind, price, seen_at, average=None):
    event = {
        'kind': kind,
        'symbol': f'XAU/USD · SMA{SMA_PERIOD} (1D)',
        'price': f'{price:.2f}',
        'average': f'{average:.2f}' if average is not None else 'ไม่มีข้อมูล SMA สด',
        'unit': 'USD/oz',
        'at': seen_at,
        'source': 'XAUS.com indicative XAU/USD',
    }
    send_push(event)


def main() -> int:
    load_env()
    if not os.getenv('NTFY_TOPIC', '').strip():
        print('Missing NTFY_TOPIC GitHub Actions secret', file=sys.stderr)
        return 2

    state = read_state()
    now = datetime.now(timezone.utc)
    phase, description = market_session(now)
    old_phase = state.get('phase')

    if phase == 'closed':
        # At close, prefer last successfully observed price. A market-close
        # notification is not a claim of a live quote at the exact close.
        if old_phase == 'open' and state.get('last_price') is not None:
            push(f'🔴 ตลาดปิดตามตาราง ({description}) | ราคาล่าสุดก่อนปิด',
                 float(state['last_price']), state['last_quote_at'],
                 float(state['last_average']) if state.get('last_average') else None)
            print('Market close notification sent', flush=True)
        else:
            print(f'Market closed: {description}; no duplicate alert', flush=True)
        state['phase'] = 'closed'
        save_state(state)
        return 0

    # During scheduled open hours, require a fresh quote before announcing open.
    spot = read_json(SPOT_URL)
    history = read_json(HISTORY_URL)
    quote, _anchor = calculate_quote(spot, history)
    print(status_line(quote), flush=True)

    if old_phase == 'closed':
        kind = f'🟢 ตลาดเปิดตามตาราง ({description}) | ได้รับราคาใหม่แล้ว'
    elif quote.price > quote.average:
        kind = f'ราคาอยู่เหนือ SMA{SMA_PERIOD}'
    elif quote.price < quote.average:
        kind = f'ราคาอยู่ใต้ SMA{SMA_PERIOD}'
    else:
        kind = f'ราคาเท่ากับ SMA{SMA_PERIOD}'

    push(kind, float(quote.price), quote.observed_at.isoformat(), float(quote.average))
    print('Notification accepted by ntfy', flush=True)
    state.update({
        'phase': 'open',
        'last_price': str(quote.price),
        'last_average': str(quote.average),
        'last_quote_at': quote.observed_at.isoformat(),
    })
    save_state(state)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
