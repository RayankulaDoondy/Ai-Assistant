"""Windows-stable local launcher for Hunt.

Why this exists
---------------
On Python 3.10+ Windows, asyncio defaults to `WindowsProactorEventLoopPolicy`
which has a known bug: after a TCP connection reset (e.g. a browser closing
mid-stream), the event loop sometimes deadlocks on the next socket write.
Uvicorn keeps accepting new connections at the OS level (so netstat shows
LISTENING) but never hands them to the worker.

That's why we kept seeing:
    INFO: Application startup complete.
    INFO: Uvicorn running on http://127.0.0.1:8001
    <silence>
    <browser shows ERR_CONNECTION_REFUSED>

The fix is to use `WindowsSelectorEventLoopPolicy` instead, which behaves
identically to the Linux default and doesn't have the proactor hang.

Uvicorn's `--loop asyncio` flag picks the loop *implementation* but does NOT
override the *policy*, so we have to do it ourselves BEFORE importing uvicorn.

Usage
-----
    python run_local.py

or just double-click `run.bat`.
"""
import sys

# CRITICAL: must run before any `import asyncio` elsewhere creates a loop.
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Now safe to bring in the rest.
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8001,
        loop="asyncio",   # uses the policy we set above
        http="h11",        # h11 handles disconnects more gracefully than httptools
        access_log=True,
        log_level="info",
    )
