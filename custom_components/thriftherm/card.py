"""The dashboard card: served by the integration and loaded on every dashboard.

Card and integration ship together, so they always fit and nobody installs a second
thing. The file is served as it is (no build step); the version in its address makes a
browser load the new card after an update instead of one from its cache. The card's
wording comes from `texts.py` over the websocket, so there is one source for it.

The dashboards load it as one of their resources, which Home Assistant keeps in its storage;
only where resources are kept in YAML is it added to every page instead. Added to every page
only, the card was missing now and then (issue #1, 2026-10-18: Firefox and the companion app
showed "Custom element not found"), and the resource the reporter added by hand cured it.
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
RESOURCE_TYPE = "module"
CARD_FILE = Path(__file__).parent / "frontend" / "thriftherm-card.js"
WS_TEXTS = f"{DOMAIN}/card_texts"
WS_REASONS = f"{DOMAIN}/decision_reasons"


def card_digest() -> str:
    """A short fingerprint of the card file: changes with every change of the card, even within a version."""
    return hashlib.sha256(CARD_FILE.read_bytes()).hexdigest()[:8]


def card_url(version: str | None, digest: str) -> str:
    """The address the dashboards load the card from: version and fingerprint, so a new card is never cached."""
    return f"{CARD_URL}?v={version}-{digest}" if version else f"{CARD_URL}?v={digest}"


async def async_register(hass: HomeAssistant) -> None:
    """Serve the card and answer its question for the wording, once per run."""
    websocket_api.async_register_command(hass, websocket_card_texts)
    websocket_api.async_register_command(hass, websocket_decision_reasons)
    if hass.http is None:
        return  # no web server (a test, a start without frontend): nothing to serve to
    # no long cache headers: the version in the address already keeps an old card out
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=False)])


async def async_load(hass: HomeAssistant, version: str | None) -> None:
    """Load the card on every dashboard.

    Done for each setup of the entry, not once per run: removing the entry takes the card
    from the resources, and an entry added again without a restart needs it back.
    """
    if hass.http is None:
        return  # nothing serves the card
    if "frontend" not in hass.config.components:
        _LOGGER.debug("Frontend not loaded: the card is served at %s but not loaded on the dashboards", CARD_URL)
        return
    url = card_url(version, await hass.async_add_executor_job(card_digest))
    try:
        if await _async_put_resource(hass, url):
            return
    except Exception:  # the card on every page is the way back, never a failed start
        _LOGGER.warning("The card could not be added to the dashboard resources; it is loaded on every page", exc_info=True)
    from homeassistant.components import frontend

    frontend.add_extra_js_url(hass, url)


def _resources(hass: HomeAssistant) -> Any:
    """The dashboards' resources where Home Assistant keeps them in its storage, else None (kept in YAML)."""
    data = hass.data.get("lovelace")
    if isinstance(data, dict):  # before the dataclass
        resources, mode = data.get("resources"), data.get("resource_mode", data.get("mode"))
    else:
        resources = getattr(data, "resources", None)
        mode = getattr(data, "resource_mode", getattr(data, "mode", None))
    if resources is None or mode != "storage" or not hasattr(resources, "async_create_item"):
        return None
    return resources


def _ours(resources: Any) -> list[dict[str, Any]]:
    """The resources for the card file: one of ours, or one somebody added by hand, with or without a version."""
    return [r for r in resources.async_items() if str(r.get("url", "")).split("?")[0] == CARD_URL]


async def _async_put_resource(hass: HomeAssistant, url: str) -> bool:
    """The card among the dashboards' resources at `url`, once. False where resources are kept in YAML."""
    resources = _resources(hass)
    if resources is None:
        return False
    await resources.async_get_info()  # loads them from storage
    found = _ours(resources)
    if not found:
        await resources.async_create_item({"res_type": RESOURCE_TYPE, "url": url})
        return True
    first, *more = found
    if first.get("url") != url or first.get("type") != RESOURCE_TYPE:
        await resources.async_update_item(first["id"], {"res_type": RESOURCE_TYPE, "url": url})
    for extra in more:  # two entries would load the card twice
        await resources.async_delete_item(extra["id"])
    return True


async def async_remove_resource(hass: HomeAssistant) -> None:
    """Thriftherm removed: its card goes from the dashboards' resources, all others stay."""
    resources = _resources(hass)
    if resources is None:
        return
    await resources.async_get_info()
    for item in _ours(resources):
        await resources.async_delete_item(item["id"])


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
