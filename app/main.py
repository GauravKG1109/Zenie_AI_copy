from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.routes import chat


app = FastAPI(
    title = "Zenie Finance AI",
    version = "Demo"
)
# version is more for documentation and tracking changes, it doesn't affect the functionality of the API

# Include the chat router
app.include_router(chat.router, prefix= "/api/v1/chat", tags=["Chat"])

app.mount("/static", StaticFiles(directory="app/static"), name="static")  # For serving static files if needed

@app.get("/")
def serve_ui():
    return FileResponse("app/static/index.html")  # Serve the UI from the static directory