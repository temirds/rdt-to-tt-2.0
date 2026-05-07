from __future__ import annotations

import argparse
import json
import mimetypes
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from collector.config import load_collector_settings
from downloader.api import available_providers, download, fetch_info
from downloader.base import DownloadOptions
from downloader.editor import cut_video, export_timeline, list_videos, resolve_media_path, trim_video
from downloader.cookies import convert_json_cookies
from downloader.web import build_config_payload, prepare_cookies_payload, summarize_info
from video.config import DEFAULT_CONFIG_PATH as DEFAULT_VIDEO_CONFIG_PATH, load_video_settings


ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8780
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

CONFIGS = {
    "collector": ROOT / "collector" / "config.json",
    "translator": ROOT / "translator" / "config.json",
    "voiceover": ROOT / "voiceover" / "configs" / "cosyvoice.json",
    "voices": ROOT / "voiceover" / "configs" / "voices.json",
    "video": ROOT / "video" / "configs" / "video.json",
    "downloader": ROOT / "downloader" / "configs" / "downloader.json",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified web UI for rdt-to-tt.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), StudioHandler)
    print(f"Web UI: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "RdtToTtStudio/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/":
                self._html(APP_HTML)
            elif path == "/api/bootstrap":
                self._json(build_bootstrap())
            elif path == "/api/jobs":
                self._json({"jobs": list_jobs()})
            elif path.startswith("/api/jobs/"):
                self._json(get_job(path.rsplit("/", 1)[-1]))
            elif path == "/api/reddit/posts":
                self._json({"posts": list_reddit_posts(query)})
            elif path.startswith("/api/reddit/posts/"):
                self._json({"post": get_reddit_post(int(path.rsplit("/", 1)[-1]))})
            elif path == "/api/voices":
                self._json({"voices": load_voices(), "default": "upvote_3"})
            elif path.startswith("/api/config/"):
                self._json({"config": load_named_config(path.rsplit("/", 1)[-1])})
            elif path == "/api/downloader/config":
                self._json(build_config_payload())
            elif path == "/api/downloader/providers":
                self._json({"providers": list(available_providers())})
            elif path == "/api/downloader/info":
                url = first(query, "url")
                if not url:
                    raise ValueError("url is required")
                info = fetch_info(url, provider=first(query, "provider") or "youtube")
                self._json(summarize_info(info))
            elif path == "/api/editor/videos":
                self._json({"videos": list_videos(first(query, "dir") or None)})
            elif path == "/api/editor/media":
                media_path = first(query, "path")
                if not media_path:
                    raise ValueError("path is required")
                self._send_file(resolve_media_path(media_path))
            elif path == "/api/media/videos":
                self._json(build_media_control_payload())
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._error(exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self._read_json()
            if path == "/api/generate":
                self._json(start_generate_job(payload))
            elif path == "/api/reddit/collect":
                self._json(start_collector_job(payload))
            elif path.startswith("/api/config/"):
                name = path.rsplit("/", 1)[-1]
                save_named_config(name, payload.get("config", payload))
                self._json({"ok": True, "config": load_named_config(name)})
            elif path == "/api/downloader/download":
                self._json(start_download_job(payload))
            elif path == "/api/cookies/parse":
                json_path = payload.get("json_path")
                output_path = payload.get("output_path") or build_config_payload().get("cookies_path")
                if not json_path:
                    raise ValueError("json_path is required")
                parsed_path = convert_json_cookies(json_path, output_path)
                self._json({"ok": True, "cookies_path": str(parsed_path)})
            elif path == "/api/editor/edit":
                self._json(run_editor_operation(payload))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._error(exc)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/reddit/posts/"):
                post_id = int(parsed.path.rsplit("/", 1)[-1])
                delete_reddit_post(post_id)
                self._json({"ok": True})
            elif parsed.path == "/api/media/videos":
                query = parse_qs(parsed.query)
                media_path = first(query, "path")
                if not media_path:
                    raise ValueError("path is required")
                delete_media_video(media_path)
                self._json({"ok": True})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._error(exc)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _html(self, content: str) -> None:
        data = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, exc: Exception) -> None:
        self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        total = path.stat().st_size
        start, end = 0, total - 1
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            status = HTTPStatus.PARTIAL_CONTENT
            left, _, right = range_header.removeprefix("bytes=").partition("-")
            start = int(left) if left else 0
            end = int(right) if right else total - 1
            end = min(end, total - 1)
        length = max(0, end - start + 1)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(1024 * 512, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    return values[0] if values else None


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_named_config(name: str) -> Any:
    if name not in CONFIGS:
        raise ValueError(f"Unknown config: {name}")
    return load_json(CONFIGS[name])


def save_named_config(name: str, payload: Any) -> None:
    if name not in CONFIGS:
        raise ValueError(f"Unknown config: {name}")
    write_json(CONFIGS[name], payload)


def load_voices() -> dict[str, Any]:
    return load_json(CONFIGS["voices"])


def collector_db_path() -> Path:
    return load_collector_settings(CONFIGS["collector"]).config.db_path


def connect_collector() -> sqlite3.Connection:
    db_path = collector_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def list_reddit_posts(query: dict[str, list[str]]) -> list[dict[str, Any]]:
    limit = max(1, min(200, int(first(query, "limit") or "50")))
    search = (first(query, "q") or "").strip()
    sql = """
        SELECT id, external_id, subreddit, title, question, language, score, comment_count,
               analysis_score, ingested_at, answers_json
        FROM collector_threads
    """
    params: list[Any] = []
    if search:
        sql += " WHERE title LIKE ? OR question LIKE ? OR subreddit LIKE ?"
        needle = f"%{search}%"
        params.extend([needle, needle, needle])
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    try:
        with connect_collector() as connection:
            rows = connection.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    return [format_post_row(row, compact=True) for row in rows]


def get_reddit_post(post_id: int) -> dict[str, Any]:
    with connect_collector() as connection:
        row = connection.execute("SELECT * FROM collector_threads WHERE id = ?", (post_id,)).fetchone()
        if row is None:
            raise ValueError(f"Post {post_id} not found")
        comments = connection.execute(
            "SELECT author, body, score, permalink FROM collector_comments WHERE thread_id = ? ORDER BY score DESC LIMIT 30",
            (post_id,),
        ).fetchall()
    payload = format_post_row(row, compact=False)
    payload["comments"] = [dict(comment) for comment in comments]
    return payload


def delete_reddit_post(post_id: int) -> None:
    with connect_collector() as connection:
        connection.execute("DELETE FROM collector_threads WHERE id = ?", (post_id,))


def format_post_row(row: sqlite3.Row, compact: bool) -> dict[str, Any]:
    answers = parse_json_value(row["answers_json"], [])
    payload = {
        "id": row["id"],
        "external_id": row["external_id"],
        "subreddit": row["subreddit"],
        "title": row["title"],
        "question": row["question"],
        "language": row["language"],
        "score": row["score"],
        "comment_count": row["comment_count"],
        "analysis_score": row["analysis_score"],
        "ingested_at": row["ingested_at"],
        "answers_count": len(answers),
    }
    if not compact:
        payload.update(
            {
                "body": row["body"],
                "answers": answers,
                "original_text": row["original_text"],
                "permalink": row["permalink"],
                "url": row["url"],
                "author": row["author"],
                "flair": row["flair"],
                "analysis_reasons": parse_json_value(row["analysis_reasons_json"], []),
                "matched_keywords": parse_json_value(row["matched_keywords_json"], []),
            }
        )
    return payload


def parse_json_value(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except Exception:
        return fallback


def select_thread_ids(count: int) -> list[int]:
    try:
        with connect_collector() as connection:
            rows = connection.execute(
                "SELECT id FROM collector_threads ORDER BY id DESC LIMIT ?",
                (max(1, count),),
            ).fetchall()
        return [int(row["id"]) for row in rows]
    except sqlite3.OperationalError:
        return []


def build_bootstrap() -> dict[str, Any]:
    posts = list_reddit_posts({"limit": ["12"]})
    videos = list_videos(None)
    voices = load_voices()
    media = build_media_control_payload()
    return {
        "stats": {
            "posts": count_table("collector_threads"),
            "videos": len(videos),
            "voices": len(voices),
            "jobs": len(list_jobs()),
            "media_remaining": media["summary"]["remaining_seconds"],
        },
        "posts": posts,
        "videos": videos[:20],
        "voices": voices,
        "default_voice": "upvote_3",
        "configs": {name: str(path) for name, path in CONFIGS.items()},
        "downloader": build_config_payload(),
        "media": media,
    }


def count_table(table: str) -> int:
    try:
        with connect_collector() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"])
    except sqlite3.OperationalError:
        return 0


def create_job(kind: str, title: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    job = {
        "id": job_id,
        "kind": kind,
        "title": title,
        "status": "queued",
        "progress": 0,
        "current": 0,
        "total": 1,
        "logs": [],
        "error": None,
        "result": None,
        "created_at": now,
        "updated_at": now,
        "meta": meta or {},
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    return job


def update_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.update(changes)
        job["updated_at"] = time.time()


def append_log(job_id: str, line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["logs"].append(line)
        if len(job["logs"]) > 400:
            job["logs"] = job["logs"][-400:]
        job["updated_at"] = time.time()


def list_jobs() -> list[dict[str, Any]]:
    with JOBS_LOCK:
        return [dict(job) for job in sorted(JOBS.values(), key=lambda item: item["created_at"], reverse=True)]


def get_job(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        if job_id not in JOBS:
            raise ValueError(f"Job {job_id} not found")
        return dict(JOBS[job_id])


def start_thread(target: Any, *args: Any) -> None:
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()


def start_generate_job(payload: dict[str, Any]) -> dict[str, Any]:
    count = max(1, min(50, int(payload.get("count") or 1)))
    voice = str(payload.get("voice") or "upvote_3").strip() or "upvote_3"
    job = create_job("generate", f"Генерация {count} видео", {"voice": voice, "count": count})
    start_thread(run_generate_job, job["id"], count, voice)
    return {"ok": True, "job": job}


def run_generate_job(job_id: str, count: int, voice: str) -> None:
    update_job(job_id, status="running", total=count)
    thread_ids = select_thread_ids(count)
    if not thread_ids:
        append_log(job_id, "Постов в базе нет, будет использован latest-thread fallback.")
        thread_ids = [0] * count
    try:
        for index, thread_id in enumerate(thread_ids[:count], start=1):
            update_job(job_id, current=index, progress=int((index - 1) / count * 100))
            args = [sys.executable, str(ROOT / "main.py"), "--video", "--voice", voice]
            if thread_id:
                args.extend(["--thread-id", str(thread_id)])
                append_log(job_id, f"[{index}/{count}] Генерация thread_id={thread_id}")
            else:
                args.append("--latest-thread")
                append_log(job_id, f"[{index}/{count}] Генерация latest-thread")
            rc = run_process(job_id, args)
            if rc != 0:
                raise RuntimeError(f"main.py finished with code {rc}")
        update_job(job_id, status="finished", progress=100, result={"count": min(count, len(thread_ids))})
    except Exception as exc:
        append_log(job_id, f"Ошибка: {exc}")
        update_job(job_id, status="failed", error=str(exc))


def start_collector_job(payload: dict[str, Any]) -> dict[str, Any]:
    demo = bool(payload.get("demo"))
    job = create_job("reddit", "Сбор Reddit", {"demo": demo})
    start_thread(run_collector_job, job["id"], demo)
    return {"ok": True, "job": job}


def run_collector_job(job_id: str, demo: bool) -> None:
    update_job(job_id, status="running", total=1)
    args = [sys.executable, "-m", "collector.cli"]
    if demo:
        args.append("--demo")
    try:
        rc = run_process(job_id, args)
        if rc != 0:
            raise RuntimeError(f"collector finished with code {rc}")
        update_job(job_id, status="finished", progress=100, current=1, result={"posts": count_table("collector_threads")})
    except Exception as exc:
        append_log(job_id, f"Ошибка: {exc}")
        update_job(job_id, status="failed", error=str(exc))


def run_process(job_id: str, args: list[str]) -> int:
    append_log(job_id, "$ " + " ".join(args))
    process = subprocess.Popen(
        args,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        append_log(job_id, line)
    return process.wait()


def start_download_job(payload: dict[str, Any]) -> dict[str, Any]:
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ValueError("url is required")
    job = create_job("download", "Скачивание видео", {"url": url})
    start_thread(run_download_worker, job["id"], payload)
    return {"ok": True, "job": job}


def run_download_worker(job_id: str, payload: dict[str, Any]) -> None:
    update_job(job_id, status="running", total=1)
    url = str(payload.get("url") or "").strip()
    payload = prepare_cookies_payload(payload)
    options = DownloadOptions(
        provider=str(payload.get("provider") or "youtube"),
        output_dir=payload.get("output_dir") or None,
        quality=payload.get("quality") or None,
        format_selector=payload.get("format_selector") or None,
        with_audio=payload.get("with_audio", True),
        prefer_mp4=payload.get("prefer_mp4", True),
        cookies_path=payload.get("cookies_path") or None,
        no_playlist=not bool(payload.get("playlist", False)),
        ffmpeg_location=payload.get("ffmpeg_location") or None,
    )

    def progress(event: dict[str, Any]) -> None:
        status = event.get("status") or "progress"
        percent = event.get("percent")
        if percent is not None:
            update_job(job_id, progress=max(0, min(99, int(float(percent)))))
        append_log(job_id, f"{status}: {event.get('message') or event.get('filename') or ''}")

    try:
        results = download(url, options, progress_cb=progress)
        files = [str(path) for result in results for path in result.filepaths]
        errors = [result.error for result in results if result.error]
        if errors:
            raise RuntimeError("; ".join(errors))
        update_job(job_id, status="finished", progress=100, current=1, result={"files": files})
    except Exception as exc:
        append_log(job_id, f"Ошибка: {exc}")
        update_job(job_id, status="failed", error=str(exc))


def run_editor_operation(payload: dict[str, Any]) -> dict[str, Any]:
    path = payload.get("path")
    operation = str(payload.get("operation") or "trim")
    output_name = payload.get("output_name") or None
    precise = bool(payload.get("precise", False))
    if operation in {"trim", "cut"}:
        start = float(payload.get("start") or 0)
        end = float(payload.get("end") or 0)
        result = (
            trim_video(path, start, end, precise=precise, output_name=output_name)
            if operation == "trim"
            else cut_video(path, start, end, precise=precise, output_name=output_name)
        )
    elif operation == "timeline":
        result = export_timeline(payload.get("clips") or [], precise=True, output_name=output_name)
    else:
        raise ValueError(f"Unknown editor operation: {operation}")
    video = {"path": str(result), "name": result.name}
    return {"ok": True, "video": video, "videos": [video]}


def build_media_control_payload() -> dict[str, Any]:
    settings = load_video_settings(DEFAULT_VIDEO_CONFIG_PATH).config
    videos = list_videos(settings.backgrounds_dir)
    usage = load_background_usage(settings.background_usage_db_path or (settings.cache_dir / "background_usage.sqlite3"))
    rows: list[dict[str, Any]] = []
    total_duration = 0.0
    total_used = 0.0
    for video in videos:
        path = str(Path(video["path"]).resolve())
        duration = float(video.get("duration") or usage.get(path, {}).get("duration_seconds") or 0.0)
        used = float(usage.get(path, {}).get("used_until_seconds") or 0.0)
        used = min(max(0.0, used), duration) if duration else 0.0
        percent = (used / duration * 100.0) if duration else 0.0
        total_duration += duration
        total_used += used
        row = dict(video)
        row.update(
            {
                "path": path,
                "duration": duration,
                "used_seconds": used,
                "remaining_seconds": max(0.0, duration - used),
                "used_percent": percent,
                "exhausted": bool(usage.get(path, {}).get("exhausted")) or (duration > 0 and used >= duration - 0.001),
            }
        )
        rows.append(row)
    return {
        "videos": rows,
        "summary": {
            "count": len(rows),
            "duration_seconds": total_duration,
            "used_seconds": total_used,
            "remaining_seconds": max(0.0, total_duration - total_used),
            "used_percent": (total_used / total_duration * 100.0) if total_duration else 0.0,
        },
    }


def load_background_usage(db_path: Path) -> dict[str, dict[str, Any]]:
    if not db_path.exists():
        return {}
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM background_usage").fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        connection.close()
    return {str(Path(row["path"]).resolve()): dict(row) for row in rows}


def delete_media_video(path: str) -> None:
    settings = load_video_settings(DEFAULT_VIDEO_CONFIG_PATH).config
    root = settings.backgrounds_dir.resolve()
    target = Path(path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Can delete only videos from configured backgrounds_dir") from exc
    if not target.exists() or not target.is_file():
        raise ValueError("video file not found")
    target.unlink()
    usage_db = (settings.background_usage_db_path or (settings.cache_dir / "background_usage.sqlite3"))
    if usage_db.exists():
        with sqlite3.connect(usage_db) as connection:
            connection.execute("DELETE FROM background_usage WHERE path = ?", (str(target),))


APP_HTML = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RDT Studio</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='10' y1='8' x2='54' y2='56' gradientUnits='userSpaceOnUse'%3E%3Cstop stop-color='%233ddc97'/%3E%3Cstop offset='1' stop-color='%2355a7ff'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='64' height='64' rx='14' fill='%230d0f12'/%3E%3Crect x='6' y='6' width='52' height='52' rx='11' fill='url(%23g)'/%3E%3Cpath d='M19 18h14c6 0 10 3.6 10 9 0 3.7-1.9 6.7-5.1 8.1L45 46h-8.2l-6.1-9.7H26V46h-7V18Zm7 6v6.6h6.4c2.3 0 3.6-1.2 3.6-3.3S34.7 24 32.4 24H26Z' fill='%2306110d'/%3E%3C/svg%3E">
  <style>
    :root {
      --bg: #0d0f12;
      --panel: #151922;
      --panel-2: #1b202b;
      --line: #2b3342;
      --text: #edf2f7;
      --muted: #8d98aa;
      --green: #3ddc97;
      --blue: #55a7ff;
      --amber: #ffbd5a;
      --red: #ff6b6b;
      --shadow: 0 18px 60px rgba(0,0,0,.36);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: radial-gradient(circle at 70% 0%, rgba(85,167,255,.12), transparent 32rem), var(--bg); color: var(--text); letter-spacing: 0; }
    button, input, select, textarea { font: inherit; }
    .app { display: grid; grid-template-columns: 270px 1fr; min-height: 100vh; }
    .side { border-right: 1px solid var(--line); background: rgba(13,15,18,.82); backdrop-filter: blur(14px); padding: 18px; position: sticky; top: 0; height: 100vh; }
    .brand { display: flex; align-items: center; gap: 12px; padding: 10px 8px 18px; }
    .brand-mark { width: 38px; height: 38px; border-radius: 8px; display: grid; place-items: center; color: #06110d; background: linear-gradient(135deg, var(--green), var(--blue)); font-weight: 900; }
    .brand b { display:block; font-size: 16px; }
    .brand span { color: var(--muted); font-size: 12px; }
    .nav { display: grid; gap: 6px; }
    .nav button { height: 42px; border: 0; border-radius: 8px; color: var(--muted); background: transparent; display: flex; align-items: center; gap: 10px; padding: 0 10px; cursor: pointer; text-align: left; }
    .nav button:hover, .nav button.active { background: #1a202b; color: var(--text); }
    .nav button.active { box-shadow: inset 3px 0 0 var(--green); }
    .main { padding: 22px 28px 36px; min-width: 0; }
    .top { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-bottom: 20px; }
    .title h1 { margin: 0; font-size: 28px; line-height: 1.15; }
    .title p { margin: 6px 0 0; color: var(--muted); }
    .stats { display: flex; gap: 10px; flex-wrap: wrap; }
    .stat { min-width: 112px; border: 1px solid var(--line); background: rgba(21,25,34,.78); border-radius: 8px; padding: 10px 12px; }
    .stat strong { display:block; font-size: 20px; }
    .stat span { color: var(--muted); font-size: 12px; }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px; }
    .panel { grid-column: span 12; border: 1px solid var(--line); background: rgba(21,25,34,.88); border-radius: 8px; box-shadow: var(--shadow); overflow: hidden; }
    .panel-head { display:flex; align-items:center; justify-content:space-between; gap:12px; padding: 15px 16px; border-bottom:1px solid var(--line); background: rgba(255,255,255,.02); }
    .panel-head h2 { margin:0; font-size: 15px; }
    .panel-body { padding: 16px; }
    .span-4 { grid-column: span 4; } .span-5 { grid-column: span 5; } .span-7 { grid-column: span 7; } .span-8 { grid-column: span 8; }
    .form { display:grid; gap: 12px; }
    label { display:grid; gap: 7px; color: var(--muted); font-size: 12px; }
    input, select, textarea { width:100%; border:1px solid var(--line); background:#0f131a; color:var(--text); border-radius:8px; min-height:40px; padding:9px 11px; outline:none; }
    input[type="checkbox"] { width: 16px; height: 16px; min-height: 16px; padding: 0; margin: 0; accent-color: var(--green); flex: 0 0 16px; }
    label:has(input[type="checkbox"]) { display: inline-flex; grid-template-columns: none; align-items: center; gap: 8px; min-height: 32px; }
    textarea { min-height: 260px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
    input:focus, select:focus, textarea:focus { border-color: rgba(85,167,255,.85); box-shadow: 0 0 0 3px rgba(85,167,255,.12); }
    .row { display:flex; gap: 10px; align-items:center; flex-wrap: wrap; }
    .btn { border:0; border-radius:8px; min-height:40px; padding:0 13px; color:#07110d; background:var(--green); display:inline-flex; align-items:center; justify-content:center; gap:8px; cursor:pointer; font-weight:800; }
    .btn.secondary { background:#252c39; color:var(--text); border:1px solid var(--line); }
    .btn.blue { background:var(--blue); color:#06101d; }
    .btn.warn { background:var(--amber); color:#1b1202; }
    .btn.danger { background:#2a171b; color:#ffb8b8; border:1px solid #5d2a32; }
    .btn:disabled { opacity:.55; cursor:not-allowed; }
    .icon { width:18px; height:18px; flex: 0 0 auto; }
    .list { display:grid; gap: 10px; }
    .item { border:1px solid var(--line); background:#11161e; border-radius:8px; padding: 12px; display:grid; gap:8px; min-width:0; }
    .item-title { display:flex; justify-content:space-between; gap:12px; align-items:start; min-width:0; }
    .item-title b { font-size: 14px; line-height: 1.3; }
    .file-name { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; display:block; max-width:100%; flex:1 1 auto; }
    .file-path { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; display:block; max-width:100%; flex:1 1 180px; }
    .meta { color:var(--muted); font-size:12px; display:flex; gap:10px; flex-wrap:wrap; min-width:0; }
    .pill { border:1px solid var(--line); border-radius:999px; padding:3px 8px; color:var(--muted); font-size:12px; background:#0f131a; }
    .log { height: 280px; overflow:auto; white-space:pre-wrap; background:#090b0f; border:1px solid var(--line); border-radius:8px; padding:12px; color:#c8d2e0; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size:12px; }
    .progress { height: 8px; border-radius: 999px; background:#0e1218; overflow:hidden; border:1px solid var(--line); }
    .bar { height:100%; width:0; background:linear-gradient(90deg,var(--green),var(--blue)); transition:width .25s ease; }
    .preview { width:100%; max-height:560px; background:#090b0f; border:1px solid var(--line); border-radius:8px; }
    .video-frame { display:grid; gap:12px; }
    .file-list { max-height: 620px; overflow:auto; padding-right:4px; }
    .timeline { position:relative; height:92px; border:1px solid var(--line); border-radius:8px; background:repeating-linear-gradient(90deg, rgba(255,255,255,.08) 0, rgba(255,255,255,.08) 1px, transparent 1px, transparent 10%), #0d1118; overflow:hidden; user-select:none; cursor:crosshair; }
    .playhead { position:absolute; top:0; bottom:0; width:2px; background:var(--amber); left:0; z-index:8; box-shadow:0 0 0 1px rgba(0,0,0,.25); }
    .clip { position:absolute; top:20px; height:52px; border:1px solid rgba(61,220,151,.42); border-radius:8px; background:linear-gradient(135deg, rgba(61,220,151,.32), rgba(85,167,255,.16)); cursor:grab; z-index:2; min-width:8px; }
    .clip.active { border-color:var(--green); box-shadow:0 0 0 3px rgba(61,220,151,.16); }
    .clip-label { display:block; padding:17px 10px 0; font-size:11px; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; pointer-events:none; }
    .clip-handle { position:absolute; top:0; bottom:0; width:10px; border-radius:8px; background:var(--green); cursor:ew-resize; z-index:6; }
    .clip-handle.start { left:-5px; }
    .clip-handle.end { right:-5px; background:var(--red); }
    .ticks { display:flex; justify-content:space-between; color:var(--muted); font-size:11px; font-family:ui-monospace, SFMono-Regular, Consolas, monospace; margin-top:6px; }
    .format-table { max-height:300px; overflow:auto; border:1px solid var(--line); border-radius:8px; }
    .format-table table { width:100%; border-collapse:collapse; font-size:12px; }
    .format-table th, .format-table td { padding:8px; border-bottom:1px solid var(--line); text-align:left; color:var(--muted); }
    .format-table th { color:var(--text); background:#11161e; position:sticky; top:0; }
    .split { display:grid; grid-template-columns:minmax(280px, 420px) 1fr; gap:16px; align-items:start; }
    .media-summary { display:grid; grid-template-columns:repeat(4, minmax(120px,1fr)); gap:12px; }
    .usage-row { position:relative; overflow:hidden; border:1px solid var(--line); background:#10141b; border-radius:8px; padding:12px; display:grid; gap:8px; }
    .usage-fill { position:absolute; inset:0 auto 0 0; width:0; opacity:.22; pointer-events:none; }
    .usage-fill.green { background:#3ddc97; }
    .usage-fill.yellow { background:#e5b84d; }
    .usage-fill.red { background:#e86363; }
    .usage-row > *:not(.usage-fill) { position:relative; z-index:1; }
    .dropdown { position:relative; }
    .dropdown-menu { position:absolute; right:0; top:46px; width:min(360px, calc(100vw - 40px)); border:1px solid var(--line); background:#151922; border-radius:8px; padding:12px; box-shadow:var(--shadow); z-index:15; }
    .dropdown-menu[hidden] { display:none; }
    .tabs { display:flex; gap:8px; flex-wrap:wrap; }
    .empty { color:var(--muted); border:1px dashed var(--line); border-radius:8px; padding:18px; text-align:center; }
    .toast-wrap { position:fixed; right:18px; bottom:18px; display:grid; gap:10px; z-index:20; }
    .toast { min-width:280px; max-width:420px; border:1px solid var(--line); background:#171c25; box-shadow:var(--shadow); border-radius:8px; padding:12px 14px; }
    .toast.good { border-color:rgba(61,220,151,.55); } .toast.bad { border-color:rgba(255,107,107,.55); }
    @media (max-width: 920px) {
      .app { grid-template-columns: 1fr; }
      .side { position:relative; height:auto; }
      .main { padding:18px; }
      .span-4, .span-5, .span-7, .span-8 { grid-column: span 12; }
      .split { grid-template-columns:1fr; }
      .media-summary { grid-template-columns:1fr 1fr; }
      .top { align-items:flex-start; flex-direction:column; }
    }
  </style>
</head>
<body>
<svg width="0" height="0" style="position:absolute">
  <symbol id="i-play" viewBox="0 0 24 24"><path fill="currentColor" d="M8 5v14l11-7z"/></symbol>
  <symbol id="i-db" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M4 6c0-2 16-2 16 0v12c0 2-16 2-16 0V6zm0 0c0 2 16 2 16 0M4 12c0 2 16 2 16 0"/></symbol>
  <symbol id="i-trash" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14"/></symbol>
  <symbol id="i-download" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M12 3v12m0 0 5-5m-5 5-5-5M4 21h16"/></symbol>
  <symbol id="i-cut" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="m4 4 16 16M20 4 4 20M8 8l-4 4 4 4M16 8l4 4-4 4"/></symbol>
  <symbol id="i-gear" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm8 4h3M1 12h3m14.3-6.3 2.1-2.1M3.6 20.4l2.1-2.1m0-12.6L3.6 3.6m16.8 16.8-2.1-2.1"/></symbol>
  <symbol id="i-spark" viewBox="0 0 24 24"><path fill="currentColor" d="m12 2 2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4z"/></symbol>
</svg>
<div class="app">
  <aside class="side">
    <div class="brand"><div class="brand-mark">RT</div><div><b>RDT Studio</b><span>монтажный центр</span></div></div>
    <nav class="nav" id="nav"></nav>
  </aside>
  <main class="main">
    <div class="top">
      <div class="title"><h1 id="pageTitle">Генерация</h1><p id="pageSub">Готовый пайплайн: Reddit, озвучка, фон, музыка, SFX и субтитры.</p></div>
      <div class="stats" id="stats"></div>
    </div>
    <section id="view"></section>
  </main>
</div>
<div class="toast-wrap" id="toasts"></div>
<script>
const $ = (q, r=document) => r.querySelector(q);
const state = {
  page: 'generate', boot: null, jobs: [], selectedPost: null,
  selectedVideo: null, configName: 'video', configText: '',
  editor: { current: null, duration: 0, clips: [], selectedClipId: null, nextClipId: 1, playheadTime: 0, dragTarget: null, dragOffset: 0 },
  mediaUi: { menu: false, q: '', status: 'all', sort: 'used_desc' }
};
const pages = [
  ['generate','i-spark','Генерация','Запуск готовых роликов пачкой'],
  ['reddit','i-db','Reddit','Сбор, просмотр и удаление постов'],
  ['voice','i-gear','Озвучка','Голос и конфиги'],
  ['downloader','i-download','Загрузчик','Видео и плейлисты'],
  ['editor','i-cut','Обрезчик','Trim, cut и preview'],
  ['media','i-play','Видео','Фоны, остаток и использование'],
  ['config','i-gear','Конфиги','Редактор JSON'],
  ['jobs','i-play','Задачи','Логи и прогресс']
];
function ico(id){ return `<svg class="icon"><use href="#${id}"></use></svg>`; }
async function api(path, opts={}) {
  const res = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts});
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
  return data;
}
function toast(text, good=true){
  const el = document.createElement('div'); el.className = `toast ${good?'good':'bad'}`; el.textContent = text; $('#toasts').appendChild(el);
  setTimeout(()=>el.remove(), 4200);
}
function renderNav(){
  $('#nav').innerHTML = pages.map(([id,icon,label]) => `<button class="${state.page===id?'active':''}" data-page="${id}">${ico(icon)}${label}</button>`).join('');
}
function renderStats(){
  const s = state.boot?.stats || {};
  $('#stats').innerHTML = [['Посты',s.posts||0],['Видео',s.videos||0],['Голоса',s.voices||0],['Задачи',state.jobs.length||0]].map(x=>`<div class="stat"><strong>${x[1]}</strong><span>${x[0]}</span></div>`).join('');
}
function setTitle(){
  const p = pages.find(x=>x[0]===state.page);
  $('#pageTitle').textContent = p[2]; $('#pageSub').textContent = p[3];
}
function shell(title, body, cls=''){ return `<div class="panel ${cls}"><div class="panel-head"><h2>${title}</h2></div><div class="panel-body">${body}</div></div>`; }
function jobCard(job){
  const log = (job.logs||[]).slice(-60).join('\n');
  return `<div class="item"><div class="item-title"><b>${job.title}</b><span class="pill">${job.status}</span></div><div class="meta"><span>${job.kind}</span><span>${job.current||0}/${job.total||1}</span></div><div class="progress"><div class="bar" style="width:${job.progress||0}%"></div></div><div class="log">${escapeHtml(log)}</div></div>`;
}
function renderGenerate(){
  const voices = Object.keys(state.boot?.voices || {});
  const running = state.jobs.some(j=>['queued','running'].includes(j.status));
  $('#view').innerHTML = `<div class="grid">
    ${shell('Финальная сборка', `<div class="form">
      <label>Количество видео<input id="genCount" type="number" min="1" max="50" value="1"></label>
      <label>Голос<select id="genVoice">${voices.map(v=>`<option ${v==='upvote_3'?'selected':''}>${v}</option>`).join('')}</select></label>
      <button class="btn" id="startGen" ${running?'disabled':''}>${ico('i-play')}Начать генерацию</button>
    </div>`, 'span-4')}
    ${shell('Очередь', `<div class="list">${state.jobs.filter(j=>j.kind==='generate').slice(0,3).map(jobCard).join('') || '<div class="empty">Задач генерации пока нет</div>'}</div>`, 'span-8')}
    ${shell('Свежие посты', postList(state.boot?.posts || [], true))}
  </div>`;
}
function postList(posts, compact=false){
  if(!posts.length) return '<div class="empty">В базе пока нет постов</div>';
  return `<div class="list">${posts.map(p=>`<div class="item"><div class="item-title"><b>${escapeHtml(p.title)}</b><span class="pill">#${p.id}</span></div><div class="meta"><span>r/${p.subreddit}</span><span>${p.score} score</span><span>${p.answers_count} answers</span><span>${Number(p.analysis_score).toFixed(1)} quality</span></div><div class="row"><button class="btn secondary" data-open-post="${p.id}">Открыть</button>${compact?'':`<button class="btn danger" data-delete-post="${p.id}">${ico('i-trash')}Удалить</button>`}</div></div>`).join('')}</div>`;
}
function renderPostDetail(p){
  if(!p) return '<div class="empty">Выбери пост слева, детали откроются здесь</div>';
  return `<div class="item">
    <div class="item-title"><b>${escapeHtml(p.title)}</b><span class="pill">#${p.id}</span></div>
    <div class="meta"><span>r/${p.subreddit}</span><span>${p.score} score</span><span>${p.answers_count} answers</span><span>${Number(p.analysis_score || 0).toFixed(1)} quality</span></div>
    <p>${escapeHtml(p.question)}</p>
    ${(p.answers||[]).map((a,i)=>`<div class="item"><div class="meta">Комментарий ${i+1}</div><p>${escapeHtml(a)}</p></div>`).join('')}
  </div>`;
}
function renderReddit(){
  $('#view').innerHTML = `<div class="grid">
    ${shell('Reddit база', `<div class="split">
      <div class="form">
        <div class="row"><button class="btn" id="collectReddit">${ico('i-db')}Собрать посты</button><button class="btn secondary" id="refreshPosts">Обновить</button></div>
        <input id="postSearch" placeholder="Поиск по заголовку или subreddit">
        <div id="postsBox">${postList(state.boot?.posts || [])}</div>
      </div>
      <div id="postDetail">${renderPostDetail(state.selectedPost)}</div>
    </div>`)}
  </div>`;
}
function renderVoice(){
  const voices = state.boot?.voices || {};
  $('#view').innerHTML = `<div class="grid">
    ${shell('Голоса', `<div class="list">${Object.entries(voices).map(([name,v])=>`<div class="item"><div class="item-title"><b>${name}</b><span class="pill">${name==='upvote_3'?'default':'voice'}</span></div><div class="meta"><span>${v.audio}</span></div><p>${escapeHtml((v.prompt_text||'').slice(0,260))}</p></div>`).join('')}</div>`, 'span-5')}
    ${shell('Настройки voiceover', `<div class="form"><button class="btn secondary" data-load-config="voiceover">Открыть cosyvoice.json</button><button class="btn secondary" data-load-config="voices">Открыть voices.json</button><div class="empty">Редактирование JSON находится в разделе Конфиги.</div></div>`, 'span-7')}
  </div>`;
}
function renderDownloader(){
  const cfg = state.boot?.downloader || {};
  const dl = state.dl || {};
  const formats = state.downloadInfo?.formats || [];
  $('#view').innerHTML = `<div class="grid">
    ${shell('Скачать', `<div class="form">
      <label>URL<input id="dlUrl" placeholder="https://youtube.com/..." value="${escapeAttr(dl.url || '')}"></label>
      <div class="row"><button class="btn secondary" id="dlInfo">${ico('i-spark')}Информация</button><button class="btn" id="startDownload">${ico('i-download')}Скачать</button></div>
      <label>Папка вывода<input id="dlOut" value="${escapeAttr(dl.output_dir || cfg.default_output_dir || 'input')}"></label>
      <div class="row">
        <label>Качество<select id="dlQuality">${['best','2160p','1440p','1080p','720p','480p','audio'].map(q=>`<option ${q===(dl.quality || cfg.default_quality || '1080p')?'selected':''}>${q}</option>`).join('')}</select></label>
        <label>Provider<select id="dlProvider">${(cfg.providers||['youtube']).map(p=>`<option ${p===(dl.provider || cfg.default_provider || 'youtube')?'selected':''}>${p}</option>`).join('')}</select></label>
      </div>
      <label>Raw format selector<input id="dlFormat" placeholder="Optional yt-dlp selector" value="${escapeAttr(dl.format_selector || '')}"></label>
      <label>cookies.json<input id="dlCookiesJson" placeholder="Path to exported cookies.json" value="${escapeAttr(dl.cookies_json_path || '')}"></label>
      <label>cookies.txt<input id="dlCookies" value="${escapeAttr(dl.cookies_path || cfg.cookies_path || '')}"></label>
      <label>ffmpeg path<input id="dlFfmpeg" placeholder="Optional ffmpeg folder or binary" value="${escapeAttr(dl.ffmpeg_location || '')}"></label>
      <div class="row"><label><input id="dlPlaylist" type="checkbox" ${(dl.playlist ?? !cfg.no_playlist)?'checked':''}> скачать плейлист</label><label><input id="dlMp4" type="checkbox" ${(dl.prefer_mp4 ?? cfg.prefer_mp4)!==false?'checked':''}> mp4</label><label><input id="dlAudio" type="checkbox" ${(dl.with_audio ?? cfg.with_audio)?'checked':''}> audio only</label></div>
      <button class="btn secondary" id="parseCookies">Parse cookies</button>
    </div>`, 'span-4')}
    ${shell('Видео / форматы', `<div id="dlInfoBox">${renderDownloadInfo(state.downloadInfo)}</div>${renderFormats(formats)}`, 'span-8')}
    ${shell('Задачи загрузчика', `<div class="list">${state.jobs.filter(j=>j.kind==='download').slice(0,4).map(jobCard).join('') || '<div class="empty">Загрузок пока нет</div>'}</div>`)}
  </div>`;
}
function readDownloaderForm(){
  return {
    url: $('#dlUrl')?.value || '', provider: $('#dlProvider')?.value || 'youtube',
    output_dir: $('#dlOut')?.value || '', quality: $('#dlQuality')?.value || '',
    format_selector: $('#dlFormat')?.value || '', cookies_path: $('#dlCookies')?.value || '',
    cookies_json_path: $('#dlCookiesJson')?.value || '', ffmpeg_location: $('#dlFfmpeg')?.value || '',
    playlist: Boolean($('#dlPlaylist')?.checked), prefer_mp4: Boolean($('#dlMp4')?.checked), with_audio: Boolean($('#dlAudio')?.checked)
  };
}
function renderDownloadInfo(info){
  if(!info) return '<div class="empty">Вставь URL и нажми Информация, здесь появятся title, duration и форматы</div>';
  return `<div class="item"><div class="item-title"><b>${escapeHtml(info.title || 'Без названия')}</b><span class="pill">${formatSeconds(info.duration)}</span></div><div class="meta"><span>${info.id || ''}</span><span>${info.format_count || 0} formats</span><span>${(info.heights || []).slice(0,6).join('p, ')}${(info.heights || []).length?'p':''}</span></div>${info.thumbnail ? `<img src="${escapeAttr(info.thumbnail)}" style="max-width:220px;border-radius:8px;border:1px solid var(--line)">` : ''}</div>`;
}
function renderFormats(formats){
  if(!formats.length) return '';
  return `<div class="format-table" style="margin-top:12px"><table><thead><tr><th>ID</th><th>Ext</th><th>Resolution</th><th>FPS</th><th>Video</th><th>Audio</th><th>TBR</th></tr></thead><tbody>${formats.map(f=>`<tr><td>${escapeHtml(f.id)}</td><td>${escapeHtml(f.ext)}</td><td>${escapeHtml(f.resolution)}</td><td>${escapeHtml(f.fps)}</td><td>${escapeHtml(f.vcodec)}</td><td>${escapeHtml(f.acodec)}</td><td>${escapeHtml(f.tbr)}</td></tr>`).join('')}</tbody></table></div>`;
}
function renderEditor(){
  const videos = state.boot?.videos || [];
  $('#view').innerHTML = `<div class="grid">
    ${shell('Файлы', `<div class="form"><label>Папка<input id="videoFolder" value="${escapeAttr(state.videoFolder || '')}" placeholder="Папка с видео"></label><button class="btn secondary" id="refreshVideos">Обновить</button><div class="file-list list">${videos.map(v=>`<div class="item"><div class="item-title"><b class="file-name" title="${escapeAttr(v.name)}">${escapeHtml(v.name)}</b><span class="pill">${formatSeconds(v.duration)}</span></div><div class="meta"><span>${formatSize(v.size)}</span><span class="file-path" title="${escapeAttr(v.path)}">${escapeHtml(v.path)}</span></div><button class="btn secondary" data-video="${escapeAttr(v.path)}">Открыть</button></div>`).join('') || '<div class="empty">Видео не найдены</div>'}</div></div>`, 'span-4')}
    ${shell('Обрезчик / Timeline', `<div class="video-frame">
      <video id="preview" class="preview" controls></video>
      <label>Файл<input id="editPath" readonly value="${escapeAttr(state.editor.current?.path || '')}"></label>
      <div class="row"><label>Source start<input id="editStart" type="number" step="0.001" value="0"></label><label>Source end<input id="editEnd" type="number" step="0.001" value="0"></label><label>Result name<input id="editName" placeholder="Optional filename"></label></div>
      <div class="row"><button class="btn secondary" id="setStartBtn">Set start</button><button class="btn secondary" id="setEndBtn">Set end</button><button class="btn secondary" id="previewSegment">Preview</button><button class="btn secondary" id="splitClip">Split</button><button class="btn danger" id="deleteClip">Delete clip</button><button class="btn secondary" id="resetTimeline">Reset</button><label><input id="editPrecise" type="checkbox" checked> re-encode</label></div>
      <div><div class="timeline" id="timeline"><div id="clips"></div><div class="playhead" id="playhead"></div></div><div class="ticks"><span id="tickStart">00:00</span><span id="tickMid">00:00</span><span id="tickEnd">00:00</span></div></div>
      <div class="row"><button class="btn" id="exportTimeline">${ico('i-cut')}Export timeline</button><button class="btn secondary" id="trimBtn">Quick trim</button><button class="btn warn" id="cutBtn">Quick cut</button></div>
    </div>`, 'span-8')}
  </div>`;
  hydrateEditor();
}
function renderMedia(){
  const media = state.boot?.media || {videos:[], summary:{}};
  const s = media.summary || {};
  const videos = getFilteredMediaVideos(media.videos || []);
  $('#view').innerHTML = `<div class="grid">
    ${shell('Сводка фонов', `<div class="media-summary">
      <div class="stat"><strong>${s.count || 0}</strong><span>файлов</span></div>
      <div class="stat"><strong>${formatTimeLong(s.duration_seconds || 0)}</strong><span>общая длина</span></div>
      <div class="stat"><strong>${formatTimeLong(s.used_seconds || 0)}</strong><span>использовано</span></div>
      <div class="stat"><strong>${formatTimeLong(s.remaining_seconds || 0)}</strong><span>осталось</span></div>
    </div><div class="progress" style="margin-top:14px"><div class="bar" style="width:${Math.min(100, s.used_percent || 0)}%"></div></div>`)}
    ${shell('Видео', `<div class="row" style="margin-bottom:12px;justify-content:space-between">
      <div class="meta"><span>${videos.length} показано</span><span>${media.videos.length || 0} всего</span></div>
      <div class="row">
        <button class="btn secondary" id="refreshMedia">Обновить</button>
        <div class="dropdown">
          <button class="btn secondary" id="mediaFilterBtn">${ico('i-gear')}Фильтры</button>
          <div class="dropdown-menu" id="mediaFilterMenu" ${state.mediaUi.menu ? '' : 'hidden'}>
            <div class="form">
              <label>Поиск<input id="mediaSearch" value="${escapeAttr(state.mediaUi.q)}" placeholder="Название файла"></label>
              <label>Статус<select id="mediaStatus">
                ${[['all','Все'],['unused','Не использованы'],['used','Использованы'],['warning','60%+'],['danger','85%+'],['exhausted','Закончились']].map(([v,t])=>`<option value="${v}" ${state.mediaUi.status===v?'selected':''}>${t}</option>`).join('')}
              </select></label>
              <label>Сортировка<select id="mediaSort">
                ${[['used_desc','Использование: больше'],['used_asc','Использование: меньше'],['remaining_asc','Осталось: меньше'],['remaining_desc','Осталось: больше'],['duration_desc','Длина: больше'],['duration_asc','Длина: меньше'],['name_asc','Название A-Z'],['name_desc','Название Z-A']].map(([v,t])=>`<option value="${v}" ${state.mediaUi.sort===v?'selected':''}>${t}</option>`).join('')}
              </select></label>
            </div>
          </div>
        </div>
      </div>
    </div><div class="list">${videos.map(renderMediaRow).join('') || '<div class="empty">Видео не найдены</div>'}</div>`)}
  </div>`;
}
function getFilteredMediaVideos(videos){
  const ui = state.mediaUi;
  let rows = videos.slice();
  const q = ui.q.trim().toLowerCase();
  if(q) rows = rows.filter(v => String(v.name || '').toLowerCase().includes(q) || String(v.path || '').toLowerCase().includes(q));
  if(ui.status === 'unused') rows = rows.filter(v => Number(v.used_percent || 0) <= 0.001);
  if(ui.status === 'used') rows = rows.filter(v => Number(v.used_percent || 0) > 0.001);
  if(ui.status === 'warning') rows = rows.filter(v => Number(v.used_percent || 0) >= 60);
  if(ui.status === 'danger') rows = rows.filter(v => Number(v.used_percent || 0) >= 85);
  if(ui.status === 'exhausted') rows = rows.filter(v => Boolean(v.exhausted));
  const sorters = {
    used_desc: (a,b) => Number(b.used_percent||0) - Number(a.used_percent||0),
    used_asc: (a,b) => Number(a.used_percent||0) - Number(b.used_percent||0),
    remaining_asc: (a,b) => Number(a.remaining_seconds||0) - Number(b.remaining_seconds||0),
    remaining_desc: (a,b) => Number(b.remaining_seconds||0) - Number(a.remaining_seconds||0),
    duration_desc: (a,b) => Number(b.duration||0) - Number(a.duration||0),
    duration_asc: (a,b) => Number(a.duration||0) - Number(b.duration||0),
    name_asc: (a,b) => String(a.name||'').localeCompare(String(b.name||'')),
    name_desc: (a,b) => String(b.name||'').localeCompare(String(a.name||''))
  };
  return rows.sort(sorters[ui.sort] || sorters.used_desc);
}
function renderMediaRow(v){
  const pct = Math.max(0, Math.min(100, Number(v.used_percent || 0)));
  const color = pct >= 85 ? 'red' : pct >= 60 ? 'yellow' : 'green';
  return `<div class="usage-row">
    <div class="usage-fill ${color}" style="width:${pct}%"></div>
    <div class="item-title"><b class="file-name" title="${escapeAttr(v.name)}">${escapeHtml(v.name)}</b><span class="pill">${pct.toFixed(1)}%</span></div>
    <div class="meta"><span>${formatTimeLong(v.used_seconds)} / ${formatTimeLong(v.duration)}</span><span>осталось ${formatTimeLong(v.remaining_seconds)}</span><span class="file-path" title="${escapeAttr(v.path)}">${escapeHtml(v.path)}</span></div>
    <div class="row"><button class="btn secondary" data-video="${escapeAttr(v.path)}">Открыть в обрезчике</button><button class="btn danger" data-delete-media="${escapeAttr(v.path)}">${ico('i-trash')}Удалить</button></div>
  </div>`;
}
function hydrateEditor(){
  const ed = state.editor;
  const video = $('#preview');
  if(!video) return;
  if(ed.current) {
    video.src = '/api/editor/media?path=' + encodeURIComponent(ed.current.path);
    $('#editPath').value = ed.current.path;
    $('#editStart').value = selectedClip()?.sourceStart?.toFixed(3) || '0';
    $('#editEnd').value = selectedClip()?.sourceEnd?.toFixed(3) || (ed.duration ? ed.duration.toFixed(3) : '0');
    $('#tickMid').textContent = formatTime(ed.duration / 2);
    $('#tickEnd').textContent = formatTime(ed.duration);
  }
  renderTimeline();
  video.ontimeupdate = () => {
    const clip = selectedClip();
    if(clip) ed.playheadTime = clip.timelineStart + clamp((video.currentTime || 0) - clip.sourceStart, 0, clipDuration(clip));
    renderPlayhead();
    if(clip && video.currentTime >= clip.sourceEnd && !video.paused) video.pause();
  };
}
function selectVideoForEditor(path){
  const video = (state.boot?.videos || []).find(v=>v.path===path) || {path, name:path, duration:0};
  const ed = state.editor;
  ed.current = video; ed.duration = Number(video.duration || 0); ed.clips = ed.duration ? [makeClip(0, ed.duration, 0)] : []; ed.selectedClipId = ed.clips[0]?.id || null; ed.playheadTime = 0;
  renderEditor();
}
function makeClip(sourceStart, sourceEnd, timelineStart){
  const ed = state.editor;
  return { id: ed.nextClipId++, source: ed.current.path, sourceStart, sourceEnd, timelineStart };
}
function selectedClip(){ return state.editor.clips.find(c=>c.id===state.editor.selectedClipId) || null; }
function clipDuration(clip){ return Math.max(0, clip.sourceEnd - clip.sourceStart); }
function timelineLength(){ const ed = state.editor; return Math.max(ed.clips.reduce((m,c)=>Math.max(m, c.timelineStart + clipDuration(c)), ed.duration || 0), 0.001); }
function percent(seconds){ return clamp(seconds / timelineLength() * 100, 0, 100); }
function renderTimeline(){
  const clipsNode = $('#clips'); if(!clipsNode) return;
  clipsNode.innerHTML = state.editor.clips.map(clip => `<div class="clip${clip.id===state.editor.selectedClipId?' active':''}" data-clip="${clip.id}" style="left:${percent(clip.timelineStart)}%;width:${Math.max(0, percent(clip.timelineStart+clipDuration(clip))-percent(clip.timelineStart))}%"><span class="clip-label">${formatTime(clip.sourceStart)} - ${formatTime(clip.sourceEnd)}</span><span class="clip-handle start" data-clip="${clip.id}" data-edge="start"></span><span class="clip-handle end" data-clip="${clip.id}" data-edge="end"></span></div>`).join('');
  renderPlayhead();
}
function renderPlayhead(){ const p=$('#playhead'); if(p) p.style.left = `${percent(state.editor.playheadTime)}%`; }
function selectClip(id){
  state.editor.selectedClipId = id;
  const clip = selectedClip();
  if(clip){ $('#editStart').value = clip.sourceStart.toFixed(3); $('#editEnd').value = clip.sourceEnd.toFixed(3); state.editor.playheadTime = clip.timelineStart; }
  renderTimeline();
}
function syncInputsToClip(){
  const clip = selectedClip(); if(!clip) return;
  const oldStart = clip.sourceStart;
  clip.sourceStart = clamp(Number($('#editStart').value || 0), 0, state.editor.duration);
  clip.sourceEnd = clamp(Number($('#editEnd').value || 0), clip.sourceStart + 0.001, state.editor.duration);
  clip.timelineStart = Math.max(0, clip.timelineStart + (clip.sourceStart - oldStart));
}
function splitClipAtPlayhead(){
  const ed = state.editor;
  const clip = ed.clips.find(c=>ed.playheadTime > c.timelineStart && ed.playheadTime < c.timelineStart + clipDuration(c));
  if(!clip) return toast('Playhead должен быть внутри клипа', false);
  const sourceAt = clip.sourceStart + (ed.playheadTime - clip.timelineStart);
  if(sourceAt <= clip.sourceStart + 0.001 || sourceAt >= clip.sourceEnd - 0.001) return toast('Слишком близко к краю клипа', false);
  const left = makeClip(clip.sourceStart, sourceAt, clip.timelineStart);
  const right = makeClip(sourceAt, clip.sourceEnd, ed.playheadTime);
  ed.clips = ed.clips.flatMap(c=>c.id===clip.id ? [left,right] : [c]);
  ed.selectedClipId = right.id; renderTimeline(); toast('Клип разделен');
}
function deleteSelectedClip(){ const ed=state.editor; ed.clips = ed.clips.filter(c=>c.id!==ed.selectedClipId); ed.selectedClipId = ed.clips[0]?.id || null; renderTimeline(); }
function resetTimeline(){ const ed=state.editor; ed.clips = ed.duration ? [makeClip(0, ed.duration, 0)] : []; ed.selectedClipId = ed.clips[0]?.id || null; renderTimeline(); }
function secondsFromTimelineEvent(event){ const rect=$('#timeline').getBoundingClientRect(); return clamp((event.clientX-rect.left)/rect.width,0,1)*timelineLength(); }
async function exportEditorTimeline(){
  syncInputsToClip();
  const ed = state.editor;
  if(!ed.current || !ed.clips.length) throw new Error('Выбери видео и клипы');
  await api('/api/editor/edit',{method:'POST',body:JSON.stringify({operation:'timeline', path:ed.current.path, clips:ed.clips.slice().sort((a,b)=>a.timelineStart-b.timelineStart).map(c=>({source:c.source, source_start:c.sourceStart, source_end:c.sourceEnd, timeline_start:c.timelineStart})), output_name:$('#editName').value, precise:$('#editPrecise').checked})});
  await refreshVideos(); toast('Timeline экспортирован');
}
async function renderConfig(){
  if(!state.configText) await loadConfig(state.configName, false);
  $('#view').innerHTML = `<div class="grid">
    ${shell('Файл', `<div class="form"><select id="configSelect">${['collector','translator','voiceover','voices','video','downloader'].map(n=>`<option ${n===state.configName?'selected':''}>${n}</option>`).join('')}</select><button class="btn secondary" id="reloadConfig">Перечитать</button><button class="btn" id="saveConfig">Сохранить</button></div>`, 'span-4')}
    ${shell('JSON', `<textarea id="configText" spellcheck="false">${escapeHtml(state.configText)}</textarea>`, 'span-8')}
  </div>`;
}
function renderJobs(){
  $('#view').innerHTML = `<div class="grid">${shell('Все задачи', `<div class="list">${state.jobs.map(jobCard).join('') || '<div class="empty">Задач пока нет</div>'}</div>`)}</div>`;
}
function render(){
  renderNav(); renderStats(); setTitle();
  if(state.page==='generate') renderGenerate();
  if(state.page==='reddit') renderReddit();
  if(state.page==='voice') renderVoice();
  if(state.page==='downloader') renderDownloader();
  if(state.page==='editor') renderEditor();
  if(state.page==='media') renderMedia();
  if(state.page==='config') renderConfig();
  if(state.page==='jobs') renderJobs();
}
async function boot(){
  state.boot = await api('/api/bootstrap');
  await pollJobs();
  render();
}
async function pollJobs(){
  try { state.jobs = (await api('/api/jobs')).jobs; renderStats(); if(['generate','jobs'].includes(state.page)) render(); } catch(e) {}
}
async function refreshPosts(){
  const posts = (await api('/api/reddit/posts?limit=80')).posts;
  state.boot.posts = posts; render();
}
async function refreshVideos(){
  const folder = $('#videoFolder')?.value || state.videoFolder || '';
  state.videoFolder = folder;
  const videos = (await api('/api/editor/videos' + (folder ? '?dir=' + encodeURIComponent(folder) : ''))).videos;
  state.boot.videos = videos; state.boot.stats.videos = videos.length; render();
}
async function refreshMedia(){
  const media = await api('/api/media/videos');
  state.boot.media = media;
  state.boot.stats.media_remaining = media.summary.remaining_seconds;
  render();
}
async function loadConfig(name, rerender=true){
  const data = await api('/api/config/'+name); state.configName = name; state.configText = JSON.stringify(data.config, null, 2); if(rerender) render();
}
function escapeHtml(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function escapeAttr(s){ return escapeHtml(s).replace(/`/g,'&#96;'); }
function formatSeconds(v){ return v == null ? '??' : `${Number(v).toFixed(1)}s`; }
function formatTime(value){ const seconds=Math.max(0, Math.floor(Number(value)||0)); const m=Math.floor(seconds/60); const s=seconds%60; return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`; }
function formatTimeLong(value){ const total=Math.max(0, Math.floor(Number(value)||0)); const h=Math.floor(total/3600); const m=Math.floor((total%3600)/60); const s=total%60; return h ? `${h}h ${String(m).padStart(2,'0')}m` : `${m}m ${String(s).padStart(2,'0')}s`; }
function formatSize(value){ let size=Number(value||0); for(const unit of ['B','KiB','MiB','GiB']){ if(size<1024) return `${size.toFixed(size<10?1:0)} ${unit}`; size/=1024; } return `${size.toFixed(1)} TiB`; }
function clamp(value,min,max){ return Math.min(max, Math.max(min, value)); }
document.addEventListener('click', async (e)=>{
  const btn = e.target.closest('button'); if(!btn) return;
  try {
    if(btn.dataset.page){ state.page = btn.dataset.page; render(); return; }
    if(btn.id==='startGen'){ const r=await api('/api/generate',{method:'POST',body:JSON.stringify({count:$('#genCount').value,voice:$('#genVoice').value})}); toast('Генерация запущена'); state.page='jobs'; await pollJobs(); render(); return; }
    if(btn.id==='collectReddit'){ await api('/api/reddit/collect',{method:'POST',body:'{}'}); toast('Сбор Reddit запущен'); state.page='jobs'; await pollJobs(); render(); return; }
    if(btn.id==='refreshPosts'){ await refreshPosts(); toast('Посты обновлены'); return; }
    if(btn.dataset.openPost){ state.selectedPost=(await api('/api/reddit/posts/'+btn.dataset.openPost)).post; $('#postDetail').innerHTML = renderPostDetail(state.selectedPost); return; }
    if(btn.dataset.deletePost){ if(confirm('Удалить пост из базы?')){ await api('/api/reddit/posts/'+btn.dataset.deletePost,{method:'DELETE'}); await refreshPosts(); toast('Пост удален'); } return; }
    if(btn.dataset.loadConfig){ state.page='config'; await loadConfig(btn.dataset.loadConfig); return; }
    if(btn.id==='dlInfo'){ state.dl = readDownloaderForm(); state.downloadInfo = await api('/api/downloader/info?provider='+encodeURIComponent(state.dl.provider)+'&url='+encodeURIComponent(state.dl.url)); renderDownloader(); toast('Информация загружена'); return; }
    if(btn.id==='parseCookies'){ const r=await api('/api/cookies/parse',{method:'POST',body:JSON.stringify({json_path:$('#dlCookiesJson').value,output_path:$('#dlCookies').value})}); $('#dlCookies').value=r.cookies_path; toast('Cookies parsed'); return; }
    if(btn.id==='startDownload'){ state.dl = readDownloaderForm(); await api('/api/downloader/download',{method:'POST',body:JSON.stringify({...state.dl, cookies_output_path: state.dl.cookies_path})}); toast('Загрузка запущена'); state.page='jobs'; await pollJobs(); render(); return; }
    if(btn.id==='refreshVideos'){ await refreshVideos(); toast('Видео обновлены'); return; }
    if(btn.id==='refreshMedia'){ await refreshMedia(); toast('Список видео обновлен'); return; }
    if(btn.id==='mediaFilterBtn'){ state.mediaUi.menu = !state.mediaUi.menu; renderMedia(); return; }
    if(btn.dataset.video){ state.page='editor'; selectVideoForEditor(btn.dataset.video); return; }
    if(btn.dataset.deleteMedia){ if(confirm('Удалить видео файл?')){ await api('/api/media/videos?path='+encodeURIComponent(btn.dataset.deleteMedia), {method:'DELETE'}); await refreshMedia(); toast('Видео удалено'); } return; }
    if(btn.id==='trimBtn'||btn.id==='cutBtn'){ await api('/api/editor/edit',{method:'POST',body:JSON.stringify({path:$('#editPath').value,start:$('#editStart').value,end:$('#editEnd').value,operation:btn.id==='trimBtn'?'trim':'cut',precise:$('#editPrecise').checked})}); await refreshVideos(); toast('Операция завершена'); return; }
    if(btn.id==='setStartBtn'){ $('#editStart').value=($('#preview').currentTime||0).toFixed(3); syncInputsToClip(); renderTimeline(); return; }
    if(btn.id==='setEndBtn'){ $('#editEnd').value=($('#preview').currentTime||0).toFixed(3); syncInputsToClip(); renderTimeline(); return; }
    if(btn.id==='previewSegment'){ const clip=selectedClip(); if(clip){ $('#preview').currentTime=clip.sourceStart; $('#preview').play(); } return; }
    if(btn.id==='splitClip'){ splitClipAtPlayhead(); return; }
    if(btn.id==='deleteClip'){ deleteSelectedClip(); return; }
    if(btn.id==='resetTimeline'){ resetTimeline(); return; }
    if(btn.id==='exportTimeline'){ await exportEditorTimeline(); return; }
    if(btn.id==='reloadConfig'){ await loadConfig($('#configSelect').value); toast('Конфиг перечитан'); return; }
    if(btn.id==='saveConfig'){ JSON.parse($('#configText').value); await api('/api/config/'+$('#configSelect').value,{method:'POST',body:JSON.stringify({config:JSON.parse($('#configText').value)})}); state.configText=$('#configText').value; toast('Конфиг сохранен'); return; }
  } catch(err) { toast(err.message, false); }
});
document.addEventListener('change', async (e)=>{
  if(e.target.id==='configSelect') { await loadConfig(e.target.value); }
  if(e.target.id==='mediaStatus') { state.mediaUi.status = e.target.value; renderMedia(); }
  if(e.target.id==='mediaSort') { state.mediaUi.sort = e.target.value; renderMedia(); }
});
document.addEventListener('input', async (e)=>{
  if(e.target.id==='postSearch') {
    const q = encodeURIComponent(e.target.value);
    const posts = (await api('/api/reddit/posts?limit=80&q='+q)).posts;
    $('#postsBox').innerHTML = postList(posts);
  }
  if(e.target.id==='editStart' || e.target.id==='editEnd') { syncInputsToClip(); renderTimeline(); }
  if(e.target.id==='mediaSearch') { state.mediaUi.q = e.target.value; renderMedia(); }
});
document.addEventListener('mousedown', (e)=>{
  const handle = e.target.closest('.clip-handle');
  const clipNode = e.target.closest('.clip');
  if(handle){ selectClip(Number(handle.dataset.clip)); state.editor.dragTarget = `trim:${handle.dataset.clip}:${handle.dataset.edge}`; e.preventDefault(); return; }
  if(clipNode){ selectClip(Number(clipNode.dataset.clip)); state.editor.dragTarget = `move:${clipNode.dataset.clip}`; state.editor.dragOffset = secondsFromTimelineEvent(e) - (selectedClip()?.timelineStart || 0); e.preventDefault(); }
});
document.addEventListener('mousemove', (e)=>{
  const ed = state.editor;
  if(!ed.dragTarget || !$('#timeline')) return;
  const seconds = secondsFromTimelineEvent(e);
  if(ed.dragTarget.startsWith('move:')){
    const id = Number(ed.dragTarget.split(':')[1]); const clip = ed.clips.find(c=>c.id===id);
    if(clip) clip.timelineStart = Math.max(0, seconds - ed.dragOffset);
  }
  if(ed.dragTarget.startsWith('trim:')){
    const [, idText, edge] = ed.dragTarget.split(':'); const clip = ed.clips.find(c=>c.id===Number(idText));
    if(clip && edge==='start'){ const oldEnd=clip.timelineStart+clipDuration(clip); const nextStart=clamp(seconds,0,oldEnd-0.05); const delta=nextStart-clip.timelineStart; clip.sourceStart=clamp(clip.sourceStart+delta,0,clip.sourceEnd-0.05); clip.timelineStart=nextStart; }
    if(clip && edge==='end'){ const nextDuration=Math.max(0.05, seconds-clip.timelineStart); clip.sourceEnd=clamp(clip.sourceStart+nextDuration, clip.sourceStart+0.05, ed.duration); }
    if(clip){ $('#editStart').value=clip.sourceStart.toFixed(3); $('#editEnd').value=clip.sourceEnd.toFixed(3); }
  }
  renderTimeline();
});
document.addEventListener('mouseup', ()=> state.editor.dragTarget = null);
document.addEventListener('click', (e)=>{
  if(e.target.id==='timeline' && state.editor.current){
    const seconds = secondsFromTimelineEvent(e);
    state.editor.playheadTime = seconds;
    const clip = state.editor.clips.find(c=>seconds > c.timelineStart && seconds < c.timelineStart + clipDuration(c));
    if(clip){ selectClip(clip.id); state.editor.playheadTime = seconds; $('#preview').currentTime = clip.sourceStart + (seconds - clip.timelineStart); }
    renderPlayhead();
  }
});
setInterval(pollJobs, 2500);
boot().catch(e=>toast(e.message,false));
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
