from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import STORAGE_DIR
from .routes.entries import router as entries_router

app = FastAPI(title="Commonplace API")

app.include_router(entries_router)
app.mount("/files", StaticFiles(directory=STORAGE_DIR), name="files")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/")
def upload_page():
    return FileResponse(STATIC_DIR / "upload.html")
