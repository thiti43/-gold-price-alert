#!/usr/bin/env python3
"""XAU/USD SMA2 trial alerts: 60s spot checks, cached daily history.

Trial is NOT verified to match the green indicator on GOLD NOW / TradingView.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError

from gold_alert import load_env, parse_timestamp, send_push
from xau_default import (
    HISTORY_URL, SPOT_URL, SMA_PERIOD, calculate_quote,
    market_session, read_json, status_line, valid_number,
)

STATE_PATH = Path('.gold_market_state.json')
CHECK_INTERVAL = 60
WATCH_SECONDS = 300
HISTORY_REFRESH_SECONDS = 6 * 3600
DEFAULT_BACKOFF_SECONDS = 120
MAX_BACKOFF_SECONDS = 3600


def read_state():
    try:
        data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    temp = STATE_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(STATE_PATH)


def notify(kind, state):
    price = state.get('last_price')
    average = state.get('last_average')
    send_push({
        'kind': kind,
        'symbol': f'XAU/USD · SMA{SMA_PERIOD} (1D)',
        'price': f'{Decimal(str(price)):.2f}' if price is not None else 'ยังไม่มีข้อมูล',
        'average': f'{Decimal(str(average)):.2f}' if average is not None else 'ยังไม่มีข้อมูล',
        'unit': 'USD/oz',
        'at': state.get('last_quote_at') or datetime.now(timezone.utc).isoformat(),
        'source': 'XAUS.com indicative reference (SMA2 TRIAL; not ICE/TradingView)',
    })


def retry_after_seconds(exc):
    header = exc.headers.get('Retry-After') if exc.headers else None
    if header:
        try:
            return max(1, min(MAX_BACKOFF_SECONDS, int(header)))
        except (ValueError, TypeError):
            try:
                when = parsedate_to_datetime(header)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                seconds = (when - datetime.now(timezone.utc)).total_seconds()
                return max(1, min(MAX_BACKOFF_SECONDS, int(seconds) + 1))
            except (ValueError, TypeError, OverflowError):
                pass
    return DEFAULT_BACKOFF_SECONDS


def fetch_api(url, state):
    now = time.time()
    until = float(state.get('api_pause_until', 0))
    if now < until:
        raise RuntimeError(f'API cooldown: {int(until - now) + 1}s remaining')
    try:
        result = read_json(url)
        if state.get('api_429_count') or state.get('api_pause_until'):
            state.pop('api_429_count', None)
            state.pop('api_pause_until', None)
            save_state(state)
        return result
    except HTTPError as exc:
        if exc.code == 429:
            count = min(int(state.get('api_429_count', 0)) + 1, 6)
            wait = max(retry_after_seconds(exc), min(MAX_BACKOFF_SECONDS, 60 * 2 ** (count - 1)))
            state['api_429_count'] = count
            state['api_pause_until'] = time.time() + wait
            save_state(state)
            print(f'HTTP 429: pausing API calls for {wait}s', flush=True)
        raise
