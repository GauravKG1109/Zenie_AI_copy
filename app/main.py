from dotenv import load_dotenv
load_dotenv()

import logging
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.routes import chat, admin
from core.database import get_db, engine


logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zenie Finance AI",
    version="Demo"
)

# Include the chat router
app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat"])
app.include_router(admin.router, tags=["Admin"])

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Establish database connection and create tables on startup
try:
    # Test the database connection
    with engine.connect() as connection:
        logger.info("[Startup] Successfully connected to the database.")
    # Create tables if they don't exist
    from core.database import Base  # Import Base after engine is defined
    Base.metadata.create_all(bind=engine)
    logger.info("[Startup] Database tables created or verified successfully.")
except Exception as e:
    logger.error(f"[Startup] Database connection failed: {e}")
    

@app.on_event("startup")
async def preload_pipeline():
    """
    Pre-load the LangGraph pipeline (and build/cache embeddings) during server
    startup so that the first user request is not delayed by embedding generation.

    Importing services.graph.graph triggers:
      1. intent_classifier.py  → loads SentenceTransformer + builds/loads embeddings
      2. graph.py              → compiles the StateGraph
    All of this runs before the server starts accepting requests.
    """
    import asyncio
    loop = asyncio.get_event_loop()

    def _load():
        from services.graph.graph import pipeline  # noqa: F401 — side-effect import
        logger.info("[Startup] LangGraph pipeline pre-loaded and embeddings ready.")

    # Run in executor so CPU-bound embedding build doesn't block the event loop
    await loop.run_in_executor(None, _load)


@app.get("/")
def serve_ui():
    return FileResponse("app/static/index.html")
