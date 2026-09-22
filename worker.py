"""Worker: poll merchant transactions, POST new payments to a webhook.

Env:
  QMID_PROVIDER      "gopay" (default) or "shopee"
  GOPAY_TOKEN        GoID access_token (required for gopay)
  MERCHANT_ID        GoPay merchant id (gopay)
  SHOPEE_TOKEN       B:... token (shopee)
  STORE_ID           Shopee store id (shopee)
  WEBHOOK_URL        where new payments are POSTed (required)
  POLL_INTERVAL      seconds, default 6 (gopay) / 10 (shopee)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from qrismerchantid import GoPayMerchant, ShopeePayPartner
from qrismerchantid.core.exceptions import QmidException

SEEN_PATH = "/data/seen.json"
SEEN_CAP = 2000


def _log(event: str, **kw: Any) -> None:
    print(json.dumps({"ts": int(time.time()), "event": event, **kw}), flush=True)


def _load_seen() -> set[str]:
    try:
        return set(json.loads(open(SEEN_PATH).read()))
    except (OSError, ValueError):
        return set()


def _save_seen(seen: set[str]) -> None:
    try:
        os.makedirs(os.path.dirname(SEEN_PATH), exist_ok=True)
        tmp = SEEN_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write(json.dumps(list(seen)[-SEEN_CAP:]))
        os.replace(tmp, SEEN_PATH)
    except OSError:
        # unwritable volume: degrade gracefully — next restart re-seeds instead of flooding
        pass


def main() -> None:
    provider = os.environ.get("QMID_PROVIDER", "gopay").lower()
    webhook = os.environ.get("WEBHOOK_URL")
    if not webhook:
        raise SystemExit("WEBHOOK_URL is required")
    interval = float(os.environ.get("POLL_INTERVAL", 6 if provider == "gopay" else 10))

    if provider == "gopay":
        token = os.environ.get("GOPAY_TOKEN")
        merchant_id = os.environ.get("MERCHANT_ID")
        if not token or not merchant_id:
            raise SystemExit("GOPAY_TOKEN and MERCHANT_ID are required for gopay")
        gopay = GoPayMerchant(access_token=token)
        watcher = gopay.watch(merchant_id, poll_interval=interval)
        label = f"gopay:{merchant_id}"
    elif provider == "shopee":
        token = os.environ.get("SHOPEE_TOKEN")
        store_id = os.environ.get("STORE_ID")
        if not token or not store_id:
            raise SystemExit("SHOPEE_TOKEN and STORE_ID are required for shopee")
        sp = ShopeePayPartner(token=token)
        watcher = sp.watch(store_id, poll_interval=interval)
        label = f"shopee:{store_id}"
    else:
        raise SystemExit(f"Unknown QMID_PROVIDER: {provider}")

    seen = _load_seen()
    _log("start", provider=label, seeded=len(seen), webhook=webhook)

    if not seen:
        # first run: don't flood the webhook with historical txs
        _log("seeding", count=watcher.seed())

    with httpx.Client(timeout=10) as hook:
        while True:
            try:
                for tx in watcher.poll_once():
                    tx_id = str(tx.get("transaction_id") or tx.get("id") or tx.get("order_id"))
                    if tx_id in seen:
                        continue
                    seen.add(tx_id)
                    resp = hook.post(webhook, json=tx)
                    _log("payment", tx_id=tx_id, status=resp.status_code)
                _save_seen(seen)
            except QmidException as exc:
                _log("api_error", error=str(exc))
                time.sleep(30)
            except httpx.HTTPError as exc:
                _log("webhook_error", error=str(exc))
                time.sleep(10)
            time.sleep(interval)


if __name__ == "__main__":
    main()
