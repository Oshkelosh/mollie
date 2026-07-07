# Mollie (`mollie`)

Accept payments via Mollie.

## Overview

| | |
|---|---|
| Addon ID | `mollie` |
| Category | payment |
| Version | 1.0.0 |
| Category guide | [../README.md](../README.md) |

Only **one** payment addon can be active at a time.

## Enable and configure

1. Install this package under `app/addons/payments/mollie/`
2. Open **Admin → Payments → Mollie** at `/admin/payments/mollie`
3. Enter credentials and enable **Enable this payment processor**

## Configuration schema

| Field | Type | Description |
|-------|------|-------------|
| `api_key` | secret | Mollie API key |
| `webhook_secret` | secret | Webhook signing secret |
| `redirect_url` | string | Customer redirect after payment |

Secrets are stored in `addon_configs`, not in `.env`.

## Routes

### Public API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/payments/mollie/checkout` | Start checkout (optional; prefer generic order checkout) |
| POST | `/api/v1/payments/mollie/webhook` | PSP webhook endpoint |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/payments/mollie` | Config form |
| POST | `/admin/payments/mollie/save` | Save config |

## Core integration

- **Storefront checkout:** `POST /api/v1/orders/{order_id}/checkout` → `PaymentAddon.create_payment()` → redirect URL
- **Webhook:** `POST /api/v1/payments/mollie/webhook` → `parse_webhook()` → core `process_payment_webhook()`
- **Amounts:** smallest currency unit (cents)

## Provider setup

Register webhook URL (replace `{PUBLIC_APP_URL}` with your public base URL):

```
{PUBLIC_APP_URL}/api/v1/payments/mollie/webhook
```

Webhook signature header: **`signature (default)`**

1. Create a Mollie account and generate an API key.
2. Mollie sends webhooks to your configured URL automatically when creating payments.
3. Use test API keys for development.

## Package layout

```
mollie/
├── README.md
├── addon.py
├── routes.py
└── templates/
```

## See also

- [Payment addon development](../README.md)
- [Oshkelosh addon guide](../../README.md)
