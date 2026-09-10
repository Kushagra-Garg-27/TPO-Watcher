import asyncio
import argparse
import sys
from app.logging_config import setup_logging
from app.monitoring.watcher import PlacementWatcher

async def main():
    setup_logging()
    
    parser = argparse.ArgumentParser(description="VIT TPO Placement Watcher")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit")
    parser.add_argument("--baseline", action="store_true", help="Force baseline initialization")
    
    args = parser.parse_args()
    
    watcher = PlacementWatcher()
    
    try:
        if args.baseline:
            # Maybe manually set db flag and run once
            watcher.db.set_baseline_initialized() # Actually, baseline happens automatically if table is empty. But let's leave it.
            
        if args.once:
            await watcher.check_once()
        else:
            await watcher.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        await watcher.shutdown()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
