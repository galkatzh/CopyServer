import asyncio
import json
import os

# SSE connections: device_id -> set of asyncio.Queue
_sse_clients: dict[str, set[asyncio.Queue]] = {}


def add_sse_client(device_id: str) -> asyncio.Queue:
    q = asyncio.Queue()
    _sse_clients.setdefault(device_id, set()).add(q)
    return q


def remove_sse_client(device_id: str, q: asyncio.Queue):
    if device_id in _sse_clients:
        _sse_clients[device_id].discard(q)
        if not _sse_clients[device_id]:
            del _sse_clients[device_id]


def sse_connected_devices() -> set[str]:
    return set(_sse_clients.keys())


async def broadcast_clip(clip: dict, exclude_device_id: str = None):
    """Send clip to all SSE clients except the sender."""
    data = json.dumps(clip)
    for device_id, queues in _sse_clients.items():
        if device_id == exclude_device_id:
            continue
        for q in queues:
            await q.put(data)


async def send_push_notifications(clip: dict, exclude_device_id: str = None):
    """Send web push to devices that don't have an active SSE connection."""
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return

    vapid_private = os.environ.get("VAPID_PRIVATE_KEY")
    vapid_contact = os.environ.get("VAPID_CONTACT", "mailto:admin@example.com")
    if not vapid_private:
        return

    from db import get_push_subscriptions
    subs = get_push_subscriptions(exclude_device_id=exclude_device_id)
    connected = sse_connected_devices()

    preview = clip.get("content", "") or clip.get("filename", "")
    if len(preview) > 100:
        preview = preview[:100] + "..."

    payload = json.dumps({
        "title": f"New {clip['type']} from {clip.get('device_name', 'device')}",
        "body": preview,
        "clip_id": clip["id"],
    })

    for sub in subs:
        try:
            webpush(
                subscription_info=json.loads(sub["push_subscription"]),
                data=payload,
                vapid_private_key=vapid_private,
                vapid_claims={"sub": vapid_contact},
            )
        except Exception as e:
            print(f"Push failed for device {sub['id']}: {e}")
