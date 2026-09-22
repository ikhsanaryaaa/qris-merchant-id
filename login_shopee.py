"""Login ShopeePay via OTP. Reads device report from device-report.txt.
Usage: set SHOPEE_PHONE=08xxx && set SHOPEE_PASSWORD=*** && python login_shopee.py
"""
import getpass
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
sys.path.insert(0, os.path.dirname(__file__))

from qrismerchantid import ShopeePayPartner

SESSION_FILE = ".shopee-session.json"

phone = os.environ.get("SHOPEE_PHONE")
if not phone:
    raise SystemExit("Set SHOPEE_PHONE first, e.g: set SHOPEE_PHONE=0812xxxxxxx")

password = os.environ.get("SHOPEE_PASSWORD")
if password is None:
    password = getpass.getpass("ShopeePay password (empty if none): ") or None

# Read device report from file
report_path = os.path.join(os.path.dirname(__file__), "device-report.txt")
try:
    with open(report_path, "r") as f:
        device_report = f.read().strip()
except FileNotFoundError:
    raise SystemExit(f"device-report.txt not found at {report_path}")

print(f"Device report loaded: {len(device_report)} chars")
print(f"Phone: {phone}")
print(f"Password: {'yes' if password else 'no'}")

sp = ShopeePayPartner(device_report=device_report)
print("Requesting OTP (channel 3 = WhatsApp)...")
challenge = sp.auth.request_otp(phone, password=password, channel=3)
print(f"OTP sent via channel {challenge['channel']} — check WhatsApp/SMS.")

otp = getpass.getpass("OTP code: ")
outcome = sp.auth.login_with_otp(challenge, otp, merchant_id=os.environ.get("SHOPEE_MERCHANT_ID"))
if outcome["status"] == "merchant-selection-required":
    for m in outcome["merchants"]:
        print(f"  {m['id']}  {m['name']}")
    outcome = {
        "status": "complete",
        "session": sp.auth.complete_login(
            outcome["verification"], merchant_id=input("Merchant ID: ")
        ),
    }

session = outcome["session"]
with open(SESSION_FILE, "w") as f:
    json.dump(session, f)
print(f"Logged in as {session['merchant']['name']} — session saved to {SESSION_FILE}")
print(f"Stores: {', '.join(s['name'] for s in session['stores'])}")
