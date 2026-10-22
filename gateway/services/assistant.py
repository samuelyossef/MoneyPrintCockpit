"""Assistant channel-resolution helper."""
from __future__ import annotations

from typing import Any, AsyncIterator, Literal

from gateway import main as m


def _assistant_channel(channels: list[dict[str, Any]], selected_id: str | None, args: dict[str, Any]) -> dict[str, Any] | None:
    requested = str(selected_id or args.get("channel_id") or "").strip()
    if requested:
        match = next((item for item in channels if str(item.get("id")) == requested), None)
        if match:
            return match
    name = str(args.get("channel_name") or "").strip().casefold()
    if name:
        matches = [item for item in channels if str(item.get("name") or "").casefold() == name]
        if len(matches) == 1:
            return matches[0]
    return channels[0] if len(channels) == 1 else None
