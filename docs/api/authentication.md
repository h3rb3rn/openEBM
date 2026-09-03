# Authentication API

## Login

```http
POST /api/auth/token
Content-Type: application/x-www-form-urlencoded

username=admin@demo.local&password=demo1234
```

**Response** (2FA disabled for this account)
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "tenant_id": "uuid",
  "user_id": "uuid",
  "role": "admin",
  "requires_2fa": false,
  "pending_token": null
}
```

**Response** (2FA enabled — see [Two-factor authentication](#two-factor-authentication))
```json
{
  "access_token": null,
  "requires_2fa": true,
  "pending_token": "eyJ..."
}
```

The server also sets an **httpOnly `ebm_session` cookie** containing the same JWT (`SameSite=Lax`), but only once authentication is fully complete — if the account has 2FA enabled, no cookie is set until `POST /api/auth/2fa/verify` succeeds. Browser-based clients (the web UI) rely entirely on this cookie — it is attached automatically to same-origin requests and is never touched by JavaScript. Non-browser clients that cannot store cookies may instead use `access_token` from the JSON body as `Authorization: Bearer <access_token>`; the server checks the cookie first and falls back to this header.

## Logout

```http
POST /api/auth/logout
```

Clears the `ebm_session` cookie server-side.

---

## Current user

```http
GET /api/auth/me
Authorization: Bearer <token>
```

Returns the `CurrentUser` schema:

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "email": "admin@demo.local",
  "full_name": "Demo Administrator",
  "role": "admin",
  "auth_type": "user",
  "scopes": null
}
```

---

## Two-factor authentication

TOTP-based (RFC 6238), managed by the user via the `/profil` page or directly:

| Endpoint | Description |
|---|---|
| `GET /api/auth/2fa/status` | Whether 2FA is enabled for the current user |
| `POST /api/auth/2fa/setup` | Generate a new secret + QR code (`qr_code_png_base64`); does not activate 2FA yet |
| `POST /api/auth/2fa/enable` | Confirm setup with a live code from the authenticator app; activates 2FA and returns 8 one-time backup codes |
| `POST /api/auth/2fa/disable` | Requires current password **and** a valid TOTP/backup code |
| `POST /api/auth/2fa/verify` | Second login step: exchanges `pending_token` + code for a full session |

Backup codes are stored only as SHA-256 hashes and are single-use — each is removed from the stored list upon successful verification. The `pending_token` returned by `/auth/token` when 2FA is required carries `scope: "2fa_pending"` and is rejected by every endpoint that expects a full session; it can only be redeemed at `/api/auth/2fa/verify`, and expires after 5 minutes.

## Password change

```http
PATCH /api/auth/password
Content-Type: application/json

{"current_password": "...", "new_password": "..."}
```

Requires the current password; the new password must be at least 8 characters.

---

## API key authentication

External programs authenticate with an `X-API-Key` header instead of a JWT:

```http
POST /api/analysis/instant
X-API-Key: ebm_live_<token>
Content-Type: application/json
```

For API key callers, `auth_type` is `"api_key"` and `scopes` lists the permitted operations. Attempting an endpoint that requires a scope not in the key's scope list returns HTTP 403.

### Available scopes

| Scope | Grants access to |
|---|---|
| `analysis` | `POST /api/analysis/instant`, `POST /api/analysis/persistent` |
| `interop` | `POST /api/interop/gdt/analyze`, `GET /api/interop/gdt/export/*`, `POST /api/interop/fhir/v4/$analyze-ebm` |
| `patients` | `GET/POST /api/patients/*` |
| `cases` | `GET /api/cases/*`, `PATCH /api/cases/*/close`, `GET /api/cases/*/audit` |

---

## Roles

| Role | Description |
|---|---|
| `admin` | Full access including user management, catalog import, API key management |
| `arzt` | Analysis, patient management, case files |
| `mfa` | Read-only access + GOP decisions |
| `readonly` | Read-only |

JWT users are authorised by role; the role check is implicit via endpoint guards. API key users are authorised by scope.
