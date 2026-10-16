"""The dashboard card: served by the integration and loaded on every dashboard.

Card and integration ship together, so they always fit and nobody installs a second
thing. The file is served as it is (no build step); the version in its address makes a
browser load the new card after an update instead of one from its cache. The card's
wording comes from `texts.py` over the websocket, so there is one source for it.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from . import texts
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_URL = f"/{DOMAIN}/thriftherm-card.js"
CARD_FILE = Path(__file__).parent / "frontend" / "thriftherm-card.js"
WS_TEXTS = f"{DOMAIN}/card_texts"
WS_REASONS = f"{DOMAIN}/decision_reasons"


def card_digest() -> str:
    """A short fingerprint of the card file: changes with every change of the card, even within a version."""
    return hashlib.sha256(CARD_FILE.read_bytes()).hexdigest()[:8]


def card_url(version: str | None, digest: str) -> str:
    """The address the dashboards load the card from: version and fingerprint, so a new card is never cached."""
    return f"{CARD_URL}?v={version}-{digest}" if version else f"{CARD_URL}?v={digest}"


async def async_register(hass: HomeAssistant, version: str | None) -> None:
    """Serve the card, load it on every dashboard and answer its question for the wording."""
    websocket_api.async_register_command(hass, websocket_card_texts)
    websocket_api.async_register_command(hass, websocket_decision_reasons)
    if hass.http is None:
        return  # no web server (a test, a start without frontend): nothing to serve to
    # no long cache headers: the version in the address already keeps an old card out
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=False)])
    if "frontend" in hass.config.components:
        from homeassistant.components import frontend

        digest = await hass.async_add_executor_job(card_digest)
        frontend.add_extra_js_url(hass, card_url(version, digest))
    else:
        _LOGGER.debug("Frontend not loaded: the card is served at %s but not loaded on the dashboards", CARD_URL)


@websocket_api.websocket_command({vol.Required("type"): WS_TEXTS, vol.Optional("language"): str})
@callback
def websocket_card_texts(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The card's wording in the language of the person looking at it (the server's language without one)."""
    connection.send_result(msg["id"], texts.card_texts(msg.get("language") or hass.config.language))


@websocket_api.websocket_command({vol.Required("type"): WS_REASONS, vol.Optional("language"): str})
@callback
def websocket_decision_reasons(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The decision reasons of the last cycle in the viewer's language (the sensor holds the server's)."""
    codes: list[str] = []
    rendered: list[str] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        data = getattr(getattr(entry, "runtime_data", None), "data", None)
        if not data:
            continue
        reasons = data["advice"].reasons
        codes.extend(r.code for r in reasons)
        rendered.extend(texts.render_reasons(reasons, msg.get("language") or hass.config.language, data.get("room_names") or {}))
    connection.send_result(msg["id"], {"codes": codes, "texts": rendered})
