#!/usr/bin/env python3
"""Gold price / average crossing alerts to iPhone via ntfy.

Python 3.10+. Standard library only. Not affiliated with GOLD NOW/Hua Seng Heng.
Requires an authorized feed for REAL GOLD NOW notifications.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
LOG = logging.getLogger("gold-alert")


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip('"').strip("'")


def get_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"'{field}' ต้องเป็นตัวเลข")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError()
        return number
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"'{field}' ต้องเป็นตัวเลขบวกที่ถูกต้อง") from exc


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("ต้องระบุ timestamp เป็น ISO 8601 พร้อมเขตเวลา")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp ผิดรูปแบบ ISO 8601") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp ต้องมีเขตเวลา เช่น +07:00 หรือ Z")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class Quote:
    price: Decimal
    average: Decimal
    observed_at: datetime
    symbol: str
    unit: str
    source: str

    @classmethod
    def from_json(cls, body: object) -> "Quote":
        if not isinstance(body, dict):
            raise ValueError("ข้อมูลราคาต้องเป็น JSON object")
        price = body.get("realtime", body.get("price"))
        return cls(
            price=get_decimal(price, "realtime"),
            average=get_decimal(body.get("average"), "average"),
            observed_at=parse_timestamp(body.get("timestamp")),
            symbol=str(body.get("symbol", "ทองคำ")).strip()[:90] or "ทองคำ",
            unit=str(body.get("unit", "บาท")).strip()[:20] or "บาท",
            source=str(body.get("source", "ฟีดที่เชื่อมต่อ")).strip()[:90],
        )


def fmt(value: Decimal) -> str:
    return f"{value:,.2f}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CrossingDetector:
    """One alert at equality or crossing; does not double-alert when leaving equality."""

    def __init__(self, tolerance: Decimal = Decimal("0"), cooldown: int = 60,
                 max_quote_age: int = 120, state: dict | None = None):
        if tolerance < 0:
            raise ValueError("PRICE_TOLERANCE ต้อง >= 0")
        self.tolerance = tolerance
        self.cooldown = cooldown
        self.max_quote_age = max_quote_age
        self.state = state if state is not None else {}

    def evaluate(self, quote: Quote, *, check_freshness: bool = True) -> dict | None:
        if check_freshness:
            delta = (utc_now() - quote.observed_at).total_seconds()
            if delta > self.max_quote_age:
                raise ValueError(f"ข้อมูลราคาเก่าเกินกำหนด ({delta:.0f} วินาที)")
            if delta < -60:
                raise ValueError("timestamp ข้อมูลราคาอยู่ในอนาคตเกิน 60 วินาที")

        current_ts = quote.observed_at.isoformat()
        previous_ts = self.state.get("last_seen_at")
        if previous_ts is not None and quote.observed_at <= parse_timestamp(previous_ts):
            return None  # same or older quote, not a new observation

        series_id = f"{quote.symbol}\x1f{quote.unit}\x1f{quote.source}"
        if self.state.get("series_id") not in (None, series_id):
            # Never compare two different instruments/data feeds.
            for name in ("previous_side", "last_nonzero_side", "zone_alerted", "last_alert_at"):
                self.state.pop(name, None)
        self.state["series_id"] = series_id

        difference = quote.price - quote.average
        side = 0 if abs(difference) <= self.tolerance else (1 if difference > 0 else -1)
        previous_side = self.state.get("previous_side")
        last_nonzero_side = self.state.get("last_nonzero_side")
        zone_alerted = self.state.get("zone_alerted", False)
        kind = None
        if side == 0:
            if previous_side is not None and previous_side != 0 and not zone_alerted:
                kind = "เท่ากัน / แตะเส้นค่าเฉลี่ย"
                self.state["zone_alerted"] = True
        else:
            if last_nonzero_side is not None and side != last_nonzero_side and not zone_alerted:
                kind = "ราคาตัดขึ้นเหนือค่าเฉลี่ย" if side > 0 else "ราคาตัดลงต่ำกว่าค่าเฉลี่ย"
            self.state["last_nonzero_side"] = side
            self.state["zone_alerted"] = False

        self.state["previous_side"] = side
        self.state["last_seen_at"] = current_ts
        if kind is None:
            return None

        previous_alert_at = self.state.get("last_alert_at")
        if previous_alert_at:
            elapsed = (quote.observed_at - parse_timestamp(previous_alert_at)).total_seconds()
            if elapsed < self.cooldown:
                LOG.info("ตัดผ่านแล้ว แต่ยังอยู่ในช่วงพักแจ้งเตือน (%ds)", self.cooldown)
                return None
        self.state["last_alert_at"] = current_ts
        return {
            "at": current_ts,
            "kind": kind,
            "price": fmt(quote.price),
            "average": fmt(quote.average),
            "unit": quote.unit,
            "symbol": quote.symbol,
            "source": quote.source,
        }


def send_push(event: dict | None = None, *, is_test: bool = False) -> None:
    topic = os.getenv("NTFY_TOPIC", "").strip()
    base_url = os.getenv("NTFY_BASE_URL", "https://ntfy.sh").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
        raise ValueError("NTFY_BASE_URL ต้องเป็น HTTPS host เช่น https://ntfy.sh")
    if not topic or not all(x.isascii() and (x.isalnum() or x in "-_" ) for x in topic):
        raise ValueError("ยังไม่ได้ตั้งค่า NTFY_TOPIC; ให้รัน python gold_alert.py setup ก่อน")
    if is_test:
        msg = ("ทดสอบแจ้งเตือนสำเร็จจากโปรแกรม Gold Crossing Alert!\n"
               "นี่เป็นข้อความทดสอบ ไม่ใช่สัญญาณราคาทองจริง")
    else:
        assert event is not None
        msg = (
            f"{event['kind']}\n"
            f"{event['symbol']}\n"
            f"Real-Time: {event['price']} {event['unit']}\n"
            f"ค่าเฉลี่ย: {event['average']} {event['unit']}\n"
            f"เวลาอ้างอิง: {event['at']}\n"
            f"ที่มา: {event['source']}"
        )
    request = Request(
        url=f"{base_url}/{topic}", data=msg.encode("utf-8"), method="POST",
        headers={"Title": "Gold Crossing Alert", "Priority": "high",
                 "Tags": "moneybag,chart_with_upwards_trend", "Content-Type": "text/plain; charset=utf-8"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            if not (200 <= response.status < 300):
                raise RuntimeError(f"ntfy HTTP {response.status}")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"ส่งแจ้งเตือนไม่สำเร็จ: {exc}") from exc


class AlertService:
    def __init__(self, *, persistent: bool = True):
        self.lock = threading.Lock()
        self.persistent = persistent
        self.state_path = BASE_DIR / "state.json"
        if persistent and self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            state = {}
        self.state = state
        tolerance = Decimal(os.getenv("PRICE_TOLERANCE", "0"))
        self.detector = CrossingDetector(
            tolerance=tolerance,
            cooldown=int(os.getenv("ALERT_COOLDOWN_SECONDS", "60")),
            max_quote_age=int(os.getenv("MAX_QUOTE_AGE_SECONDS", "120")),
            state=self.state.setdefault("detector", {}),
        )
        self.state.setdefault("pending", [])

    def save(self) -> None:
        if not self.persistent:
            return
        temp_path = self.state_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.state_path)

    def flush_pending(self) -> int:
        sent = 0
        # Financial notifications should not suddenly arrive hours late.
        max_delay = int(os.getenv("MAX_PUSH_AGE_SECONDS", "180"))
        while self.state["pending"]:
            event = self.state["pending"][0]
            age = (utc_now() - parse_timestamp(event["at"])).total_seconds()
            if age > max_delay:
                LOG.warning("ข้ามการส่งแจ้งเตือนที่เก่าเกิน %d วินาที", max_delay)
                self.state["pending"].pop(0)
                self.save()
                continue
            try:
                send_push(event)
            except (ValueError, RuntimeError) as exc:
                LOG.error("แจ้งเตือนค้างส่ง: %s", exc)
                break
            self.state["pending"].pop(0)
            sent += 1
            LOG.info("ส่งแจ้งเตือนสำเร็จ: %s", event["kind"])
            self.save()
        return sent

    def accept(self, quote: Quote) -> dict:
        with self.lock:
            event = self.detector.evaluate(quote)
            if event:
                self.state["pending"].append(event)
                LOG.warning("พบเงื่อนไข: %s | %s / %s %s", event["kind"],
                            event["price"], event["average"], event["unit"])
            self.save()
            sent = self.flush_pending()
            return {"event": event, "sent": sent, "pending": len(self.state["pending"])}


def setup() -> None:
    if ENV_PATH.exists():
        print("มีไฟล์ .env อยู่แล้ว ไม่เขียนทับเพื่อรักษาการตั้งค่าเดิม")
        load_env()
    else:
        topic = "gold-crossing-" + secrets.token_hex(18)
        feed_token = secrets.token_urlsafe(32)
        lines = (
            "# Generated locally. Do not share this file or topic/token publicly.\n"
            f"NTFY_BASE_URL=https://ntfy.sh\nNTFY_TOPIC={topic}\n"
            f"FEED_TOKEN={feed_token}\n"
            "XAU_POLL_SECONDS=60\n"
            "PRICE_TOLERANCE=0\nALERT_COOLDOWN_SECONDS=60\n"
            "MAX_QUOTE_AGE_SECONDS=300\nMAX_PUSH_AGE_SECONDS=180\n"
            "HOST=127.0.0.1\nPORT=8765\n"
        )
        ENV_PATH.write_text(lines, encoding="utf-8")
        try:
            ENV_PATH.chmod(0o600)
        except OSError:
            pass
        load_env()
        print("สร้าง .env เรียบร้อย")
    print("\nขั้นตอนที่ iPhone หรือ Android:")
    print("1. ติดตั้ง ntfy จาก App Store / Google Play และอนุญาต Notifications")
    print("2. ใน ntfy ให้กด Subscribe / + และใช้เซิร์ฟเวอร์ https://ntfy.sh")
    print("3. วางชื่อ topic นี้ให้ตรงกันทุกตัว:")
    print("\n   " + os.getenv("NTFY_TOPIC", "(ไม่พบ topic)"))
    print("\n4. ทดสอบด้วย: python gold_alert.py test-push")
    print("หมายเหตุ: topic เป็นกุญแจเข้าถึงข้อความ ควรเก็บไว้เป็นความลับ")


def demo(push: bool) -> None:
    """A deterministic data sequence demonstrating equality and crosses. Never live data."""
    now = utc_now()
    samples = [(65000, 65015), (65006, 65012), (65012, 65012),
               (65018, 65012), (65016, 65014), (65008, 65014),
               (65007, 65014), (65010, 65010), (65013, 65009)]
    detector = CrossingDetector(cooldown=0, max_quote_age=86400)
    print("จำลองข้อมูลเท่านั้น — ไม่ใช่ราคา GOLD NOW จริง")
    count = 0
    for i, (price, avg) in enumerate(samples):
        quote = Quote(Decimal(price), Decimal(avg), now + timedelta(seconds=i),
                      "ทองคำจำลอง", "บาท", "DEMO (ไม่ใช่ GOLD NOW)")
        event = detector.evaluate(quote, check_freshness=False)
        print(f"[{i+1}] Real-Time {price:,} | Average {avg:,}", end="")
        if event:
            count += 1
            print(f"  --> {event['kind']}")
            if push:
                send_push(event)
                print("    ส่งคำขอแจ้งเตือนไป ntfy แล้ว (การถึง iPhone ขึ้นกับเครือข่าย/การตั้งค่า)")
        else:
            print()
    print(f"เหตุการณ์ที่ตรวจพบ: {count} ครั้ง")
    if not push:
        print("ไม่มีการส่ง Push ในโหมดนี้ หากต้องการทดสอบจริงใช้: python gold_alert.py demo --push")


def validate_source_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1"):
        return
    raise ValueError("PRICE_API_URL ต้องเป็น HTTPS หรือ HTTP localhost เท่านั้น")


def fetch_quote(url: str) -> Quote:
    validate_source_url(url)
    headers = {"Accept": "application/json", "User-Agent": "GoldCrossingAlert/1.0"}
    token = os.getenv("PRICE_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=12) as response:
        if response.status != 200:
            raise RuntimeError(f"Price feed returned HTTP {response.status}")
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("JSON จากแหล่งราคามีขนาดใหญ่เกิน 64 KB")
    return Quote.from_json(json.loads(raw.decode("utf-8")))


def run_poll() -> None:
    url = os.getenv("PRICE_API_URL", "").strip()
    if not url:
        raise ValueError(
            "ยังไม่ได้ตั้ง PRICE_API_URL: ตอนนี้เชื่อมต่อ GOLD NOW จริงไม่ได้ "
            "ให้ทดลอง 'demo' หรือ 'test-push' ก่อน และรอแหล่งข้อมูลที่ได้รับอนุญาต")
    validate_source_url(url)
    poll = float(os.getenv("POLL_INTERVAL_SECONDS", "10"))
    if poll < 1:
        raise ValueError("POLL_INTERVAL_SECONDS ต้อง >= 1")
    service = AlertService()
    print("เริ่มตรวจสอบฟีดที่ตั้งค่าไว้ (Ctrl+C เพื่อหยุด)")
    while True:
        try:
            quote = fetch_quote(url)
            result = service.accept(quote)
            LOG.info("ราคา=%s เฉลี่ย=%s | push=%s | pending=%s", fmt(quote.price),
                     fmt(quote.average), result["sent"], result["pending"])
        except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
            LOG.error("อ่านราคา/ส่งแจ้งเตือนไม่สำเร็จ: %s", exc)
        time.sleep(poll)


def run_server() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    expected_token = os.getenv("FEED_TOKEN", "")
    if not expected_token:
        raise ValueError("ต้องรัน setup ก่อนเพื่อสร้าง FEED_TOKEN")
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("สำหรับความปลอดภัย ตัวอย่างนี้อนุญาตให้รับข้อมูลผ่าน localhost เท่านั้น")
    service = AlertService()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self.reply(200, {"status": "running", "mode": "local feed", "is_gold_now_connected": False})
            else:
                self.reply(404, {"error": "Not found"})

        def do_POST(self) -> None:
            if self.path != "/quote":
                return self.reply(404, {"error": "Not found"})
            if not secrets.compare_digest(self.headers.get("X-Feed-Token", ""), expected_token):
                return self.reply(401, {"error": "Unauthorized"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 65536:
                    return self.reply(413, {"error": "Invalid payload length"})
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                quote = Quote.from_json(body)
                result = service.accept(quote)
                self.reply(200, {"ok": True, "alert_created": result["event"] is not None,
                                 "push_request_sent": result["sent"], "pending": result["pending"]})
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.reply(400, {"error": str(exc)})

    with ThreadingHTTPServer((host, port), Handler) as server:
        print(f"Local feed server: http://{host}:{port}/quote")
        print("ต้องส่ง POST JSON + Header X-Feed-Token จากระบบราคาที่ได้รับอนุญาต")
        print("เซิร์ฟเวอร์นี้ยังไม่ได้เชื่อมราคา GOLD NOW โดยตรง")
        server.serve_forever()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(description="Gold Crossing Alert -> ntfy iPhone Push")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="สร้าง .env และ ntfy topic แบบสุ่ม")
    sub.add_parser("test-push", help="ส่งข้อความทดสอบเข้า iPhone (ต้องตั้ง ntfy ก่อน)")
    demo_parser = sub.add_parser("demo", help="สาธิตจุดตัดจากราคา *จำลอง*")
    demo_parser.add_argument("--push", action="store_true", help="ส่งการแจ้งเตือนจำลองจริง")
    sub.add_parser("run", help="อ่านราคา JSON จาก PRICE_API_URL ที่ได้รับอนุญาต")
    sub.add_parser("serve", help="รับราคา JSON ผ่าน POST localhost/quote")
    args = parser.parse_args()
    load_env()
    try:
        if args.command == "setup":
            setup()
        elif args.command == "test-push":
            send_push(is_test=True)
            print("ntfy ยอมรับคำขอแล้ว — ตรวจสอบการแจ้งเตือนบน iPhone")
        elif args.command == "demo":
            demo(args.push)
        elif args.command == "run":
            run_poll()
        elif args.command == "serve":
            run_server()
    except KeyboardInterrupt:
        print("\nหยุดโปรแกรมแล้ว")
    except (OSError, ValueError, RuntimeError, InvalidOperation) as exc:
        print(f"ข้อผิดพลาด: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
