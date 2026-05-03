from __future__ import annotations

import argparse
import json
import mimetypes
import re
import shutil
import threading
import time
import uuid
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .api import available_providers, download, fetch_info
from .base import DownloadOptions, DownloadResult
from .config import DEFAULT_CONFIG_PATH, load_downloader_settings
from .cookies import convert_json_cookies
from .editor import cut_video, describe_video, export_timeline, list_videos, resolve_media_path, trim_video


JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DownloaderWebHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Downloader web UI: {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local downloader web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=8765, help="Bind port.")
    return parser.parse_args()


class DownloaderWebHandler(BaseHTTPRequestHandler):
    server_version = "DownloaderWeb/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_text(INDEX_HTML, content_type="text/html; charset=utf-8")
            return
        if parsed.path == "/editor":
            self._send_text(EDITOR_HTML, content_type="text/html; charset=utf-8")
            return
        if parsed.path == "/api/config":
            self._send_json(build_config_payload())
            return
        if parsed.path == "/api/editor/videos":
            query = parse_qs(parsed.query)
            directory = first_query_value(query, "dir")
            try:
                self._send_json({"videos": list_videos(directory)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/editor/media":
            query = parse_qs(parsed.query)
            path = first_query_value(query, "path")
            if not path:
                self._send_json({"error": "path is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_file(path)
            return
        if parsed.path == "/api/info":
            query = parse_qs(parsed.query)
            url = first_query_value(query, "url")
            provider = first_query_value(query, "provider") or "youtube"
            if not url:
                self._send_json({"error": "url is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                info = fetch_info(url, DownloadOptions(provider=provider))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(summarize_info(info))
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(job)
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/cookies/parse":
            try:
                payload = self._read_json()
                json_path = payload.get("json_path")
                output_path = payload.get("output_path") or load_downloader_settings(DEFAULT_CONFIG_PATH).config.cookies_path
                if not json_path:
                    raise ValueError("json_path is required")
                parsed_path = convert_json_cookies(json_path, output_path)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"cookies_path": str(parsed_path)})
            return

        if parsed.path == "/api/editor/edit":
            try:
                payload = self._read_json()
                operation = str(payload.get("operation") or "").strip()
                path = str(payload.get("path") or "").strip()
                start = float(payload.get("start"))
                end = float(payload.get("end") if payload.get("end") is not None else start)
                precise = bool(payload.get("precise"))
                output_name = str(payload.get("output_name") or "").strip() or None
                if operation == "trim":
                    output = trim_video(path, start, end, precise=precise, output_name=output_name)
                    videos = [describe_video(output)]
                elif operation == "cut":
                    output = cut_video(path, start, end, precise=precise, output_name=output_name)
                    videos = [describe_video(output)]
                elif operation == "timeline":
                    output = export_timeline(payload.get("clips") or [], precise=precise, output_name=output_name)
                    videos = [describe_video(output)]
                else:
                    raise ValueError("operation must be trim, cut, or timeline")
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"video": videos[0], "videos": videos})
            return

        if parsed.path != "/api/download":
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json()
            payload = prepare_cookies_payload(payload)
            options = build_options_from_payload(payload)
            url = str(payload.get("url") or "").strip()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if not url:
            self._send_json({"error": "url is required"}, status=HTTPStatus.BAD_REQUEST)
            return

        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "status": "queued",
            "created_at": time.time(),
            "provider": options.provider,
            "url": url,
            "progress": {},
            "results": [],
            "error": None,
        }
        with JOBS_LOCK:
            JOBS[job_id] = job

        thread = threading.Thread(target=run_download_job, args=(job_id, url, options), daemon=True)
        thread.start()
        self._send_json({"job_id": job_id})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, text: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: str) -> None:
        try:
            file_path = resolve_media_path(path)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if not file_path.exists() or not file_path.is_file():
            self._send_json({"error": "file not found"}, status=HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        size = file_path.stat().st_size
        range_header = self.headers.get("Range")
        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                start = int(match.group(1) or 0)
                end = int(match.group(2) or size - 1)
                end = min(end, size - 1)
                if start <= end:
                    self.send_response(HTTPStatus.PARTIAL_CONTENT)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                    with file_path.open("rb") as handle:
                        handle.seek(start)
                        remaining = end - start + 1
                        while remaining > 0:
                            chunk = handle.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with file_path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)


def build_config_payload() -> dict[str, Any]:
    settings = load_downloader_settings(DEFAULT_CONFIG_PATH).config
    return {
        "providers": available_providers(),
        "default_provider": settings.default_provider,
        "default_output_dir": str(settings.default_output_dir),
        "default_quality": settings.default_quality,
        "with_audio": settings.with_audio,
        "prefer_mp4": settings.prefer_mp4,
        "no_playlist": settings.no_playlist,
        "cookies_path": str(settings.cookies_path) if settings.cookies_path else "",
    }


def build_options_from_payload(payload: dict[str, Any]) -> DownloadOptions:
    provider = str(payload.get("provider") or "youtube").strip().lower()
    if provider not in available_providers():
        raise ValueError(f"unsupported provider: {provider}")

    output_dir = optional_path(payload.get("output_dir"))
    cookies_path = optional_path(payload.get("cookies_path"))
    ffmpeg_location = optional_path(payload.get("ffmpeg_location"))
    return DownloadOptions(
        provider=provider,
        output_dir=output_dir,
        quality=optional_string(payload.get("quality")),
        format_selector=optional_string(payload.get("format_selector")),
        with_audio=optional_bool(payload.get("with_audio")),
        prefer_mp4=optional_bool(payload.get("prefer_mp4")),
        cookies_path=cookies_path,
        no_playlist=optional_bool(payload.get("no_playlist")),
        ffmpeg_location=ffmpeg_location,
    )


