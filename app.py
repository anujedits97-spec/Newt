"""Standalone TeraBox public-share resolver API."""
import logging
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import config
import terabox_resolver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("terabox_api")

app = FastAPI(
    title="Ak TeraBox Resolver API",
    description="Standalone TeraBox public-share resolver API.",
    version="1.0.0",
)

_hits: dict[str, deque] = defaultdict(deque)


def _check_rate_limit(client_ip: str):
    now = time.time()
    window = _hits[client_ip]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= config.RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded, try again in a bit.")
    window.append(now)


@app.middleware("http")
async def rate_limit_mw(request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    try:
        _check_rate_limit(client_ip)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": False, "error": exc.detail},
        )
    return await call_next(request)


@app.get("/", include_in_schema=False)
def root():
    return {
        "status": True,
        "creator": "Ak",
        "message": "Ak TeraBox API is online",
        "version": "1.0.0",
        "provider": "TeraBox",
        "endpoints": {
            "terabox": "/api/terabox?url=<TERABOX_SHARE_URL>",
            "unified": "/api?url=<TERABOX_SHARE_URL>",
            "health": "/health",
        },
    }


@app.get("/health")
def health():
    return {"status": True, "service": "ak-terabox-api", "providers": ["TeraBox"]}


@app.get("/api/terabox")
async def terabox(
    url: str = Query(..., description="TeraBox public share link"),
    cookie: str | None = Query(
        None,
        description="Optional upstream Cookie header; public shares do not normally require it",
    ),
):
    if not terabox_resolver.is_terabox_link(url):
        return JSONResponse(
            status_code=400,
            content={
                "status": False,
                "error": "Only TeraBox share links are supported here.",
            },
        )

    try:
        data = await run_in_threadpool(
            terabox_resolver.resolve_terabox,
            url,
            cookie,
        )
    except terabox_resolver.TeraBoxUpstreamError as exc:
        logger.warning("TeraBox upstream failed: %s", exc)
        errno = next(
            (
                attempt.get("errno")
                for attempt in exc.attempts
                if attempt.get("errno") not in (None, 0, "0")
            ),
            -1,
        )
        return JSONResponse(
            status_code=502,
            content={
                "status": False,
                "error": str(exc),
                "errno": errno,
                "attempts": exc.attempts,
            },
        )
    except Exception as exc:
        logger.exception("TeraBox resolve failed")
        return JSONResponse(
            status_code=502,
            content={"status": False, "error": str(exc)},
        )

    return {"status": True, "creator": "Ak", "data": data}


@app.get("/api")
async def unified(
    url: str = Query(..., description="TeraBox public share link"),
    cookie: str | None = Query(None, description="Optional TeraBox Cookie header"),
):
    return await terabox(url=url, cookie=cookie)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=config.PORT, reload=False)
