import httpx
import pytest

from app import upstox_auth as ua

S = {"UPSTOX_API_KEY": "key", "UPSTOX_API_SECRET": "sec", "UPSTOX_MOBILE": "m", "UPSTOX_PIN": "p",
     "UPSTOX_TOTP_KEY": "t", "UPSTOX_REDIRECT_URI": "https://example.test/cb"}


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_extract_code():
    assert ua.extract_code("https://example.test/cb?code=AbC123&state=x") == "AbC123"
    with pytest.raises(ua.LoginError):
        ua.extract_code("https://example.test/cb?error=denied")


def test_authorization_url_contains_params():
    u = ua.authorization_url("k", "https://example.test/cb")
    assert u.startswith(ua.AUTH_URL) and "client_id=k" in u and "response_type=code" in u


def test_token_validity_checks_profile_endpoint():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200 if req.headers["authorization"] == "Bearer good" else 401)

    with client(handler) as c:
        assert ua.token_is_valid("good", c) and not ua.token_is_valid("bad", c)
        assert not ua.token_is_valid("", c)  # empty token never hits the network


def test_exchange_code_success_and_failure():
    ok = lambda req: httpx.Response(200, json={"access_token": "TOK"})
    bad = lambda req: httpx.Response(401, json={"errors": ["expired"]})
    with client(ok) as c:
        assert ua.exchange_code("c", S, c) == "TOK"
    with client(bad) as c, pytest.raises(ua.LoginError) as e:
        ua.exchange_code("c", S, c)
    assert "401" in str(e.value) and "sec" not in str(e.value)  # no secrets in errors


def test_save_token_replaces_or_appends(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\nUPSTOX_ACCESS_TOKEN=old\nB=2\n")
    ua.save_token("new", p)
    assert p.read_text() == "A=1\nUPSTOX_ACCESS_TOKEN=new\nB=2\n"
    q = tmp_path / ".env2"
    q.write_text("A=1")
    ua.save_token("t", q)
    assert q.read_text() == "A=1\nUPSTOX_ACCESS_TOKEN=t\n"


def test_missing_settings_named_but_values_not_leaked(monkeypatch):
    for k in ua.REQUIRED:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("UPSTOX_PIN", "123456")
    with pytest.raises(ua.LoginError) as e:
        ua.settings()
    assert "UPSTOX_API_KEY" in str(e.value) and "123456" not in str(e.value)
