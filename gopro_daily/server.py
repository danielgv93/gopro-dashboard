"""HTTP API + static web UI."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import threading
import webbrowser
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .config import AppConfig, load_config, save_config
from .edits import OVERLAY_MIN_SECONDS, Cut, EditStore, edit_summary, make_edit, plan_pieces
from .ingest import FootageCopy, copy_origin
from .jobs import JobManager, reveal_in_finder
from .layout import build_widgets
from .pipeline import dashboard_bin, ffmpeg_bin, work_root
from .scanner import Chapter, Group, scan

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="GoPro Daily")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def revalidate_ui(request, call_next):
    # without it the browser keeps running an old app.js after an update (heuristic caching on Last-Modified)
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

state: dict = {"config": load_config(), "groups": {}, "media": {}}
THUMB_CACHE = Path(tempfile.gettempdir()) / "gopro-daily-thumbs"
jobs = JobManager()
edits = EditStore()
footage_copy = FootageCopy()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# ----------------------------------------------------------------------------- config


@app.get("/api/config")
def get_config() -> dict:
    return state["config"].model_dump()


@app.put("/api/config")
def put_config(payload: dict) -> dict:
    try:
        config = AppConfig.model_validate(payload)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False)) from e
    state["config"] = config
    save_config(config)
    return config.model_dump()


@app.post("/api/config/reset")
def reset_config() -> dict:
    current: AppConfig = state["config"]
    config = AppConfig()
    config.source.input_dir, config.output.output_dir = current.source.input_dir, current.output.output_dir
    state["config"] = config
    save_config(config)
    return config.model_dump()


@app.get("/api/options")
def options() -> dict:
    from gopro_overlay import geo

    return {"map_styles": geo.available_map_styles(), "overlay_min_seconds": OVERLAY_MIN_SECONDS}


@app.get("/api/layout-preview")
def layout_preview(width: int = 1920, height: int = 1080) -> dict:
    """Exact widget geometry (same code path as rendering) for the interactive preview."""
    config: AppConfig = state["config"]
    mode = config.overlay.layout_mode
    if mode == "xml":
        path = Path(config.overlay.layout_xml_path).expanduser()
        return {"mode": mode, "xml": path.read_text() if path.is_file() else "<layout/>", "widgets": []}
    widgets = [] if mode == "builtin" else [w.to_dict() for w in build_widgets(config.overlay, width, height)]
    return {"mode": mode, "widgets": widgets}


@app.get("/api/font")
def overlay_font() -> FileResponse:
    """Serves the overlay font so the preview draws text with the same glyphs as the render."""
    path = Path(state["config"].overlay.font).expanduser()
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="font/ttf")


# ----------------------------------------------------------------------------- scanning & jobs


@app.post("/api/scan")
def do_scan() -> dict:
    config: AppConfig = state["config"]
    try:
        groups = scan(config)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    state["groups"] = {g.key: g for g in groups}
    # Only files found by the last scan can be previewed; the browser addresses them by opaque id, never by path.
    state["media"] = {c.media_id: c for g in groups for r in g.recordings + g.duplicates for c in r.chapters}
    return {"groups": [{**g.to_dict(), "edit": edit_summary(g, edits.get(g.key))} for g in groups]}


# ----------------------------------------------------------------------------- local footage copy


def _footage_root() -> Path:
    return work_root(state["config"]) / "footage"


def _local_copy() -> Path | None:
    """The copy folder the current source lives in, or None when the source is not a copy on this Mac."""
    source, root = Path(state["config"].source.input_dir).expanduser(), _footage_root()
    if not state["config"].source.input_dir or source == root or not source.is_relative_to(root):
        return None
    return root / source.relative_to(root).parts[0]


@app.get("/api/copy")
def copy_status() -> dict:
    status = {**footage_copy.state, "footage_root": str(_footage_root())}
    local = _local_copy()
    if local and local.is_dir():
        origin = copy_origin(local)
        status["local"] = {"path": str(local), "size": sum(f.stat().st_size for f in local.rglob("*") if f.is_file()),
                           "origin": str(origin or ""), "origin_exists": bool(origin and origin.is_dir())}
    return status


@app.post("/api/copy")
def start_copy() -> dict:
    """Copies the scanned files next to the export work files; when done, the copy becomes the source folder."""
    config: AppConfig = state["config"]
    source = Path(config.source.input_dir).expanduser()
    if not state["media"]:
        raise HTTPException(400, "Scan the footage first")
    if source.is_relative_to(_footage_root()):
        raise HTTPException(400, "This footage is already on this Mac")

    def done(src: Path, dest: Path) -> None:
        current: AppConfig = state["config"]
        if Path(current.source.input_dir).expanduser() == src:  # the user may have picked another folder meanwhile
            current.source.input_dir = str(dest)
            save_config(current)
        edits.move_footage(str(src), str(dest))

    try:
        footage_copy.start(list(state["media"].values()), source, _footage_root() / (source.name or "footage"), done)
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e)) from e
    return copy_status()


@app.delete("/api/copy")
def delete_copy() -> dict:
    """Deletes the local copy in use and points the source back at the folder it was copied from."""
    if footage_copy.running:
        raise HTTPException(400, "Wait for the copy to finish or cancel it first")
    local = _local_copy()
    if not local or not local.is_dir():
        raise HTTPException(400, "The source folder is not a copy on this Mac")
    origin = copy_origin(local)
    shutil.rmtree(local)
    config: AppConfig = state["config"]
    config.source.input_dir = str(origin) if origin and origin.is_dir() else ""
    save_config(config)
    if origin:
        edits.move_footage(str(local), str(origin))
    state["groups"], state["media"] = {}, {}
    return config.model_dump()


@app.post("/api/copy/open")
def open_copy() -> dict:
    """Opens the local copy in Finder: the one in use, else the one being (or last) copied."""
    folder = _local_copy() or (Path(footage_copy.state["dest"]) if footage_copy.state.get("dest") else None)
    if not folder or not folder.is_dir():
        raise HTTPException(404, "There is no copy on this Mac")
    subprocess.Popen(["open", str(folder)])
    return {"ok": True}


def _exports_cache() -> Path:
    return work_root(state["config"]) / "exports"


@app.get("/api/cache")
def cache_status() -> dict:
    folder = _exports_cache()
    return {"path": str(folder), "size": sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.is_dir() else 0}


@app.delete("/api/cache")
def clear_cache() -> dict:
    """Deletes leftover export work files (cancelled, failed or kept runs). Footage copies are left alone."""
    if any(j.status in ("queued", "running") for j in jobs.jobs.values()):
        raise HTTPException(400, "Wait for the exports to finish or cancel them first")
    shutil.rmtree(_exports_cache(), ignore_errors=True)
    return cache_status()


@app.post("/api/copy/cancel")
def cancel_copy() -> dict:
    footage_copy.cancel()
    return copy_status()


# ----------------------------------------------------------------------------- film edits (footage editor)


def _group(key: str) -> Group:
    group = state["groups"].get(key)
    if not group:
        raise HTTPException(404, "Unknown film (scan again)")
    return group


class EditRequest(BaseModel):
    cuts: list[Cut]


@app.put("/api/groups/{key}/edit")
def put_edit(key: str, req: EditRequest) -> dict:
    """Saves the editor's cuts for a film. They are applied on export; the source files are never modified."""
    group = _group(key)
    edits.put(key, make_edit(group, req.cuts))
    return edit_summary(group, edits.get(key))