def prepare_cookies_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cookies_json_path = optional_string(payload.get("cookies_json_path"))
    if not cookies_json_path:
        return payload

    output_path = optional_string(payload.get("cookies_output_path"))
    if not output_path:
        output_path = str(load_downloader_settings(DEFAULT_CONFIG_PATH).config.cookies_path)
    parsed_path = convert_json_cookies(cookies_json_path, output_path)
    prepared = dict(payload)
    prepared["cookies_path"] = str(parsed_path)
    return prepared


def run_download_job(job_id: str, url: str, options: DownloadOptions) -> None:
    update_job(job_id, status="running", started_at=time.time())

    def on_progress(data: dict[str, Any]) -> None:
        progress = {
            "status": data.get("status"),
            "percent": clean_progress_value(data.get("_percent_str")),
            "speed": clean_progress_value(data.get("_speed_str")),
            "eta": clean_progress_value(data.get("_eta_str")),
            "total": clean_progress_value(data.get("_total_bytes_str")),
            "filename": data.get("filename"),
        }
        update_job(job_id, progress=progress)

    try:
        results = download(url, options, progress_cb=on_progress)
        failed = [result for result in results if not result.success]
        update_job(
            job_id,
            status="failed" if failed else "finished",
            finished_at=time.time(),
            results=[serialize_result(result) for result in results],
            error=failed[0].error if failed else None,
        )
    except Exception as exc:
        update_job(job_id, status="failed", finished_at=time.time(), error=str(exc))


def update_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(changes)


def serialize_result(result: DownloadResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["filepaths"] = [str(path) for path in result.filepaths]
    return payload


def summarize_info(info: dict[str, Any]) -> dict[str, Any]:
    formats = info.get("formats") or []
    playable_formats = [fmt for fmt in formats if isinstance(fmt, dict) and _is_playable_format(fmt)]
    heights = sorted(
        {
            int(fmt["height"])
            for fmt in playable_formats
            if isinstance(fmt.get("height"), int)
        },
        reverse=True,
    )
    return {
        "title": info.get("title"),
        "id": info.get("id"),
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url"),
        "thumbnail": info.get("thumbnail"),
        "format_count": len(formats),
        "heights": heights,
        "formats": [summarize_format(fmt) for fmt in playable_formats[:80]],
    }


def summarize_format(fmt: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": fmt.get("format_id"),
        "ext": fmt.get("ext"),
        "resolution": fmt.get("resolution") or _format_resolution(fmt),
        "height": fmt.get("height"),
        "fps": fmt.get("fps"),
        "vcodec": fmt.get("vcodec"),
        "acodec": fmt.get("acodec"),
        "tbr": fmt.get("tbr"),
        "protocol": fmt.get("protocol"),
    }


def _is_playable_format(fmt: dict[str, Any]) -> bool:
    return fmt.get("vcodec") != "none" or fmt.get("acodec") != "none"


def _format_resolution(fmt: dict[str, Any]) -> str:
    width = fmt.get("width")
    height = fmt.get("height")
    if width and height:
        return f"{width}x{height}"
    return ""


def first_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0].strip()


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_path(value: Any) -> Path | None:
    text = optional_string(value)
    return Path(text).resolve() if text else None


def optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def clean_progress_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Downloader</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f5f2;
      --surface: #ffffff;
      --line: #d8dbd2;
      --text: #20231f;
      --muted: #62695e;
      --accent: #256f5b;
      --accent-dark: #1d5546;
      --danger: #a23b3b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, Segoe UI, system-ui, sans-serif;
    }
    main {
      width: min(1600px, calc(100vw - 8px));
      height: calc(100vh - 8px);
      margin: 4px auto;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 10px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }
    h1 {
      margin: 0;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .provider {
      color: var(--muted);
      font-size: 14px;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(340px, .75fr);
      grid-template-rows: minmax(0, 1fr) minmax(150px, .55fr);
      grid-template-areas:
        "download side"
        "status side";
      gap: 10px;
      min-height: 0;
    }
    section {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-height: 0;
      overflow: hidden;
    }
    .download { grid-area: download; }
    .side { grid-area: side; display: grid; grid-template-rows: auto minmax(0, 1fr); gap: 10px; background: transparent; border: 0; padding: 0; }
    .status-panel { grid-area: status; }
    h2 {
      margin: 0 0 8px;
      font-size: 14px;
      letter-spacing: 0;
    }
    label {
      display: block;
      margin: 8px 0 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    input, select {
      width: 100%;
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 9px;
      background: white;
      color: var(--text);
      font: inherit;
      font-size: 13px;
    }
    .row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .checks {
      display: flex;
      gap: 14px;
      margin: 10px 0 0;
      flex-wrap: wrap;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--text);
      font-size: 14px;
      font-weight: 500;
    }
    .check input {
      width: 16px;
      height: 16px;
    }
    .actions {
      display: flex;
      gap: 8px;
      margin-top: 10px;
      flex-wrap: wrap;
    }
    button {
      height: 34px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      padding: 0 12px;
      background: var(--accent);
      color: white;
      cursor: pointer;
      font-weight: 700;
      font-size: 13px;
    }
    button.secondary {
      background: white;
      color: var(--accent-dark);
    }
    button:disabled {
      opacity: .55;
      cursor: not-allowed;
    }
    .status {
      height: calc(100% - 58px);
      min-height: 74px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fbfbf9;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      overflow: auto;
      font-family: Consolas, ui-monospace, monospace;
      font-size: 12px;
    }
    .thumb {
      width: 100%;
      max-height: 180px;
      aspect-ratio: 16 / 9;
      object-fit: cover;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #eef0ec;
      display: none;
      margin-bottom: 8px;
    }
    .meta {
      display: grid;
      gap: 6px;
      font-size: 12px;
      overflow: auto;
    }
    .meta div {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      border-bottom: 1px solid #eceee8;
      padding-bottom: 6px;
    }
    .meta span:first-child { color: var(--muted); }
    .formats {
      margin-top: 8px;
      max-height: 210px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfbf9;
      font-family: Consolas, ui-monospace, monospace;
      font-size: 11px;
    }
    .formats table { width: 100%; border-collapse: collapse; }
    .formats th, .formats td {
      padding: 5px 6px;
      border-bottom: 1px solid #eceee8;
      text-align: left;
      white-space: nowrap;
    }
    .formats th { color: var(--muted); font-weight: 700; position: sticky; top: 0; background: #fbfbf9; }
    .files {
      margin: 8px 0 0;
      padding-left: 18px;
      overflow-wrap: anywhere;
      max-height: 44px;
      overflow: auto;
      font-size: 12px;
    }
    .error { color: var(--danger); }
    .hint { color: var(--muted); font-size: 12px; margin-top: 6px; }
    @media (max-width: 820px) {
      main { width: calc(100vw - 8px); height: auto; margin: 4px auto; }
      header { align-items: start; flex-direction: column; }
      .layout {
        grid-template-columns: 1fr;
        grid-template-rows: auto auto auto;
        grid-template-areas: "download" "side" "status";
      }
      .row { grid-template-columns: 1fr; }
      section { overflow: visible; }
      .status { height: 150px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Downloader</h1>
        <div class="provider" id="providerLabel">Provider: youtube</div>
      </div>
      <a class="provider" href="/editor">Editor</a>
    </header>
    <div class="layout">
      <section class="download">
        <h2>Download</h2>
        <label for="url">URL</label>
        <input id="url" placeholder="https://www.youtube.com/watch?v=...">
        <div class="row">
          <div>
            <label for="provider">Provider</label>
            <select id="provider"></select>
          </div>
          <div>
            <label for="quality">Quality</label>
            <select id="quality">
              <option value="best">best</option>
              <option value="2160p">2160p</option>
              <option value="1440p">1440p</option>
              <option value="1080p">1080p</option>
              <option value="720p">720p</option>
              <option value="480p">480p</option>
              <option value="360p">360p</option>
              <option value="audio">audio</option>
            </select>
          </div>
        </div>
        <label for="outputDir">Output folder</label>
        <input id="outputDir">
        <div class="row">
          <div>
            <label for="cookiesJsonPath">cookies.json</label>
            <input id="cookiesJsonPath" placeholder="Path to exported cookies.json">
          </div>
          <div>
            <label for="cookiesPath">cookies.txt</label>
            <input id="cookiesPath" placeholder="Parsed cookies.txt">
          </div>
        </div>
        <div class="row">
          <div>
            <label for="formatSelector">Raw format selector</label>
            <input id="formatSelector" placeholder="Optional yt-dlp selector">
          </div>
          <div>
            <label for="ffmpegLocation">FFmpeg location</label>
            <input id="ffmpegLocation" placeholder="Optional">
          </div>
        </div>
        <div class="checks">
          <label class="check"><input id="withAudio" type="checkbox"> With audio</label>
          <label class="check"><input id="preferMp4" type="checkbox" checked> Prefer MP4</label>
        </div>
        <div class="actions">
          <button id="downloadBtn">Download</button>
          <button id="infoBtn" class="secondary">Fetch info</button>
          <button id="parseCookiesBtn" class="secondary">Parse cookies</button>
        </div>
        <div class="hint">If cookies.json is set, Download parses it to cookies.txt first and uses that file.</div>
      </section>
      <section class="side">
        <section>
          <h2>Info</h2>
          <img id="thumb" class="thumb" alt="">
          <div class="meta" id="meta"></div>
          <div class="formats" id="formats"></div>
        </section>
        <section>
          <h2>Files</h2>
          <ul class="files" id="files"></ul>
        </section>
      </section>
      <section class="status-panel">
        <h2>Status</h2>
        <div class="status" id="status">Ready.</div>
      </section>
    </div>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let currentJob = null;
    let pollTimer = null;

    async function init() {
      const config = await requestJson('/api/config');
      $('provider').innerHTML = config.providers.map((p) => `<option value="${p}">${p}</option>`).join('');
      $('provider').value = config.default_provider || 'youtube';
      $('providerLabel').textContent = `Provider: ${$('provider').value}`;
      $('outputDir').value = config.default_output_dir || '';
      $('cookiesPath').value = config.cookies_path || '';
      $('quality').value = config.default_quality || 'best';
      $('withAudio').checked = Boolean(config.with_audio);
      $('preferMp4').checked = Boolean(config.prefer_mp4);
    }

    async function fetchVideoInfo() {
      const url = $('url').value.trim();
      if (!url) return setStatus('URL is required.', true);
      setBusy(true);
      setStatus('Fetching info...');
      try {
        const info = await requestJson(`/api/info?provider=${encodeURIComponent($('provider').value)}&url=${encodeURIComponent(url)}`);
        renderInfo(info);
        setStatus(`Info loaded: ${info.title || info.id || url}`);
      } catch (err) {
        setStatus(err.message, true);
      } finally {
        setBusy(false);
      }
    }

    async function startDownload() {
      const payload = formPayload();
      if (!payload.url) return setStatus('URL is required.', true);
      setBusy(true);
      $('files').innerHTML = '';
      setStatus('Queued...');
      try {
        const response = await requestJson('/api/download', {
          method: 'POST',
          body: JSON.stringify(payload),
          headers: {'Content-Type': 'application/json'}
        });
        currentJob = response.job_id;
        pollJob();
        pollTimer = setInterval(pollJob, 1000);
      } catch (err) {
        setStatus(err.message, true);
        setBusy(false);
      }
    }

    async function parseCookies() {
      const jsonPath = $('cookiesJsonPath').value.trim();
      if (!jsonPath) return setStatus('cookies.json path is required.', true);
      setBusy(true);
      setStatus('Parsing cookies...');
      try {
        const response = await requestJson('/api/cookies/parse', {
          method: 'POST',
          body: JSON.stringify({
            json_path: jsonPath,
            output_path: $('cookiesPath').value.trim()
          }),
          headers: {'Content-Type': 'application/json'}
        });
        $('cookiesPath').value = response.cookies_path;
        setStatus(`Cookies parsed:\n${response.cookies_path}`);
      } catch (err) {
        setStatus(err.message, true);
      } finally {
        setBusy(false);
      }
    }

    async function pollJob() {
      if (!currentJob) return;
      try {
        const job = await requestJson(`/api/jobs/${currentJob}`);
        renderJob(job);
        if (job.status === 'finished' || job.status === 'failed') {
          clearInterval(pollTimer);
          pollTimer = null;
          currentJob = null;
          setBusy(false);
        }
      } catch (err) {
        setStatus(err.message, true);
        clearInterval(pollTimer);
        setBusy(false);
      }
    }

    function formPayload() {
      return {
        url: $('url').value.trim(),
        provider: $('provider').value,
        output_dir: $('outputDir').value.trim(),
        quality: $('quality').value,
        format_selector: $('formatSelector').value.trim(),
        with_audio: $('withAudio').checked,
        cookies_path: $('cookiesPath').value.trim(),
        cookies_json_path: $('cookiesJsonPath').value.trim(),
        cookies_output_path: $('cookiesPath').value.trim(),
        ffmpeg_location: $('ffmpegLocation').value.trim(),
        prefer_mp4: $('preferMp4').checked,
        no_playlist: true
      };
    }

    function renderInfo(info) {
      const thumb = $('thumb');
      if (info.thumbnail) {
        thumb.src = info.thumbnail;
        thumb.style.display = 'block';
      } else {
        thumb.style.display = 'none';
      }
      const rows = [
        ['Title', info.title || ''],
        ['ID', info.id || ''],
        ['Duration', info.duration ? `${info.duration}s` : ''],
        ['Formats', info.format_count ?? ''],
        ['Heights', (info.heights || []).join(', ')]
      ];
      $('meta').innerHTML = rows.map(([k, v]) => `<div><span>${escapeHtml(k)}</span><strong>${escapeHtml(String(v))}</strong></div>`).join('');
      renderFormats(info.formats || []);
    }

    function renderFormats(formats) {
      if (!formats.length) {
        $('formats').innerHTML = '';
        return;
      }
      const rows = formats.map((fmt) => `
        <tr>
          <td>${escapeHtml(String(fmt.id || ''))}</td>
          <td>${escapeHtml(String(fmt.ext || ''))}</td>
          <td>${escapeHtml(String(fmt.resolution || fmt.height || ''))}</td>
          <td>${escapeHtml(String(fmt.fps || ''))}</td>
          <td>${escapeHtml(String(fmt.vcodec || ''))}</td>
          <td>${escapeHtml(String(fmt.acodec || ''))}</td>
          <td>${escapeHtml(String(fmt.tbr ? Math.round(fmt.tbr) : ''))}</td>
        </tr>
      `).join('');
      $('formats').innerHTML = `
        <table>
          <thead><tr><th>ID</th><th>EXT</th><th>RES</th><th>FPS</th><th>V</th><th>A</th><th>TBR</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    }

    function renderJob(job) {
      const p = job.progress || {};
      const lines = [
        `Job: ${job.id}`,
        `Status: ${job.status}`,
        `URL: ${job.url}`
      ];
      if (p.status) lines.push(`Progress: ${[p.status, p.percent, p.total, p.speed, p.eta && 'ETA ' + p.eta].filter(Boolean).join(' ')}`);
      if (job.error) lines.push(`Error: ${job.error}`);
      setStatus(lines.join('\n'), job.status === 'failed');
      const files = [];
      for (const result of job.results || []) {
        for (const path of result.filepaths || []) files.push(path);
      }
      $('files').innerHTML = files.map((path) => `<li>${escapeHtml(path)}</li>`).join('');
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, options);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      return data;
    }

    function setStatus(text, isError = false) {
      $('status').textContent = text;
      $('status').classList.toggle('error', isError);
    }

    function setBusy(value) {
      $('downloadBtn').disabled = value;
      $('infoBtn').disabled = value;
      $('parseCookiesBtn').disabled = value;
    }

    function escapeHtml(value) {
      return value.replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]));
    }

    $('provider').addEventListener('change', () => $('providerLabel').textContent = `Provider: ${$('provider').value}`);
    $('downloadBtn').addEventListener('click', startDownload);
    $('infoBtn').addEventListener('click', fetchVideoInfo);
    $('parseCookiesBtn').addEventListener('click', parseCookies);
    init().catch((err) => setStatus(err.message, true));
  </script>
</body>
</html>
"""


EDITOR_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Downloader Editor</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f5f2;
      --surface: #ffffff;
      --line: #d8dbd2;
      --text: #20231f;
      --muted: #62695e;
      --accent: #256f5b;
      --accent-soft: #bcd8ce;
      --danger: #a23b3b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, Segoe UI, system-ui, sans-serif;
    }
    main {
      width: min(1760px, calc(100vw - 8px));
      height: calc(100vh - 8px);
      margin: 4px auto;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 10px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }
    h1 {
      margin: 0;
      font-size: 22px;
      letter-spacing: 0;
    }
    a { color: var(--accent); text-decoration: none; font-weight: 700; }
    .layout {
      min-height: 0;
      display: grid;
      grid-template-columns: 300px minmax(0, 1fr);
      gap: 10px;
    }
    section {
      min-height: 0;
      overflow: hidden;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }
    h2 {
      margin: 0 0 8px;
      font-size: 14px;
      letter-spacing: 0;
    }
    label {
      display: block;
      margin: 8px 0 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    input, select {
      width: 100%;
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 9px;
      background: white;
      color: var(--text);
      font: inherit;
      font-size: 13px;
    }
    button {
      height: 34px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      padding: 0 12px;
      background: var(--accent);
      color: white;
      cursor: pointer;
      font-weight: 700;
      font-size: 13px;
    }
    button.secondary { background: white; color: var(--accent); }
    button.danger { background: var(--danger); border-color: var(--danger); }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .icon-button {
      width: 34px;
      padding: 0;
      display: inline-grid;
      place-items: center;
      font-size: 18px;
      line-height: 1;
    }
    .sidebar {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 10px;
    }
    .file-list {
      overflow: auto;
      display: grid;
      gap: 6px;
      align-content: start;
    }
    .file {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      background: #fbfbf9;
      cursor: pointer;
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .file.active { border-color: var(--accent); background: #eef7f3; }
    .file strong { display: block; font-size: 12px; margin-bottom: 4px; }
    .muted { color: var(--muted); font-size: 12px; }
    .workspace {
      display: grid;
      grid-template-rows: minmax(0, 1fr) auto;
      gap: 10px;
    }
    .preview {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 10px;
      min-height: 0;
    }
    video {
      width: 100%;
      height: 100%;
      min-height: 260px;
      max-height: calc(100vh - 280px);
      background: #111;
      border-radius: 8px;
      border: 1px solid var(--line);
    }
    .panel {
      display: grid;
      align-content: start;
      gap: 8px;
      overflow: auto;
    }
    .row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 4px;
    }
    .timeline-panel {
      display: grid;
      grid-template-columns: 42px minmax(0, 1fr);
      grid-template-rows: auto auto;
      gap: 8px;
      align-items: start;
    }
    .timeline-panel h2 { grid-column: 1 / -1; }
    .timeline-tools {
      display: grid;
      gap: 6px;
      justify-items: center;
      padding-top: 2px;
    }
    .timeline {
      position: relative;
      height: 82px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background:
        repeating-linear-gradient(90deg, #e9ebe5 0, #e9ebe5 1px, transparent 1px, transparent 10%),
        linear-gradient(#fbfbf9, #f1f3ee);
      overflow: hidden;
      user-select: none;
      cursor: crosshair;
    }
    .playhead {
      position: absolute;
      top: 0;
      bottom: 0;
      width: 2px;
      background: #111;
      left: 0;
    }
    .clip {
      position: absolute;
      top: 18px;
      height: 46px;
      border: 1px solid rgba(32, 35, 31, .18);
      border-radius: 6px;
      background: linear-gradient(135deg, rgba(37, 111, 91, .20), rgba(37, 111, 91, .08));
      cursor: grab;
      z-index: 2;
    }
    .clip:hover { background: linear-gradient(135deg, rgba(37, 111, 91, .28), rgba(37, 111, 91, .12)); }
    .clip.active {
      border: 2px solid var(--accent);
      box-shadow: 0 0 0 3px rgba(37, 111, 91, .18);
      z-index: 4;
    }
    .clip-label {
      display: block;
      padding: 15px 10px 0;
      font-size: 11px;
      color: #20231f;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      pointer-events: none;
    }
    .clip-handle {
      position: absolute;
      top: 0;
      bottom: 0;
      width: 10px;
      border-radius: 6px;
      background: var(--accent);
      cursor: ew-resize;
      z-index: 6;
    }
    .clip-handle.start { left: -5px; }
    .clip-handle.end { right: -5px; background: var(--danger); }
    .ticks {
      display: flex;
      justify-content: space-between;
      color: var(--muted);
      font-size: 11px;
      font-family: Consolas, ui-monospace, monospace;
    }
    .status {
      min-height: 54px;
      max-height: 92px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      background: #fbfbf9;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-family: Consolas, ui-monospace, monospace;
      font-size: 12px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      margin-top: 4px;
    }
    .check input { width: 16px; height: 16px; }
    @media (max-width: 900px) {
      main { width: calc(100vw - 8px); height: auto; margin: 4px auto; }
      .layout, .preview { grid-template-columns: 1fr; }
      video { height: auto; max-height: none; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Video Editor</h1>
      <a href="/">Downloader</a>
    </header>
    <div class="layout">
      <div class="sidebar">
        <section>
          <h2>Folder</h2>
          <input id="folder" placeholder="Output folder">
          <div class="actions">
            <button id="refreshBtn" class="secondary">Refresh</button>
          </div>
        </section>
        <section class="file-list" id="files"></section>
      </div>
      <div class="workspace">
        <section class="preview">
          <video id="video" controls></video>
          <div class="panel">
            <h2>Clip</h2>
            <div class="row">
              <div>
                <label for="start">Source start</label>
                <input id="start" type="number" min="0" step="0.001" value="0">
              </div>
              <div>
                <label for="end">Source end</label>
                <input id="end" type="number" min="0" step="0.001" value="0">
              </div>
            </div>
            <div class="row">
              <button id="setStartBtn" class="secondary">Set start</button>
              <button id="setEndBtn" class="secondary">Set end</button>
            </div>
            <label for="outputName">Result name</label>
            <input id="outputName" placeholder="Optional filename without extension">
            <label class="check"><input id="precise" type="checkbox" checked> Re-encode export</label>
            <div class="actions">
              <button id="previewBtn" class="secondary">Preview segment</button>
              <button id="deleteClipBtn" class="secondary">Delete clip</button>
              <button id="applyBtn">Export</button>
            </div>
            <div class="status" id="status">Select a video.</div>
          </div>
        </section>
        <section class="timeline-panel">
          <h2>Timeline</h2>
          <div class="timeline-tools">
            <button id="splitBtn" class="secondary icon-button" title="Split selected clip at playhead" aria-label="Split selected clip">✂</button>
            <button id="deleteClipIconBtn" class="secondary icon-button" title="Delete selected clip" aria-label="Delete selected clip">⌫</button>
            <button id="resetTimelineBtn" class="secondary icon-button" title="Reset timeline" aria-label="Reset timeline">↺</button>
          </div>
          <div>
            <div class="timeline" id="timeline">
              <div id="clips"></div>
              <div class="playhead" id="playhead"></div>
            </div>
            <div class="ticks"><span id="tickStart">00:00</span><span id="tickMid">00:00</span><span id="tickEnd">00:00</span></div>
          </div>
        </section>
      </div>
    </div>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let videos = [];
    let current = null;
    let duration = 0;
    let dragTarget = null;
    let dragOffset = 0;
    let clips = [];
    let selectedClipId = null;
    let nextClipId = 1;
    let playheadTime = 0;

    async function init() {
      const config = await requestJson('/api/config');
      $('folder').value = config.default_output_dir || '';
      await loadVideos();
    }

    async function loadVideos() {
      setStatus('Loading videos...');
      const payload = await requestJson(`/api/editor/videos?dir=${encodeURIComponent($('folder').value.trim())}`);
      videos = payload.videos || [];
      renderFiles();
      setStatus(videos.length ? 'Select a video.' : 'No videos found.');
    }

    function renderFiles() {
      $('files').innerHTML = videos.map((video, index) => `
        <div class="file ${current && current.path === video.path ? 'active' : ''}" data-index="${index}">
          <strong>${escapeHtml(video.name)}</strong>
          <span class="muted">${formatTime(video.duration || 0)} · ${formatSize(video.size)}</span>
        </div>
      `).join('');
      document.querySelectorAll('.file').forEach((node) => {
        node.addEventListener('click', () => selectVideo(videos[Number(node.dataset.index)]));
      });
    }

    function selectVideo(video) {
      current = video;
      duration = Number(video.duration || 0);
      $('video').src = `/api/editor/media?path=${encodeURIComponent(video.path)}`;
      $('start').value = '0';
      $('end').value = duration ? duration.toFixed(3) : '0';
      $('outputName').value = '';
      clips = duration ? [makeClip(0, duration, 0)] : [];
      selectedClipId = clips[0]?.id || null;
      playheadTime = 0;
      $('tickStart').textContent = '00:00';
      $('tickMid').textContent = formatTime(duration / 2);
      $('tickEnd').textContent = formatTime(duration);
      renderFiles();
      renderTimeline();
      setStatus(video.path);
    }

    function makeClip(sourceStart, sourceEnd, timelineStart) {
      return {
        id: nextClipId++,
        source: current.path,
        sourceStart,
        sourceEnd,
        timelineStart
      };
    }

    function selectedClip() {
      return clips.find((clip) => clip.id === selectedClipId) || null;
    }

    function renderTimeline() {
      renderClips();
      renderPlayhead();
    }

    function renderClips() {
      $('clips').innerHTML = clips.map((clip) => {
        const left = percent(clip.timelineStart);
        const right = percent(clip.timelineStart + clipDuration(clip));
        const active = clip.id === selectedClipId ? ' active' : '';
        return `
          <div class="clip${active}" data-id="${clip.id}" title="${formatTime(clip.sourceStart)} - ${formatTime(clip.sourceEnd)}" style="left:${left}%;width:${Math.max(0, right - left)}%">
            <span class="clip-label">${formatTime(clip.sourceStart)} - ${formatTime(clip.sourceEnd)}</span>
            <span class="clip-handle start" data-id="${clip.id}" data-edge="start"></span>
            <span class="clip-handle end" data-id="${clip.id}" data-edge="end"></span>
          </div>
        `;
      }).join('');
      document.querySelectorAll('.clip').forEach((node) => {
        node.addEventListener('click', (event) => {
          event.stopPropagation();
          selectClip(Number(node.dataset.id));
        });
        node.addEventListener('mousedown', (event) => {
          event.stopPropagation();
          selectClip(Number(node.dataset.id));
          const clip = selectedClip();
          if (!clip) return;
          dragTarget = `move:${clip.id}`;
          dragOffset = secondsFromEvent(event) - clip.timelineStart;
        });
      });
      document.querySelectorAll('.clip-handle').forEach((node) => {
        node.addEventListener('mousedown', (event) => {
          event.stopPropagation();
          selectClip(Number(node.dataset.id));
          dragTarget = `trim:${node.dataset.id}:${node.dataset.edge}`;
        });
      });
    }

    function clipDuration(clip) {
      return Math.max(0, clip.sourceEnd - clip.sourceStart);
    }

    function selectClip(id) {
      selectedClipId = id;
      const clip = selectedClip();
      if (clip) {
        $('start').value = clip.sourceStart.toFixed(3);
        $('end').value = clip.sourceEnd.toFixed(3);
        playheadTime = clip.timelineStart;
      }
      renderTimeline();
    }

    function renderPlayhead() {
      $('playhead').style.left = `${percent(playheadTime)}%`;
    }

    function percent(seconds) {
      const total = timelineLength();
      if (!total) return 0;
      return clamp(seconds / total * 100, 0, 100);
    }

    function timelineLength() {
      const end = clips.reduce((max, clip) => Math.max(max, clip.timelineStart + clipDuration(clip)), duration || 0);
      return Math.max(end, 0.001);
    }

    function secondsFromEvent(event) {
      const rect = $('timeline').getBoundingClientRect();
      const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
      return ratio * timelineLength();
    }

    async function applyEdit() {
      if (!current) return setStatus('Select a video first.', true);
      syncInputsToSelectedClip();
      if (!clips.length) return setStatus('Timeline is empty.', true);
      await runEdit();
    }

    function splitAtPlayhead() {
      if (!current) return setStatus('Select a video first.', true);
      const clip = clipAtTimelineTime(playheadTime);
      if (!clip) return setStatus('Move playhead onto a clip before splitting.', true);
      const sourceAt = clip.sourceStart + (playheadTime - clip.timelineStart);
      if (!(sourceAt > clip.sourceStart + 0.001 && sourceAt < clip.sourceEnd - 0.001)) {
        return setStatus('Playhead must be inside the selected clip.', true);
      }
      const left = makeClip(clip.sourceStart, sourceAt, clip.timelineStart);
      const right = makeClip(sourceAt, clip.sourceEnd, playheadTime);
      clips = clips.flatMap((item) => item.id === clip.id ? [left, right] : [item]);
      selectedClipId = right.id;
      selectClip(right.id);
      setStatus(`Split clip at ${formatTime(sourceAt)}. Both pieces are independent timeline clips now.`);
      renderTimeline();
    }

    function clipAtTimelineTime(seconds) {
      return clips.find((clip) => seconds > clip.timelineStart && seconds < clip.timelineStart + clipDuration(clip)) || null;
    }

    function deleteSelectedClip() {
      if (!selectedClipId) return;
      clips = clips.filter((clip) => clip.id !== selectedClipId);
      selectedClipId = clips[0]?.id || null;
      if (selectedClipId) selectClip(selectedClipId);
      else renderTimeline();
    }

    function resetTimeline() {
      clips = duration ? [makeClip(0, duration, 0)] : [];
      selectedClipId = clips[0]?.id || null;
      if (selectedClipId) selectClip(selectedClipId);
      else renderTimeline();
    }

    function syncInputsToSelectedClip() {
      const clip = selectedClip();
      if (!clip) return;
      const start = clamp(Number($('start').value || 0), 0, duration);
      const end = clamp(Number($('end').value || 0), start + 0.001, duration);
      const oldStart = clip.sourceStart;
      clip.sourceStart = start;
      clip.sourceEnd = end;
      clip.timelineStart = Math.max(0, clip.timelineStart + (start - oldStart));
    }

    async function runEdit() {
      setBusy(true);
      setStatus('Running ffmpeg...');
      try {
        const payload = await requestJson('/api/editor/edit', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            path: current.path,
            start: 0,
            end: duration,
            operation: 'timeline',
            clips: clips
              .slice()
              .sort((a, b) => a.timelineStart - b.timelineStart)
              .map((clip) => ({
                source: clip.source,
                source_start: clip.sourceStart,
                source_end: clip.sourceEnd,
                timeline_start: clip.timelineStart
              })),
            output_name: $('outputName').value.trim(),
            precise: $('precise').checked
          })
        });
        const saved = (payload.videos || [payload.video]).map((video) => video.path).join('\n');
        setStatus(`Saved:\n${saved}`);
        await loadVideos();
      } catch (err) {
        setStatus(err.message, true);
      } finally {
        setBusy(false);
      }
    }

    function previewSegment() {
      if (!current) return;
      const clip = selectedClip();
      $('video').currentTime = clip ? clip.sourceStart : 0;
      $('video').play();
    }

    function clamp(value, min, max) {
      return Math.min(max, Math.max(min, value));
    }

    function formatTime(value) {
      const seconds = Math.max(0, Math.floor(Number(value) || 0));
      const m = Math.floor(seconds / 60);
      const s = seconds % 60;
      return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    function formatSize(value) {
      let size = Number(value || 0);
      for (const unit of ['B', 'KiB', 'MiB', 'GiB']) {
        if (size < 1024) return `${size.toFixed(size < 10 ? 1 : 0)} ${unit}`;
        size /= 1024;
      }
      return `${size.toFixed(1)} TiB`;
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, options);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      return data;
    }

    function setStatus(text, isError = false) {
      $('status').textContent = text;
      $('status').style.color = isError ? 'var(--danger)' : 'var(--text)';
    }

    function setBusy(value) {
      $('applyBtn').disabled = value;
      $('splitBtn').disabled = value;
      $('deleteClipBtn').disabled = value;
      $('deleteClipIconBtn').disabled = value;
      $('resetTimelineBtn').disabled = value;
      $('refreshBtn').disabled = value;
    }

    function escapeHtml(value) {
      return value.replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]));
    }

    $('refreshBtn').addEventListener('click', loadVideos);
    $('applyBtn').addEventListener('click', applyEdit);
    $('splitBtn').addEventListener('click', splitAtPlayhead);
    $('deleteClipBtn').addEventListener('click', deleteSelectedClip);
    $('deleteClipIconBtn').addEventListener('click', deleteSelectedClip);
    $('resetTimelineBtn').addEventListener('click', resetTimeline);
    $('previewBtn').addEventListener('click', previewSegment);
    $('setStartBtn').addEventListener('click', () => { $('start').value = ($('video').currentTime || 0).toFixed(3); syncInputsToSelectedClip(); renderTimeline(); });
    $('setEndBtn').addEventListener('click', () => { $('end').value = ($('video').currentTime || 0).toFixed(3); syncInputsToSelectedClip(); renderTimeline(); });
    $('start').addEventListener('input', () => { syncInputsToSelectedClip(); renderTimeline(); });
    $('end').addEventListener('input', () => { syncInputsToSelectedClip(); renderTimeline(); });
    $('video').addEventListener('timeupdate', () => {
      const clip = selectedClip();
      if (clip) playheadTime = clip.timelineStart + clamp(($('video').currentTime || 0) - clip.sourceStart, 0, clipDuration(clip));
      renderPlayhead();
      if (clip && $('video').currentTime >= clip.sourceEnd && !$('video').paused) $('video').pause();
    });
    $('timeline').addEventListener('click', (event) => {
      if (!duration || dragTarget) return;
      const clickedTime = secondsFromEvent(event);
      playheadTime = clickedTime;
      const clip = clipAtTimelineTime(clickedTime);
      if (clip) {
        selectClip(clip.id);
        playheadTime = clickedTime;
        $('video').currentTime = clip.sourceStart + (clickedTime - clip.timelineStart);
      }
      renderPlayhead();
    });
    window.addEventListener('mousemove', (event) => {
      if (!dragTarget || !duration) return;
      const seconds = secondsFromEvent(event);
      if (dragTarget.startsWith('move:')) {
        const id = Number(dragTarget.split(':')[1]);
        const clip = clips.find((item) => item.id === id);
        if (clip) clip.timelineStart = Math.max(0, seconds - dragOffset);
      }
      if (dragTarget.startsWith('trim:')) {
        const [, idText, edge] = dragTarget.split(':');
        const clip = clips.find((item) => item.id === Number(idText));
        if (clip) {
          if (edge === 'start') {
            const oldEnd = clip.timelineStart + clipDuration(clip);
            const newTimelineStart = clamp(seconds, 0, oldEnd - 0.05);
            const delta = newTimelineStart - clip.timelineStart;
            clip.sourceStart = clamp(clip.sourceStart + delta, 0, clip.sourceEnd - 0.05);
            clip.timelineStart = clip.sourceEnd - clip.sourceStart > 0.05 ? newTimelineStart : clip.timelineStart;
          }
          if (edge === 'end') {
            const newDuration = Math.max(0.05, seconds - clip.timelineStart);
            clip.sourceEnd = clamp(clip.sourceStart + newDuration, clip.sourceStart + 0.05, duration);
          }
          $('start').value = clip.sourceStart.toFixed(3);
          $('end').value = clip.sourceEnd.toFixed(3);
        }
      }
      renderTimeline();
    });
    window.addEventListener('mouseup', () => dragTarget = null);
    init().catch((err) => setStatus(err.message, true));
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
