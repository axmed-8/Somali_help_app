"""Verify CSRF protection on login (does not depend on demo accounts)."""
import os
import re
import sys
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["GURMADNET_DB"] = "mysql"
os.environ["EMAIL_PROVIDER"] = "memory"

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)
os.environ["EMAIL_PROVIDER"] = "memory"

import app as ers

importlib.reload(ers)
ers.app.config["TESTING"] = False
ers.app.config["WTF_CSRF_ENABLED"] = True
client = ers.app.test_client()

r = client.get("/login")
assert r.status_code == 200, r.status_code
html = r.get_data(as_text=True)
assert "csrf_token" in html
assert "csrf-token" in html
assert "csrf.js" in html
assert "Demo accounts" not in html
assert "admin@emergency.so" not in html

r2 = client.post("/login", data={"username": "nobody@example.com", "password": "x"})
assert r2.status_code in (400, 302), r2.status_code
print("csrf-without-token:", r2.status_code)

m = re.search(r'name="csrf_token" value="([^"]+)"', html)
assert m, "csrf token missing from login HTML"
token = m.group(1)
r3 = client.post(
    "/login",
    data={"username": "nobody@example.com", "password": "wrong", "csrf_token": token},
    follow_redirects=True,
)
print("login-with-csrf (invalid creds):", r3.status_code)
assert r3.status_code == 200
assert b"Invalid email or password" in r3.data or b"invalid" in r3.data.lower()
print("USE_MYSQL=", ers.USE_MYSQL)
print("ALL CSRF CHECKS PASSED")