@app.delete("/api/groups/{key}/edit")
def reset_edit(key: str) -> dict:
    group = _group(key)
    edits.delete(key)
    return edit_summary(group, None)


# ----------------------------------------------------------------------------- previews

PREVIEW_CACHE = {"Cache-Control": "private, max-age=3600"}


def _media(media_id: str) -> Chapter:
    chapter = state["media"].get(media_id)
    if not chapter:
        raise HTTPException(404, "Unknown clip (scan again)")
    return chapter


@app.get("/api/media/{media_id}/video")
def media_video(media_id: str) -> FileResponse:
    """The camera's low-res proxy (.LRV) when present — tiny and H.264, so any browser plays it — else the chapter."""
    chapter = _media(media_id)
    path = chapter.lrv or chapter.path
    if not path.is_file():
        raise HTTPException(404, "File no longer available")
    return FileResponse(path, media_type="video/mp4", headers=PREVIEW_CACHE)


@app.get("/api/media/{media_id}/thumb")
def media_thumb(media_id: str) -> FileResponse:
    """The camera's .THM JPEG when present; otherwise one frame grabbed with ffmpeg (from the proxy if possible)."""
    chapter = _media(media_id)
    if chapter.thm and chapter.thm.is_file():
        return FileResponse(chapter.thm, media_type="image/jpeg", headers=PREVIEW_CACHE)
    THUMB_CACHE.mkdir(exist_ok=True)
    target = THUMB_CACHE / f"{media_id}.jpg"
    if not target.is_file():
        source = chapter.lrv or chapter.path
        seek = "1" if chapter.duration > 2 else "0"
        proc = subprocess.run(
            [ffmpeg_bin(state["config"]), "-v", "error", "-ss", seek, "-i", str(source), "-frames:v", "1",
             "-vf", "scale=320:-2", "-q:v", "4", "-y", str(target)],
            capture_output=True, timeout=30,
        )
        if proc.returncode != 0 or not target.is_file():
            raise HTTPException(404, "Could not extract a thumbnail")
    return FileResponse(target, media_type="image/jpeg", headers=PREVIEW_CACHE)


