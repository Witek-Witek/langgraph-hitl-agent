import os

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


load_dotenv()

from llm import config_banner
from db import checkpointer_pooled
from graph import build_graph
from server import chat
from db import make_checkpointer, open_pool 
from server import chat, threads
from threads_repo import ThreadsRepo


def _cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with open_pool() as pool:
        cp = await make_checkpointer(pool)
        app.state.graph = build_graph(checkpointer=cp)
        app.state.threads = ThreadsRepo(pool)
        print(config_banner())
        print("Graf skompilowany, pula otwarta, rejestr gotowy.")
        yield


app = FastAPI(title="LangGraph Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(threads.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
