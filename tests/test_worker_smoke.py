"""Offline smoke test: fake transport + fake webhook, run worker loop 2 rounds.

Run: PYTHONPATH=src python tests/test_worker_smoke.py
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.update(
    QMID_PROVIDER="gopay",
    GOPAY_TOKEN="t",
    MERCHANT_ID="m",
    WEBHOOK_URL="http://127.0.0.1:8977/hook",
    POLL_INTERVAL="0.1",
)

# worker hardcodes /data/seen.json; point it at a temp file for the test
_seen_tmp = os.path.join(os.path.dirname(__file__), "seen_test.json")
if os.path.exists(_seen_tmp):
    os.remove(_seen_tmp)

import worker  # noqa: E402
from qrismerchantid.gopay import GoPayMerchant  # noqa: E402

# worker uses /data/seen.json hard-coded; monkeypatch for test
worker.SEEN_PATH = _seen_tmp

calls = {"n": 0}


class FakeTransport:
    """Two txs on first poll, one new tx on every poll after."""

    def request(self, method, url, body, headers):
        calls["n"] += 1
        txs = (
            [{"transaction_id": "T1", "gross_amount": 100}, {"transaction_id": "T2", "gross_amount": 200}]
            if calls["n"] == 1
            else [{"transaction_id": "T3", "gross_amount": 300}]
        )
        return 200, json.dumps({"transactions": txs})


received: list[dict] = []


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        received.append(json.loads(self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def main() -> None:
    server = HTTPServer(("127.0.0.1", 8977), Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # build watcher with fake transport (mirrors worker.main gopay branch)
    gopay = GoPayMerchant(access_token="t", transport=FakeTransport())
    watcher = gopay.watch("m", poll_interval=0.1)
    seen: set[str] = set()

    import httpx

    with httpx.Client(timeout=5) as hook:
        for _ in range(3):  # 3 polls
            for tx in watcher.poll_once():
                tx_id = str(tx.get("transaction_id") or tx.get("id") or tx.get("order_id"))
                if tx_id in seen:
                    continue
                seen.add(tx_id)
                hook.post(os.environ["WEBHOOK_URL"], json=tx)

    server.shutdown()
    assert received == [
        {"transaction_id": "T1", "gross_amount": 100},
        {"transaction_id": "T2", "gross_amount": 200},
        {"transaction_id": "T3", "gross_amount": 300},
    ], received
    print("SMOKE OK — 3 payments forwarded to webhook in order")


if __name__ == "__main__":
    main()