class JobRequest(BaseModel):
    keys: list[str]


@app.post("/api/jobs")
def create_jobs(req: JobRequest) -> list[dict]:
    config: AppConfig = state["config"]
    if not config.output.output_dir:
        raise HTTPException(400, "Choose an output folder first")
    groups: dict[str, Group] = state["groups"]
    missing = [k for k in req.keys if k not in groups]
    if missing:
        raise HTTPException(400, f"Unknown groups (scan again): {missing}")
    plans = {k: plan_pieces(groups[k], edits.get(k)) for k in req.keys}
    empty = [groups[k].label for k, pieces in plans.items() if not pieces]
    if empty:
        raise HTTPException(400, f"Nothing left to export in {', '.join(empty)}: every clip was removed in the editor")
    return [jobs.submit(groups[k], config, plans[k]).summary() for k in req.keys]


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return [j.summary() for j in sorted(jobs.jobs.values(), key=lambda j: j.id, reverse=True)]


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: int, since: int = 0) -> dict:
    job = jobs.jobs.get(job_id)
    if not job:
        raise HTTPException(404)
    lines = list(job.logs)
    start = max(0, since - job.log_offset)
    return {"next": job.log_offset + len(lines), "lines": lines[start:]}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict:
    if job_id not in jobs.jobs:
        raise HTTPException(404)
    jobs.cancel(job_id)
    return jobs.jobs[job_id].summary()


@app.delete("/api/jobs")
def clear_jobs() -> dict:
    jobs.clear_finished()
    return {"ok": True}


# ----------------------------------------------------------------------------- macOS helpers


class PickRequest(BaseModel):
    prompt: str = "Choose a folder"
    kind: str = "folder"  # folder | file


@app.post("/api/pick")
def pick(req: PickRequest) -> dict:
    """Opens the native macOS Finder dialog (the browser can't give us absolute paths)."""
    what = "choose folder" if req.kind == "folder" else "choose file"
    prompt = req.prompt.replace('"', "'")
    script = f'POSIX path of ({what} with prompt "{prompt}")'
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return {"path": None}
    return {"path": proc.stdout.strip().rstrip("/") or "/"}


class RevealRequest(BaseModel):
    path: str


@app.post("/api/reveal")
def reveal(req: RevealRequest) -> dict:
    if not Path(req.path).exists():
        raise HTTPException(404, "File not found")
    reveal_in_finder(req.path)
    return {"ok": True}


@app.get("/api/doctor")
def doctor() -> list[dict]:
    config: AppConfig = state["config"]
    checks = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    ffmpeg = shutil.which(ffmpeg_bin(config)) or (ffmpeg_bin(config) if Path(ffmpeg_bin(config)).exists() else None)
    check("ffmpeg", bool(ffmpeg), ffmpeg or "Not found — brew install ffmpeg")
    if ffmpeg:
        encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        check("VideoToolbox (Apple GPU encoding)", "hevc_videotoolbox" in encoders,
              "Available" if "hevc_videotoolbox" in encoders else "Your ffmpeg lacks VideoToolbox encoders")
        check(f"Encoder {config.encoding.codec}", config.encoding.codec in encoders,
              "Available" if config.encoding.codec in encoders else "Not supported by this ffmpeg build")
    dash = dashboard_bin()
    check("gopro-dashboard-overlay", bool(shutil.which(dash) or Path(dash).exists()), dash)
    check("Overlay font", Path(config.overlay.font).exists() or not config.overlay.enabled, config.overlay.font)
    try:
        ZoneInfo(config.grouping.timezone)
        check("Timezone", True, config.grouping.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        check("Timezone", False, f"Unknown timezone '{config.grouping.timezone}'")
    if config.overlay.layout_mode == "xml":
        p = Path(config.overlay.layout_xml_path).expanduser()
        check("Custom layout XML", p.is_file(), str(p))
    return checks


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="GoPro Daily web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
