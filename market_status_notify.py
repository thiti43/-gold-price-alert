"""Notify XAU/USD reference market open/close with last price; no SMA change."""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from gold_alert import load_env, parse_timestamp
from xau_default import SPOT_URL, read_json, spot_from_data

STATE = Path(__file__).resolve().parent / '.market_session_state.json'
NY = ZoneInfo('America/New_York')
BKK = ZoneInfo('Asia/Bangkok')


def phase_at(now):
    n = now.astimezone(NY)
    d, m = n.weekday(), n.hour * 60 + n.minute
    if d == 5 or (d == 6 and m < 1080) or (d == 4 and m >= 1020):
        return 'weekend_closed'
    if d in (0, 1, 2, 3) and 1020 <= m < 1080:
        return 'daily_break'
    return 'open'


def thai_time(t):
    return t.astimezone(BKK).strftime('%d/%m/%Y %H:%M:%S น.')


def load():
    try:
        s = json.loads(STATE.read_text(encoding='utf-8'))
        return s if s.get('version') == 1 and s.get('feed') == 'XAUS.com' else {}
    except (FileNotFoundError, ValueError, TypeError, OSError):
        return {}


def quote_from_saved(s):
    try:
        p, at = Decimal(s['price']), parse_timestamp(s['at'])
        if p.is_finite() and p > 0: return p, at
    except (ValueError, KeyError, TypeError): pass
    return None


def quote_from_feed(d):
    x, state = d['xau'], d['data_state']
    if x['currency'] != 'USD' or x['unit'] not in ('oz', 'troy_oz'):
        raise ValueError('Expected USD/oz')
    p = Decimal(str(d['spot_usd_oz']))
    if not p.is_finite() or p <= 0 or abs(p-Decimal(str(x['price']))) > Decimal('0.01'):
        raise ValueError('Invalid price')
    at = parse_timestamp(state.get('as_of') or d['updated_at'])
    if at > datetime.now(timezone.utc) + timedelta(seconds=60):
        raise ValueError('Future quote timestamp')
    return p, at


def save(status, quote):
    p, at = quote
    s = {'version': 1, 'feed': 'XAUS.com', 'phase': status, 'price': str(p), 'at': at.isoformat()}
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s), encoding='utf-8')
    tmp.replace(STATE)


def push(status, quote, now, initial=False):
    topic = os.getenv('NTFY_TOPIC', '').strip()
    if not topic or not all(c.isascii() and (c.isalnum() or c in '-_') for c in topic):
        raise ValueError('Missing/invalid NTFY_TOPIC')
    titles = {'open': '🟢 ตลาดเปิดตามตารางอ้างอิง XAU/USD',
              'weekend_closed': '🔴 ตลาดปิดสุดสัปดาห์ตามตารางอ้างอิง',
              'daily_break': '🟠 ตลาดพักรายวันตามตารางอ้างอิง'}
    p, at = quote
    title = ('สถานะเริ่มต้น: ' if initial else '') + titles[status]
    label = ('ราคาใหม่หลังเวลาเปิด' if status == 'open'
             else 'ราคาล่าสุดที่มี (ไม่ใช่ราคาปิด ณ วินาทีนี้)')
    body = (f'{title}\nXAU/USD: {p:,.2f} USD/oz\n'
            f'เวลาอ้างอิงราคา: {thai_time(at)}\n'
            f'เวลาตรวจสถานะ: {thai_time(now)}\n{label}\n'
            'แหล่งราคา: XAUS.com\nสถานะตามเวลาซื้อขายอ้างอิงนิวยอร์ก ไม่ใช่สถานะจริงจาก API')
    req = Request('https://ntfy.sh/'+topic, data=body.encode('utf-8'), method='POST',
                  headers={'Title': 'XAUUSD Market Status', 'Content-Type': 'text/plain; charset=utf-8'})
    with urlopen(req, timeout=12) as response:
        if response.status // 100 != 2: raise RuntimeError('ntfy HTTP error')


def main(now=None):
    load_env()
    now = now or datetime.now(timezone.utc)
    status, state = phase_at(now), load()
    previous = state.get('phase')
    saved = quote_from_saved(state)
    feed_data = None
    try:
        feed_data = read_json(SPOT_URL)
        fetched = quote_from_feed(feed_data)
    except Exception as exc:
        print('Market price feed unavailable:', exc, flush=True)
        fetched = None
    quote = max([q for q in (saved, fetched) if q is not None], key=lambda q:q[1], default=None)
    print(f'Market: {status}, previous: {previous}, quote: {quote}', flush=True)
    if quote is None: return 0  # Never send a market alert without a price.

    if status == 'open' and previous != 'open':
        if feed_data is None: return 0
        try:
            price, at = spot_from_data(feed_data, now=now)  # Requires fresh quote.
            boundary = now.astimezone(NY).replace(hour=18,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
            if at < boundary or (saved is not None and at <= saved[1]):
                print('Waiting for a newly updated opening price', flush=True)
                return 0
            quote = price, at
        except ValueError as exc:
            print('Waiting for fresh market-open quote:', exc, flush=True)
            return 0

    if status != previous:
        push(status, quote, now, initial=previous is None)
        print('Market open/close push sent', flush=True)
    save(status, quote)
    return 0

if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as exc:
        print('Market-status check failed:', exc, file=sys.stderr)
        sys.exit(1)
