# Phone-Number Authentication

A small, self-contained authentication backend where a user proves control of
**their own** phone number with a one-time passcode (OTP) sent by SMS, and
receives a session token for authenticated requests.

> This system **authenticates** the person holding a phone. It does **not**
> locate a phone from its number — there is no geolocation anywhere in it, by
> design. Finding a phone from a number is not something an app can do; it
> requires the phone owner's consent (Find My Device / Find My) or the carrier.

## Flow

1. `POST /auth/request-code` `{"phone": "+97517958864"}` → OTP sent by SMS
2. `POST /auth/verify` `{"phone": "+97517958864", "code": "123456"}` → session token
3. Send `Authorization: Bearer <token>` on authenticated routes

## Endpoints

| Method | Path                 | Purpose                               |
|--------|----------------------|---------------------------------------|
| POST   | `/auth/request-code` | Send an OTP to a phone number         |
| POST   | `/auth/verify`       | Verify the OTP, get a session token   |
| GET    | `/auth/me`           | Current user (requires Bearer token)  |
| POST   | `/auth/logout`       | Invalidate the current token          |
| GET    | `/health`            | Liveness check                        |

## Run

```sh
pip install -r requirements.txt
AUTH_DEV_MODE=1 python app.py        # dev: OTP is returned in the response + logged
```

In dev mode the code is returned as `dev_code` so you can test without an SMS
provider. **Turn this off in production** (unset `AUTH_DEV_MODE`).

### Try it

```sh
curl -XPOST localhost:5000/auth/request-code -H 'Content-Type: application/json' \
     -d '{"phone":"+97517958864"}'
# -> {"dev_code":"690784", ...}
curl -XPOST localhost:5000/auth/verify -H 'Content-Type: application/json' \
     -d '{"phone":"+97517958864","code":"690784"}'
# -> {"token":"...", ...}
curl localhost:5000/auth/me -H "Authorization: Bearer <token>"
```

## Security notes

- OTPs are **hashed** (HMAC-SHA256 with a server pepper) before storage and
  bound to the phone number, so they are not reusable across numbers.
- Codes expire in 5 minutes, allow 5 verify attempts, and are burned on use.
- A 30-second cooldown between code requests slows SMS-pumping abuse.
- Session tokens are random 256-bit values; only their hashes are stored.

### For production

- Set `AUTH_PEPPER` to a strong secret and unset `AUTH_DEV_MODE`.
- Replace `send_sms()` in `app.py` with a real provider (Twilio, Vonage, …).
- Put it behind HTTPS and add per-IP rate limiting at the proxy.
- SQLite is fine to start; move to Postgres for scale.
