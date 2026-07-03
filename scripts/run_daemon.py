"""Headless launcher — boots AgentMaxRuntime and blocks on wait_for_shutdown.

No REPL, no stdin reads. Used by background processes / tests where we just
want the runtime up so the IPC endpoints are reachable.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> None:
    from core.runtime import AgentMaxRuntime

    rt = AgentMaxRuntime()
    await rt.start()
    print("[run_daemon] runtime ready. Press Ctrl+C or hit /api/shutdown to stop.", flush=True)
    try:
        await rt.wait_for_shutdown()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await rt.shutdown()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
