# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fastapi",
#     "uvicorn[standard]",
#     "python-multipart",
#     "pywebpush",
#     "cryptography",
#     "python-dotenv",
# ]
# ///

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

import db
import notify

PASSPHRASE = os.environ.get("PASSPHRASE", "changeme")
PORT = int(os.environ.get("PORT", "8080"))
UPLOAD_MAX_MB = int(os.environ.get("UPLOAD_MAX_MB", "100"))
CLIP_EXPIRE_DAYS = int(os.environ.get("CLIP_EXPIRE_DAYS", "7"))
UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")


@asynccontextmanager
async def lifespan(app):
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    db.init_db()

    async def cleanup_loop():
        while True:
            await asyncio.sleep(3600)
            db.cleanup_expired()

    task = asyncio.create_task(cleanup_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


# --- Auth dependency ---

async def get_current_device(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = auth[7:]
    device = db.get_device_by_token(token)
    if not device:
        raise HTTPException(401, "Invalid token")
    db.touch_device(device["id"])
    return device


# --- Auth routes ---

@app.post("/api/auth/pair")
async def pair_device(request: Request):
    body = await request.json()
    passphrase = body.get("passphrase", "")
    device_name = body.get("deviceName", "").strip()
    if not device_name:
        raise HTTPException(400, "Device name required")
    if passphrase != PASSPHRASE:
        raise HTTPException(403, "Wrong passphrase")
    device = db.create_device(device_name)
    return device


@app.get("/api/auth/verify")
async def verify_token(device=Depends(get_current_device)):
    return {"id": device["id"], "name": device["name"]}


# --- Clip routes ---

@app.get("/api/clips")
async def get_clips(before: int = None, limit: int = 50, device=Depends(get_current_device)):
    clips = db.list_clips(limit=min(limit, 100), before=before)
    return clips


@app.post("/api/clips/text")
async def create_text_clip(request: Request, device=Depends(get_current_device)):
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(400, "Content required")
    clip = db.create_clip("text", device["id"], content=content, expire_days=CLIP_EXPIRE_DAYS)
    clip["device_name"] = device["name"]
    await notify.broadcast_clip(clip, exclude_device_id=device["id"])
    asyncio.create_task(notify.send_push_notifications(clip, exclude_device_id=device["id"]))
    return clip


@app.post("/api/clips/file")
async def create_file_clip(
    file: UploadFile = File(...),
    device=Depends(get_current_device),
):
    if file.size and file.size > UPLOAD_MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {UPLOAD_MAX_MB}MB)")

    file_id = db.gen_id()
    ext = os.path.splitext(file.filename or "")[1]
    stored_name = f"{file_id}{ext}"
    filepath = os.path.join(UPLOADS_DIR, stored_name)

    size = 0
    with open(filepath, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > UPLOAD_MAX_MB * 1024 * 1024:
                f.close()
                os.remove(filepath)
                raise HTTPException(413, f"File too large (max {UPLOAD_MAX_MB}MB)")
            f.write(chunk)

    clip = db.create_clip(
        "file", device["id"],
        filename=file.filename,
        filepath=filepath,
        filesize=size,
        mimetype=file.content_type,
        expire_days=CLIP_EXPIRE_DAYS,
    )
    clip["device_name"] = device["name"]
    await notify.broadcast_clip(clip, exclude_device_id=device["id"])
    asyncio.create_task(notify.send_push_notifications(clip, exclude_device_id=device["id"]))
    return clip


@app.delete("/api/clips/{clip_id}")
async def delete_clip(clip_id: str, device=Depends(get_current_device)):
    db.delete_clip(clip_id)
    return {"ok": True}


@app.get("/api/clips/{clip_id}/download")
async def download_clip_file(clip_id: str, device=Depends(get_current_device)):
    conn = db.get_db()
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.close()
    if not row or row["type"] != "file" or not row["filepath"]:
        raise HTTPException(404, "File not found")
    return FileResponse(row["filepath"], filename=row["filename"], media_type=row["mimetype"] or "application/octet-stream")


# --- SSE ---

@app.get("/api/events")
async def sse_events(device=Depends(get_current_device)):
    device_id = device["id"]
    q = notify.add_sse_client(device_id)

    async def event_stream():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            notify.remove_sse_client(device_id, q)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# --- Devices ---

@app.get("/api/devices")
async def get_devices(device=Depends(get_current_device)):
    return db.list_devices()


@app.delete("/api/devices/{device_id}")
async def remove_device(device_id: str, device=Depends(get_current_device)):
    db.delete_device(device_id)
    return {"ok": True}


# --- Push subscriptions ---

@app.post("/api/push/subscribe")
async def push_subscribe(request: Request, device=Depends(get_current_device)):
    body = await request.json()
    db.set_push_subscription(device["id"], json.dumps(body))
    return {"ok": True}


@app.delete("/api/push/subscribe")
async def push_unsubscribe(device=Depends(get_current_device)):
    db.set_push_subscription(device["id"], None)
    return {"ok": True}


@app.get("/api/push/vapid-key")
async def get_vapid_key():
    key = os.environ.get("VAPID_PUBLIC_KEY", "")
    return {"key": key}


# --- Static files (must be last) ---

app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "public"), html=True), name="static")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CopyServer")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--ssl-certfile", default=os.environ.get("SSL_CERTFILE"))
    parser.add_argument("--ssl-keyfile", default=os.environ.get("SSL_KEYFILE"))
    args = parser.parse_args()

    kwargs = {"host": args.host, "port": args.port}
    if args.ssl_certfile and args.ssl_keyfile:
        kwargs["ssl_certfile"] = args.ssl_certfile
        kwargs["ssl_keyfile"] = args.ssl_keyfile
    uvicorn.run(app, **kwargs)
