"""Mollie addon routes — thin delegates to shared payment route factory."""

from __future__ import annotations

from typing import Any

from app.addons.payments.shared_routes import build_payment_routers


def _parse_mollie_config_form(form: Any) -> tuple[dict[str, Any], bool]:
    return (
        {
            "api_key": form.get("api_key", ""),
            "webhook_secret": form.get("webhook_secret", ""),
            "redirect_url": form.get("redirect_url", ""),
        },
        form.get("is_enabled") == "on",
    )


admin_router, api_router, jinja_env = build_payment_routers(
    "mollie",
    template_name="mollie_config.html",
    page_title="Mollie Settings",
    secret_keys=("api_key", "webhook_secret"),
    parse_config_form=_parse_mollie_config_form,
)
