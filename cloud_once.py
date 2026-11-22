"""One scheduled cloud observation; state restored/saved by GitHub Actions cache."""
import os, sys
from xau_default import HistoryCache, run_once
from gold_alert import AlertService, load_env


def main():
    load_env()
    if not os.environ.get("NTFY_TOPIC", "").strip():
        print("Missing NTFY_TOPIC secret", file=sys.stderr)
        return 2
    service = AlertService()
    # This cloud schedule has gaps. Never invent a crossing from a previous
    # observation if the market may have crossed back in between.
    from xau_default import reset_on_gap, read_json, SPOT_URL, calculate_quote, HISTORY_URL, status_line
    spot = read_json(SPOT_URL)
    history = read_json(HISTORY_URL)
    quote, anchor = calculate_quote(spot, history)
    print(status_line(quote))
    reset_on_gap(service, quote, anchor=anchor)
    service.accept(quote)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
