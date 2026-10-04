import asyncio
import argparse
import sys
from app.logging_config import setup_logging
from app.monitoring.watcher import PlacementWatcher

import uvicorn
from app.api.app import app
from app.config import settings

def get_uvicorn_config(app_obj=None) -> uvicorn.Config:
    return uvicorn.Config(
        app=app_obj or app,
        host=settings.PUBLIC_HOST,
        port=settings.PUBLIC_PORT,
        log_level=settings.LOG_LEVEL.lower(),
        access_log=False,
        server_header=False,
    )

async def main():
    setup_logging()
    
    parser = argparse.ArgumentParser(description="VIT TPO Placement Watcher")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit")
    parser.add_argument("--baseline", action="store_true", help="Force baseline initialization")
    
    args = parser.parse_args()
    
    watcher = PlacementWatcher()
    
    try:
        if args.baseline:
            watcher.db.set_baseline_initialized()
            
        if args.once:
            await watcher.check_once()
        else:
            config = get_uvicorn_config()
            server = uvicorn.Server(config)
            await asyncio.gather(
                watcher.run_forever(),
                server.serve()
            )
    except KeyboardInterrupt:
        pass
    finally:
        await watcher.shutdown()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
