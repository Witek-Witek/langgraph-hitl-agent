import asyncio
import logging

import uvicorn

from runtime_env import selector_loop_factory


async def _serve() -> None:
    config = uvicorn.Config("server.app:app", host="127.0.0.1", port=8000, log_level="info")
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    asyncio.run(_serve(), loop_factory=selector_loop_factory())