# Ak TeraBox API — Standalone

Standalone FastAPI resolver for public TeraBox share links.


## Endpoints

```text
GET /health
GET /api/terabox?url=<TERABOX_SHARE_URL>
GET /api?url=<TERABOX_SHARE_URL>
```

Optional fallback cookie:

```text
GET /api/terabox?url=<TERABOX_SHARE_URL>&cookie=<COOKIE>
```

Cookies are not required by the application at startup. Public-share resolution is attempted from the share page first. Some TeraBox shares may still be rejected upstream because of access, verification, region, password, or authorization requirements.

## Response

```json
{
  "status": true,
  "creator": "Ak",
  "data": {
    "title": "video.mp4",
    "thumbnail": "https://...",
    "size": "131.02 MB",
    "size_bytes": 137388129,
    "extension": "mp4",
    "category": "Video",
    "download_url": "https://...",
    "dlink": "https://...",
    "fs_id": "...",
    "files": []
  }
}
```

## Local

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Docker / Render

The included Dockerfile uses Python 3.11 and respects Render's `PORT` variable.

