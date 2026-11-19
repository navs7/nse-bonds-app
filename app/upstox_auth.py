"""Daily Upstox access-token login (Upstox tokens expire every day, around 3:30 AM IST).

Flow: open the Upstox login page with Playwright, fill mobile + TOTP + PIN, catch the redirect
carrying ?code=..., exchange it for an access token, and save it to .env as UPSTOX_ACCESS_TOKEN.
A token that still works is reused, so running this twice in a day does not log in twice.

All secrets come from .env (git-ignored). Nothing secret is ever printed.

    python -m app.upstox_auth            # login only if the saved token is missing/expired
    python -m app.upstox_auth --force    # always log in again
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import config  # loads .env into os.environ

AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api-v2.upstox.com/login/authorization/token"  # host used by the old working script
PROFILE_URL = "https://api.upstox.com/v2/user/profile"
ENV_PATH = config.ROOT / ".env"

REQUIRED = ["UPSTOX_API_KEY", "UPSTOX_API_SECRET", "UPSTOX_MOBILE", "UPSTOX_PIN", "UPSTOX_TOTP_KEY"]


class LoginError(RuntimeError):
    pass


def settings() -> dict[str, str]:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        raise LoginError("Missing in .env: " + ", ".join(missing))
    s = {k: os.environ[k] for k in REQUIRED}
    s["UPSTOX_REDIRECT_URI"] = os.environ.get("UPSTOX_REDIRECT_URI", "https://account.upstox.com/developer/apps")
    s["UPSTOX_LOGIN_HEADLESS"] = os.environ.get("UPSTOX_LOGIN_HEADLESS", "false")
    return s


def authorization_url(api_key: str, redirect_uri: str) -> str:
    q = urlencode({"response_type": "code", "client_id": api_key, "redirect_uri": redirect_uri})
    return f"{AUTH_URL}?{q}"


def extract_code(url: str) -> str:
    codes = parse_qs(urlparse(url).query).get("code")
    if not codes:
        raise LoginError("Redirect URL did not contain an authorization code")
    return codes[0]


def token_is_valid(token: str, client: httpx.Client) -> bool:
    if not token:
        return False
    r = client.get(PROFILE_URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    return r.status_code == 200


def fetch_code(s: dict[str, str]) -> str:
    """Drive the login page. Needs: playwright install chromium"""
    import pyotp
    from playwright.sync_api import sync_playwright

    redirect = s["UPSTOX_REDIRECT_URI"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=s["UPSTOX_LOGIN_HEADLESS"].lower() == "true")
        try:
            page = browser.new_context().new_page()
            with page.expect_request(lambda r: r.url.startswith(redirect) and "code=" in r.url,
                                     timeout=90_000) as req:
                page.goto(authorization_url(s["UPSTOX_API_KEY"], redirect))
                page.locator("#mobileNum").fill(s["UPSTOX_MOBILE"])
                page.get_by_role("button", name="Get OTP").click()
                page.locator("#otpNum").fill(pyotp.TOTP(s["UPSTOX_TOTP_KEY"]).now())
                page.get_by_role("button", name="Continue").click()
                page.get_by_label("Enter 6-digit PIN").fill(s["UPSTOX_PIN"])
                page.get_by_role("button", name="Continue").click()
            return extract_code(req.value.url)
        except Exception as e:  # do not leak page contents/credentials into the message
            raise LoginError(f"Browser login failed ({type(e).__name__}). Try UPSTOX_LOGIN_HEADLESS=false.") from None
        finally:
            browser.close()


def exchange_code(code: str, s: dict[str, str], client: httpx.Client) -> str:
    r = client.post(
        TOKEN_URL,
        headers={"Accept": "application/json", "Api-Version": "2.0"},
        data={
            "code": code,
            "client_id": s["UPSTOX_API_KEY"],
            "client_secret": s["UPSTOX_API_SECRET"],
            "redirect_uri": s["UPSTOX_REDIRECT_URI"],
            "grant_type": "authorization_code",
        },
    )
    token = r.json().get("access_token") if r.headers.get("content-type", "").startswith("application/json") else None
    if r.status_code != 200 or not token:
        raise LoginError(f"Token exchange failed (HTTP {r.status_code}). The code may have expired; rerun.")
    return token


def save_token(token: str, env_path: Path = ENV_PATH) -> None:
    line = f"UPSTOX_ACCESS_TOKEN={token}"
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if re.search(r"^UPSTOX_ACCESS_TOKEN=.*$", text, flags=re.M):
        text = re.sub(r"^UPSTOX_ACCESS_TOKEN=.*$", lambda _m: line, text, flags=re.M)
    else:
        text = text + ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    env_path.write_text(text, encoding="utf-8")


def ensure_token(force: bool = False) -> str:
    """Return a working token, logging in only when needed."""
    with httpx.Client(timeout=30) as client:
        current = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
        if not force and token_is_valid(current, client):
            print("Existing Upstox token is still valid; no login needed.")
            return current
        s = settings()
        print("Logging in to Upstox...")
        token = exchange_code(fetch_code(s), s, client)
        save_token(token)
        os.environ["UPSTOX_ACCESS_TOKEN"] = token
        print("New Upstox token saved to .env.")
        return token


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="log in even if the saved token still works")
    try:
        ensure_token(ap.parse_args(argv).force)
    except LoginError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
