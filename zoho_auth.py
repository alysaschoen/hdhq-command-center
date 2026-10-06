#!/usr/bin/env python3
"""One-time Zoho OAuth bootstrap.

Reads ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_GRANT_CODE from .env, swaps the
(10-minute) grant code for a permanent refresh token, writes ZOHO_REFRESH_TOKEN
back into .env and blanks ZOHO_GRANT_CODE.

    python zoho_auth.py
"""

import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_PATH = Path(__file__).with_name(".env")


def _set_env_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    line = f"{key}={value}"
    return pattern.sub(line, text) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"


def main() -> int:
    load_dotenv(ENV_PATH)
    cid, secret, code = (os.getenv("ZOHO_CLIENT_ID"), os.getenv("ZOHO_CLIENT_SECRET"), os.getenv("ZOHO_GRANT_CODE"))
    accounts = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com").rstrip("/")
    if not (cid and secret and code):
        print("Need ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET and ZOHO_GRANT_CODE in .env", file=sys.stderr)
        return 1
    resp = requests.post(f"{accounts}/oauth/v2/token", params={
        "grant_type": "authorization_code", "client_id": cid, "client_secret": secret, "code": code,
    }, timeout=60)
    data = resp.json()
    if "refresh_token" not in data:
        print(f"Token exchange failed: {data}", file=sys.stderr)
        return 1
    text = ENV_PATH.read_text()
    text = _set_env_value(text, "ZOHO_REFRESH_TOKEN", data["refresh_token"])
    text = _set_env_value(text, "ZOHO_GRANT_CODE", "")
    ENV_PATH.write_text(text)
    print("OK: refresh token saved to .env; grant code cleared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
