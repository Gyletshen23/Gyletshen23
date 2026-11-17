"""
Phone-number authentication system (OTP / one-time passcode over SMS).

A user authenticates by proving control of their own phone number:
  1. POST /auth/request-code  {"phone": "+97517958864"}  -> an OTP is sent by SMS
  2. POST /auth/verify        {"phone": "...", "code": "123456"} -> a session token
  3. Authenticated routes send  Authorization: Bearer <token>

This authenticates the person holding the phone. It does NOT and cannot locate
a phone from its number. There is deliberately no geolocation anywhere in here.

SMS sending is abstracted behind send_sms(). In development the code is written
to the server log (and returned in the response only when AUTH_DEV_MODE=1) so
you can test without a real SMS provider. Wire send_sms() to Twilio, Vonage,
etc. for production.
"""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing

from flask import Flask, g, jsonify, request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = os.environ.get("AUTH_DB_PATH", os.path.join(os.path.dirname(__file__), "auth.db"))
DEV_MODE = os.environ.get("AUTH_DEV_MODE") == "1"      # returns OTP in response; dev only
CODE_TTL_SECONDS = 5 * 60                               # OTP valid for 5 minutes
CODE_LENGTH = 6
MAX_VERIFY_ATTEMPTS = 5                                 # per OTP before it is burned
REQUEST_COOLDOWN_SECONDS = 30                           # min gap between code requests
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60                # token valid for 30 days

# Server-side pepper for hashing OTPs. Set AUTH_PEPPER in production so codes in
# the DB are not verifiable even if the file leaks.
PEPPER = os.environ.get("AUTH_PEPPER", "dev-pepper-change-me").encode()

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                phone       TEXT UNIQUE NOT NULL,
                created_at  INTEGER NOT NULL,
                last_login  INTEGER
            );

            CREATE TABLE IF NOT EXISTS otps (
                phone        TEXT PRIMARY KEY,
                code_hash    TEXT NOT NULL,
                expires_at   INTEGER NOT NULL,
                attempts     INTEGER NOT NULL DEFAULT 0,
                requested_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash  TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                expires_at  INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )
        db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# E.164-ish: a leading + and 8-15 digits.
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(raw):
    """Strip spaces/dashes and validate E.164 format. Returns None if invalid."""
    if not isinstance(raw, str):
        return None
    cleaned = re.sub(r"[\s\-()]", "", raw)
    return cleaned if PHONE_RE.match(cleaned) else None


def hash_code(phone, code):
    """Bind the hash to the phone so a code is only valid for its own number."""
    msg = f"{phone}:{code}".encode()
    return hmac.new(PEPPER, msg, hashlib.sha256).hexdigest()


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def now():
    return int(time.time())


def send_sms(phone, body):
    """
    Deliver an SMS. Replace this body with a real provider call, e.g. Twilio:

        from twilio.rest import Client
        Client(sid, auth).messages.create(to=phone, from_=NUMBER, body=body)

    In dev we just log it.
    """
    app.logger.info("SMS to %s: %s", phone, body)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/auth/request-code")
def request_code():
    data = request.get_json(silent=True) or {}
    phone = normalize_phone(data.get("phone"))
    if not phone:
        return jsonify(error="invalid_phone",
                       message="Provide a valid phone number in E.164 form, e.g. +97517958864."), 400

    db = get_db()
    existing = db.execute("SELECT requested_at FROM otps WHERE phone = ?", (phone,)).fetchone()
    if existing and now() - existing["requested_at"] < REQUEST_COOLDOWN_SECONDS:
        retry = REQUEST_COOLDOWN_SECONDS - (now() - existing["requested_at"])
        return jsonify(error="rate_limited",
                       message=f"Please wait {retry}s before requesting another code."), 429

    code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
    db.execute(
        """INSERT INTO otps (phone, code_hash, expires_at, attempts, requested_at)
           VALUES (?, ?, ?, 0, ?)
           ON CONFLICT(phone) DO UPDATE SET
               code_hash=excluded.code_hash,
               expires_at=excluded.expires_at,
               attempts=0,
               requested_at=excluded.requested_at""",
        (phone, hash_code(phone, code), now() + CODE_TTL_SECONDS, now()),
    )
    db.commit()

    send_sms(phone, f"Your verification code is {code}. It expires in 5 minutes.")

    resp = {"status": "code_sent", "expires_in": CODE_TTL_SECONDS}
    if DEV_MODE:
        resp["dev_code"] = code   # only exposed when AUTH_DEV_MODE=1
    return jsonify(resp), 200


@app.post("/auth/verify")
def verify():
    data = request.get_json(silent=True) or {}
    phone = normalize_phone(data.get("phone"))
    code = data.get("code")
    if not phone or not isinstance(code, str):
        return jsonify(error="invalid_request", message="phone and code are required."), 400

    db = get_db()
    row = db.execute("SELECT * FROM otps WHERE phone = ?", (phone,)).fetchone()
    if not row:
        return jsonify(error="no_code", message="Request a code first."), 400
    if now() > row["expires_at"]:
        db.execute("DELETE FROM otps WHERE phone = ?", (phone,))
        db.commit()
        return jsonify(error="expired", message="Code expired. Request a new one."), 400
    if row["attempts"] >= MAX_VERIFY_ATTEMPTS:
        db.execute("DELETE FROM otps WHERE phone = ?", (phone,))
        db.commit()
        return jsonify(error="too_many_attempts", message="Too many attempts. Request a new code."), 429

    if not hmac.compare_digest(row["code_hash"], hash_code(phone, code)):
        db.execute("UPDATE otps SET attempts = attempts + 1 WHERE phone = ?", (phone,))
        db.commit()
        remaining = MAX_VERIFY_ATTEMPTS - (row["attempts"] + 1)
        return jsonify(error="wrong_code", message=f"Incorrect code. {remaining} attempts left."), 401

    # Success: burn the OTP, upsert the user, issue a session token.
    db.execute("DELETE FROM otps WHERE phone = ?", (phone,))
    db.execute(
        """INSERT INTO users (phone, created_at, last_login) VALUES (?, ?, ?)
           ON CONFLICT(phone) DO UPDATE SET last_login = excluded.last_login""",
        (phone, now(), now()),
    )
    user = db.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone()

    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (hash_token(token), user["id"], now(), now() + SESSION_TTL_SECONDS),
    )
    db.commit()

    return jsonify(status="authenticated", token=token, expires_in=SESSION_TTL_SECONDS), 200


def current_user():
    """Resolve the Bearer token to a user row, or None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer "):].strip()
    db = get_db()
    row = db.execute(
        """SELECT u.id, u.phone, s.expires_at
             FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?""",
        (hash_token(token),),
    ).fetchone()
    if not row or now() > row["expires_at"]:
        return None
    return row


@app.get("/auth/me")
def me():
    user = current_user()
    if not user:
        return jsonify(error="unauthorized", message="Missing or invalid token."), 401
    return jsonify(id=user["id"], phone=user["phone"]), 200


@app.post("/auth/logout")
def logout():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):].strip()
        db = get_db()
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))
        db.commit()
    return jsonify(status="logged_out"), 200


@app.get("/health")
def health():
    return jsonify(status="ok"), 200


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=DEV_MODE)
