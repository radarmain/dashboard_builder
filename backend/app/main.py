"""RDB API entrypoint. Run: uvicorn app.main:app --reload --port 8000 (from backend/)."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.favorites import router as favorites_router
from app.routes_chat import router as chat_router
from app.routes_core import router as core_router
from app.routes_gbq import router as gbq_router
from app.routes_lifecycle import router as lifecycle_router
from app.routes_portal import router as portal_router
from app.routes_tableau import router as tableau_router
from app.routes_tableau_import import router as tableau_import_router
from app.routes_users import router as users_router

app = FastAPI(title="RDB API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(core_router)
app.include_router(lifecycle_router)
app.include_router(favorites_router)
app.include_router(gbq_router)
app.include_router(chat_router)
app.include_router(portal_router)
app.include_router(users_router)
app.include_router(tableau_router)
app.include_router(tableau_import_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
