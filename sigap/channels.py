"""Outbound delivery registry. Each chat channel registers a sender when it starts, so staff replies
and reminders reach students on whatever channel they used (Telegram now, WhatsApp later)."""
from collections.abc import Awaitable, Callable

Sender = Callable[[str, str], Awaitable[bool]]  # (external_id, html_text) -> delivered?
_senders: dict[str, Sender] = {}


def register(channel: str, sender: Sender) -> None:
    _senders[channel] = sender


def available(channel: str) -> bool:
    return channel in _senders


async def deliver(channel: str, external_id: str, html_text: str) -> bool:
    sender = _senders.get(channel)
    if not sender:
        return False
    try:
        return await sender(external_id, html_text)
    except Exception:  # a failed delivery is reported to staff, never crashes the caller
        return False
