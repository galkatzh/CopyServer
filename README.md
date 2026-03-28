# CopyServer

Share text and files between your devices. Self-hosted, real-time, installable as a PWA.

## Quick start

```
cp .env.example .env   # edit PASSPHRASE at minimum
uv run server.py
```

Open `http://localhost:8080`, enter your passphrase and a device name. Repeat on your other devices.

## Requirements

Python 3.11+. All dependencies are declared inline in `server.py`, so `uv run server.py` handles everything automatically.

Without uv:

```
pip install -r requirements.txt
python server.py
```

## Configuration

All settings go in `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `PASSPHRASE` | `changeme` | Shared secret for pairing devices |
| `PORT` | `8080` | Server port |
| `UPLOAD_MAX_MB` | `100` | Max file upload size in MB |
| `CLIP_EXPIRE_DAYS` | `7` | Auto-delete clips after this many days |
| `VAPID_PRIVATE_KEY` | | Private key for push notifications |
| `VAPID_PUBLIC_KEY` | | Public key for push notifications |
| `VAPID_CONTACT` | `mailto:you@example.com` | Contact for VAPID (email or URL) |
| `SSL_CERTFILE` | | Path to TLS certificate file |
| `SSL_KEYFILE` | | Path to TLS private key file |

TLS can also be set via CLI flags: `uv run server.py --ssl-certfile cert.pem --ssl-keyfile key.pem`

## Using with Tailscale

If your devices are on a Tailscale network, you don't need a reverse proxy. Tailscale encrypts traffic between devices, but you still need HTTPS for PWA install and push notifications to work.

1. Enable HTTPS on your Tailscale node:

   ```
   tailscale cert your-machine.tailnet-name.ts.net
   ```

   This creates `your-machine.tailnet-name.ts.net.crt` and `your-machine.tailnet-name.ts.net.key` in the current directory.

2. Run the server with TLS:

   ```
   uv run server.py \
     --ssl-certfile your-machine.tailnet-name.ts.net.crt \
     --ssl-keyfile your-machine.tailnet-name.ts.net.key \
     --port 443
   ```

   Or put the paths in `.env`:

   ```
   SSL_CERTFILE=your-machine.tailnet-name.ts.net.crt
   SSL_KEYFILE=your-machine.tailnet-name.ts.net.key
   PORT=443
   ```

3. Open `https://your-machine.tailnet-name.ts.net` on your devices.

Note: Tailscale certs auto-renew, but you'll need to restart the server (or re-run `tailscale cert`) when they rotate.

Without HTTPS, everything still works over `http://your-machine:8080` — you just won't get push notifications when the app is closed, and PWA install may not be available.

## Quick sharing

### Clipboard button

The app has a "Clipboard" button that reads your clipboard and shares it (text or images) in one tap.

### iOS Shortcut

Create a Shortcut to share to CopyServer from the iOS Share Sheet:

1. Open the **Shortcuts** app
2. Create a new Shortcut
3. Add action: **Receive** input from **Share Sheet** (accept Text, URLs, Images, Files)
4. Add an **If** action: check if *Shortcut Input* **has any value**
5. Inside the If:
   - For text: Add **Get Text from Input**, then **Get Contents of URL**:
     - URL: `https://YOUR_HOST:PORT/api/clips/text`
     - Method: POST
     - Headers: `Authorization: Bearer YOUR_TOKEN`, `Content-Type: application/json`
     - Body (JSON): `{"content": "Shortcut Input"}`
   - For files/images: Add **Get Contents of URL**:
     - URL: `https://YOUR_HOST:PORT/api/clips/file`
     - Method: POST
     - Headers: `Authorization: Bearer YOUR_TOKEN`
     - Body: Form, key `file` = *Shortcut Input*
6. Name the shortcut "CopyServer" and enable **Show in Share Sheet**

To find your token, open the browser console on iOS and run:
```js
localStorage.getItem("copyserver_token")
```

### Linux script

Save as `copyserver-share.sh` and bind to a keyboard shortcut:

```bash
#!/bin/bash
HOST="https://your-machine.tailnet-name.ts.net:8443"
TOKEN="your-token-here"

# Share clipboard text
TEXT=$(xclip -selection clipboard -o 2>/dev/null || xsel --clipboard --output 2>/dev/null)
if [ -n "$TEXT" ]; then
    curl -s -X POST "$HOST/api/clips/text" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"content\": $(echo "$TEXT" | jq -Rs .)}" > /dev/null
    notify-send "CopyServer" "Clipboard shared"
fi
```

### Windows PowerShell script

Save as `copyserver-share.ps1` and bind to a global hotkey (e.g. via AutoHotkey):

```powershell
$Host_ = "https://your-machine.tailnet-name.ts.net:8443"
$Token = "your-token-here"

$text = Get-Clipboard -Raw
if ($text) {
    $body = @{ content = $text } | ConvertTo-Json
    $headers = @{
        "Authorization" = "Bearer $Token"
        "Content-Type" = "application/json"
    }
    Invoke-RestMethod -Uri "$Host_/api/clips/text" -Method POST -Headers $headers -Body $body
    [System.Windows.Forms.MessageBox]::Show("Clipboard shared", "CopyServer")
}
```

## Push notifications

Push notifications alert your devices when the app is closed. They require VAPID keys and HTTPS.

Generate keys:

```
python -c "from py_vapid import Vapid; v = Vapid(); v.generate_keys(); print('VAPID_PRIVATE_KEY=' + v.private_pem().decode().strip()); print('VAPID_PUBLIC_KEY=' + v.public_key_urlsafe_base64())"
```

Or with openssl:

```
openssl ecparam -genkey -name prime256v1 -out vapid_private.pem
openssl ec -in vapid_private.pem -pubout -out vapid_public.pem
```

Add the keys to `.env`.

## Installing as a PWA

- **Android**: Open the app in Chrome, tap the menu, select "Add to Home Screen" or "Install app"
- **iOS**: Open in Safari, tap the share button, select "Add to Home Screen"
- **Desktop**: In Chrome/Edge, click the install icon in the address bar

The installed app works in standalone mode (no browser chrome) and receives push notifications.

## Deploying with a reverse proxy

If you prefer a reverse proxy over terminating TLS in the server directly:

```nginx
server {
    listen 443 ssl;
    server_name copy.example.com;

    ssl_certificate /etc/letsencrypt/live/copy.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/copy.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;           # required for SSE
        proxy_cache off;
        proxy_read_timeout 86400s;     # keep SSE connections alive
    }
}
```

## How it works

- Devices pair by entering a shared passphrase. Each device gets a long-lived auth token stored in the browser.
- Sharing text or uploading a file sends it to the server, which stores metadata in SQLite and files on disk.
- Other devices receive the new clip instantly via Server-Sent Events (SSE).
- If a device has the app closed, it gets a Web Push notification instead.
- Expired clips are cleaned up automatically every hour.

## Data storage

- Database: `data/copyserver.db` (SQLite)
- Uploaded files: `uploads/`

Both directories are created automatically and excluded from git.
