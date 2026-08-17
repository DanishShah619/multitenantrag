from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routers import ingestion, chat, demo

app = FastAPI(title="Wizz AI Backend", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before production - see note below
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingestion.router)
app.include_router(chat.router)
app.include_router(demo.router)


@app.on_event("startup")
def on_startup():
    # For Phase 1 this is fine. Once the schema stabilizes, switch to
    # Alembic migrations instead of create_all so schema changes are
    # tracked and reversible.
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}


from pathlib import Path
from fastapi.staticfiles import StaticFiles

# Mount frontend web app
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

