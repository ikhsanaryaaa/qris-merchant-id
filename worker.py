"""Worker: poll merchant transactions, POST new payments to a webhook.

Env:
  QMID_PROVIDER      "gopay" (default) or "shopee"
  GOPAY_TOKEN        GoID access_token (required for gopay)
  MERCHANT_ID        GoPay merchant id (gopay)
  SHOPEE_TOKEN       B:... token (shopee B1, manual)
  SHOPEE_SESSION     path to session JSON (shopee B2, auto-refresh)
  STORE_ID           Shopee store id (shopee)
  WEBHOOK_URL        where new payments are POSTed (required)
  WEBHOOK_SECRET     HMAC-SHA256 key for signing webhook POSTs
  POLL_INTERVAL      seconds, default 6 (gopay) / 10 (shopee)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

import httpx

from qrismerchantid import GoPayMerchant, ShopeePayPartner
from qrismerchantid.core.exceptions import ApiException, QmidException
from qrismerchantid.shopee import constants as SC

SEEN_PATH = "/data/seen.json"
SEEN_CAP = 2000


def _log(event: str, **kw: Any) -> None:
    print(json.dumps({"ts": int(time.time()), "event": event, **kw}), flush=True)


def _sign(body: str, secret: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


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
        pass


def _load_session(path: str) -> dict[str, Any] | None:
    try:
        return json.loads(open(path).read())
    except (OSError, ValueError):
        return None


def _save_session(path: str, session: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(session, f)
        os.replace(tmp, path)
    except OSError:
        pass


def main() -> None:
    provider = os.environ.get("QMID_PROVIDER", "gopay").lower()
    webhook = os.environ.get("WEBHOOK_URL")
    if not webhook:
        raise SystemExit("WEBHOOK_URL is required")
    interval = float(os.environ.get("POLL_INTERVAL", 6 if provider == "gopay" else 10))
    webhook_secret = os.environ.get("WEBHOOK_SECRET", "")

    sp: ShopeePayPartner | None = None
    session_path: str | None = None
    session: dict[str, Any] | None = None

    if provider == "gopay":
        token = os.environ.get("GOPAY_TOKEN")
        merchant_id = os.environ.get("MERCHANT_ID")
        if not token or not merchant_id:
            raise SystemExit("GOPAY_TOKEN and MERCHANT_ID are required for gopay")
        gopay = GoPayMerchant(access_token=token)
        watcher = gopay.watch(merchant_id, poll_interval=interval)
        label = f"gopay:{merchant_id}"
    elif provider == "shopee":
        store_id = os.environ.get("STORE_ID")
        if not store_id:
            raise SystemExit("STORE_ID is required for shopee")
        session_path = os.environ.get("SHOPEE_SESSION", "/data/shopee-session.json")
        session = _load_session(session_path)
        if session:
            sp = ShopeePayPartner()
            sp.set_token(str(session["token"]))
            if session.get("store_id"):
                store_id = str(session["store_id"])
            watcher = sp.watch(store_id, poll_interval=interval)
            label = f"shopee:{store_id} (B2 auto-refresh)"
        else:
            token = os.environ.get("SHOPEE_TOKEN")
            if not token:
                raise SystemExit("Need SHOPEE_TOKEN (B1) or SHOPEE_SESSION file (B2)")
            sp = ShopeePayPartner(token=token)
            watcher = sp.watch(store_id, poll_interval=interval)
            label = f"shopee:{store_id} (B1 manual)"
    else:
        raise SystemExit(f"Unknown QMID_PROVIDER: {provider}")

    seen = _load_seen()
    _log("start", provider=label, seeded=len(seen), webhook=webhook)

    if not seen:
        _log("seeding", count=watcher.seed())

    with httpx.Client(timeout=10) as hook:
        while True:
            try:
                for tx in watcher.poll_once():
                    tx_id = str(tx.get("transaction_id") or tx.get("id") or tx.get("order_id"))
                    if tx_id in seen:
                        continue
                    seen.add(tx_id)
                    amount = tx.get("gross_amount") or tx.get("amount_idr")
                    if not isinstance(amount, (int, float)):
                        continue
                    payload = json.dumps(
                        {
                            "amount": int(amount),
                            "reference": tx_id,
                            "paidAt": tx.get("transaction_time") or tx.get("create_time"),
                        }
                    )
                    headers = {"content-type": "application/json"}
                    if webhook_secret:
                        headers["x-webhook-signature"] = _sign(payload, webhook_secret)
                    resp = hook.post(webhook, content=payload, headers=headers)
                    _log("payment", tx_id=tx_id, amount=int(amount), status=resp.status_code)
                _save_seen(seen)
            except ApiException as exc:
                if sp is not None and session is not None and _is_dead_token(exc):
                    _log("token_expired", code=str(exc.code), error="refreshing")
                    try:
                        session = sp.auth.refresh_session(session)
                        if session_path:
                            _save_session(session_path, session)
                        _log("token_refreshed")
                    except ApiException as refresh_exc:
                        _log("session_dead", code=str(refresh_exc.code), error=str(refresh_exc))
                        time.sleep(300)
                else:
                    _log("api_error", code=str(exc.code), error=str(exc))
                    time.sleep(30)
            except QmidException as exc:
                _log("error", error=str(exc))
                time.sleep(30)
            except httpx.HTTPError as exc:
                _log("webhook_error", error=str(exc))
                time.sleep(10)
            time.sleep(interval)


def _is_dead_token(exc: ApiException) -> bool:
    return str(exc.code) in SC.INVALID_TOKEN_CODES


if __name__ == "__main__":
    main()
