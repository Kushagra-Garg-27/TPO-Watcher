import asyncio
import argparse
import sys
from app.logging_config import setup_logging
from app.monitoring.watcher import PlacementWatcher

import uvicorn
from app.api.app import app
from app.config import settings


def create_application(*,runtime=None):
    """Construction only. A supplied experiment is not a startup request."""
    from app.api.app import create_app
    from app.monitoring.runtime_supervisor import RuntimeSupervisor
    from app.monitoring.production_runtime import ProductionRuntime
    if runtime is not None and not isinstance(runtime,(RuntimeSupervisor,ProductionRuntime)):
        raise ValueError('explicit guarded runtime required')
    application=create_app()
    application.state.notification_runtime=runtime
    if runtime is not None and runtime.database is not None and callable(runtime.clock):
        application.state.opportunity_database=runtime.database.db_path
        application.state.opportunity_clock=runtime.clock
    return application


async def run_application(runtime,stop,*,poll_interval_ms=60000):
    """Explicit capabilities only; API construction never starts this lifecycle."""
    from app.monitoring.runtime_supervisor import RuntimeSupervisor
    from app.monitoring.production_runtime import ProductionRuntime
    if not isinstance(runtime,(RuntimeSupervisor,ProductionRuntime)):raise ValueError('explicit runtime required')
    return await runtime.run(stop,poll_interval_ms=poll_interval_ms)


async def run_guarded_once(runtime,*,poll=True,drain=True):
    """Explicit isolated entry point; never selected by command-line/environment."""
    from app.monitoring.runtime_supervisor import RuntimeSupervisor
    if not isinstance(runtime,RuntimeSupervisor):raise ValueError('explicit guarded runtime required')
    runtime.start()
    result=await runtime.poll_once() if poll else True
    if drain:await runtime.drain_once()
    return result

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
                watcher.run_pruning_loop(),
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
