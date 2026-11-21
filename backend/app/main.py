"""RDB API entrypoint. Run: uvicorn app.main:app --reload --port 8000 (from backend/)."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes_core import router as core_router
from app.routes_gbq import router as gbq_router

app = FastAPI(title="RDB API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(core_router)
app.include_router(gbq_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
