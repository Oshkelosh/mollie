"""
Mollie payment integration.

Supports checkout payments, refunds, and webhooks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field, SecretStr

from app.addons.payments.base import PaymentAddon
from app.addons.payments.helpers import create_payment_error, effective_redirect_url, extract_order_id, mock_checkout
from schemas.payment import PaymentWebhookOutcome
from app.addons.log import info, warning
from app.addons.config_serialization import dump_addon_config

_API_BASE = "https://api.mollie.com/v2"


class MollieConfig(BaseModel):
    api_key: SecretStr = Field(default=..., description="Mollie API key")
    webhook_secret: SecretStr = Field(default=..., description="Webhook signing secret")
    redirect_url: str = Field(
        default="",
        description="Optional override for redirect after payment (leave blank to use Site URL)",
    )

    @classmethod
    def config_model(cls):
        return cls


class MollieAddon(PaymentAddon):
    addon_id: str = "mollie"
    addon_name: str = "Mollie"
    addon_description: str = "Accept payments via Mollie."
    addon_category: str = "payment"
    version: str = "1.0.0"
    is_enabled: bool = False

    _config: Dict[str, Any] | None = None
    _api_key: str | None = None
    _webhook_secret: str | None = None
    _redirect_url: str = ""

    @classmethod
    def config_schema(cls):
        return MollieConfig

    async def initialize(self, config: dict) -> None:
        validated = self.config_schema()(**config)
        self._config = dump_addon_config(validated)
        self._api_key = validated.api_key.get_secret_value()
        self._webhook_secret = validated.webhook_secret.get_secret_value()
        self._redirect_url = validated.redirect_url
        self.is_enabled = True
        info("Mollie", "Initialized")

    async def validate_config(self, config: dict) -> None:
        from app.core.exceptions import ValidationError

        validated = self.config_schema()(**config)
        api_key = validated.api_key.get_secret_value()
        if not api_key:
            return
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_API_BASE}/payments",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"limit": 1},
            )
        if resp.status_code == 401:
            raise ValidationError(message="Invalid API key — check your credentials")
        if resp.status_code == 403:
            raise ValidationError(
                message="API key is valid but missing required permissions: payments:read"
            )
        if resp.status_code >= 400:
            raise ValidationError(message="Mollie rejected the API key")

    async def shutdown(self) -> None:
        self._api_key = None
        self._webhook_secret = None
        self.is_enabled = False

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("Mollie API key not configured")

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.request(
                method,
                f"{_API_BASE}{path}",
                headers={**self._auth_headers(), "Content-Type": "application/json"},
                json=json_body,
            )
            resp.raise_for_status()
            return resp.json()

    async def create_payment(
        self,
        amount: int,
        currency: str,
        order_id: str,
        customer_email: str,
        *,
        return_url: str | None = None,
        cancel_url: str | None = None,
    ) -> Dict[str, Any]:
        if not self._api_key:
            return mock_checkout("mollie", order_id, amount, currency)

        body: dict[str, Any] = {
            "amount": {"currency": currency.upper(), "value": f"{amount / 100:.2f}"},
            "description": f"Order {order_id}",
            "redirectUrl": effective_redirect_url(
                self._redirect_url, fallback=return_url or ""
            ),
            "metadata": {"order_id": order_id},
        }
        if customer_email:
            body["billingEmail"] = customer_email

        try:
            data = await self._request("POST", "/payments", json_body=body)
            return {
                "success": True,
                "payment_id": data.get("id", ""),
                "session_id": data.get("id", ""),
                "url": data.get("_links", {}).get("checkout", {}).get("href", ""),
                "order_id": order_id,
            }
        except Exception as exc:
            warning("Mollie", "create_payment error: {}", exc)
            return create_payment_error("mollie", exc, order_id)

    async def confirm_payment(self, payment_id: str) -> Dict[str, Any]:
        status = await self.get_payment_status(payment_id)
        if status.get("status") == "error":
            return {"success": False, "error": status.get("detail", "Unknown error")}
        return {
            "success": status.get("status") == "paid",
            "payment_id": payment_id,
            "status": status.get("status", "unknown"),
            "amount": status.get("amount", 0),
        }

    async def refund_payment(self, payment_id: str, amount: int) -> Dict[str, Any]:
        if not self._api_key:
            return {"success": False, "error": "Mollie API key not configured"}

        try:
            # Refund in the payment's own currency; hardcoding EUR fails (or
            # mis-refunds) any non-EUR payment.
            payment = await self._request("GET", f"/payments/{payment_id}")
            currency = str(
                (payment.get("amount") or {}).get("currency") or "EUR"
            ).upper()
            body = {"amount": {"currency": currency, "value": f"{amount / 100:.2f}"}}
            data = await self._request(
                "POST",
                f"/payments/{payment_id}/refunds",
                json_body=body,
            )
            return {
                "success": True,
                "refund_id": data.get("id", ""),
                "amount": amount,
                "status": data.get("status", "pending"),
            }
        except Exception as exc:
            warning("Mollie", "refund_payment({}) error: {}", payment_id, exc)
            return {"success": False, "error": str(exc)}

    async def get_payment_status(self, payment_id: str) -> Dict[str, Any]:
        if not self._api_key:
            return {"payment_id": payment_id, "status": "error", "detail": "Not configured"}

        try:
            data = await self._request("GET", f"/payments/{payment_id}")
            amount_value = data.get("amount", {}).get("value", "0")
            return {
                "payment_id": payment_id,
                "status": data.get("status", "unknown"),
                "amount": int(float(amount_value) * 100),
                "currency": data.get("amount", {}).get("currency", "eur").lower(),
            }
        except Exception as exc:
            warning("Mollie", "get_payment_status({}) error: {}", payment_id, exc)
            return {"payment_id": payment_id, "status": "error", "detail": str(exc)}

    def _webhook_payment_id(self, body: bytes) -> str:
        """Extract the Mollie payment id from a form- or JSON-encoded webhook body."""
        text = body.decode("utf-8", errors="ignore").strip()
        if text.startswith("{"):
            import json

            try:
                data = json.loads(text)
            except ValueError:
                return ""
            resource = data.get("resource", data)
            return str(resource.get("id", data.get("id", "")))
        parsed = parse_qs(text)
        values = parsed.get("id")
        return values[0] if values else ""

    async def verify_webhook(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
    ) -> bool:
        """Mollie webhooks are unsigned: confirm by re-fetching the payment.

        The webhook body only carries a payment id; the authoritative status comes
        from the Mollie API using our own key, so a forged id fails the paid check.
        """
        del headers
        payment_id = self._webhook_payment_id(body)
        if not payment_id:
            return False
        status = await self.get_payment_status(payment_id)
        return str(status.get("status", "")).strip().lower() == "paid"

    async def parse_webhook(
        self, payload: Dict[str, Any], signature: str
    ) -> PaymentWebhookOutcome:
        try:
            event_type = payload.get("type", payload.get("status", ""))
            event_data = payload.get("resource", payload)
            event_id = str(event_data.get("id", payload.get("id", "")))
            info("Mollie", "Webhook received: {}", event_type)

            if event_type in ("payment.paid", "paid"):
                order_id = extract_order_id(event_data.get("metadata"))
                return PaymentWebhookOutcome(
                    handled=True,
                    event_id=event_id,
                    event_type=str(event_type),
                    mark_paid=order_id is not None,
                    order_id=order_id,
                    payment_id=str(event_data.get("id", "")) or None,
                )

            return PaymentWebhookOutcome(
                handled=True,
                event_id=event_id,
                event_type=str(event_type),
            )
        except Exception as exc:
            warning("Mollie", "parse_webhook error: {}", exc)
            return PaymentWebhookOutcome(handled=False, error=str(exc))

    def get_routers(self) -> List[APIRouter]:
        from app.addons.payments.mollie.routes import api_router

        return [api_router]

    def get_admin_routes(self) -> List[APIRouter]:
        from app.addons.payments.mollie.routes import admin_router

        return [admin_router]

    def get_admin_templates(self) -> str:
        from pathlib import Path

        return str(Path(__file__).resolve().parent / "templates")

    def get_admin_static(self) -> str:
        from pathlib import Path

        return str(Path(__file__).resolve().parent / "static")
