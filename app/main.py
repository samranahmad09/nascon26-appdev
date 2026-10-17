import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .core import db, errors, realtime
from .core import routes as core_routes
from .core import seed as _core_seed  # noqa: F401  (registers core tables)
from .core.middleware import BodySizeLimit, RateLimit
from .modules import MODULES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def startup() -> None:
    db.init()
    core_routes.create_schema()
    if not core_routes.is_seeded():
        core_routes.reset_state()


@asynccontextmanager
async def lifespan(_: FastAPI):
    realtime.bind_loop(asyncio.get_running_loop())
    await asyncio.to_thread(startup)
    yield
    db.close()


app = FastAPI(title="NaSCon 26 League Platform", lifespan=lifespan)
errors.install(app)
app.add_middleware(RateLimit)
app.add_middleware(BodySizeLimit)
app.include_router(core_routes.router)
for module in MODULES:
    app.include_router(module.router)
