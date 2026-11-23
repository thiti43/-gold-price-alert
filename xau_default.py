    difference = q.price - q.average
    sign = "เหนือ" if difference > 0 else "ใต้" if difference < 0 else "เท่ากับ"
    return (f"XAU/USD {fmt(q.price)} USD/oz  |  SMA{SMA_PERIOD} {fmt(q.average)} USD/oz  "
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
    parser = argparse.ArgumentParser(description=f"XAU/USD 1D SMA{SMA_PERIOD} -> ntfy push (NOT broker-specific)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Generate secret ntfy topic")
    sub.add_parser("test-push", help="Send notification test")
    sub.add_parser("check", help=f"Check spot/SMA{SMA_PERIOD} without push")
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
            print(f"Monitoring reference XAU/USD (not GOLD NOW). 1D/SMA{SMA_PERIOD}, Ctrl+C to stop.")
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


def market_session(now: datetime | None = None) -> tuple[str, str]:
    """Reference XAU/USD session schedule, not a live exchange status feed."""
    now = now or utc_now()
    ny = now.astimezone(ZoneInfo("America/New_York"))
    weekday = ny.weekday()
    minute = ny.hour * 60 + ny.minute
    if (weekday == 5 or
        (weekday == 6 and minute < 18 * 60) or
        (weekday == 4 and minute >= 17 * 60)):
        return "closed", "สุดสัปดาห์"
    if 17 * 60 <= minute < 18 * 60:
        return "closed", "พักตลาดรายวัน"
    return "open", "ช่วงเวลาซื้อขายอ้างอิง"


if __name__ == "__main__":
    raise SystemExit(main())
