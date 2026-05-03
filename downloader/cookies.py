from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def convert_json_cookies(json_path: Path | str, output_path: Path | str | None = None) -> Path:
    source = Path(json_path).resolve()
    if output_path is None:
        target = source.with_suffix(".txt")
    else:
        target = Path(output_path).resolve()

    cookies = _load_cookie_items(source)
    lines = [
        "# Netscape HTTP Cookie File",
        "# This file was generated from cookies.json",
    ]
    for item in cookies:
        line = _cookie_item_to_netscape_line(item)
        if line:
            lines.append(line)

    if len(lines) <= 2:
        raise ValueError("cookies.json does not contain convertible cookies")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _load_cookie_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"cookies JSON not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        raw = raw["cookies"]

    if not isinstance(raw, list):
        raise ValueError("cookies JSON must be a list or an object with a cookies list")

    return [item for item in raw if isinstance(item, dict)]


def _cookie_item_to_netscape_line(item: dict[str, Any]) -> str | None:
    name = item.get("name")
    value = item.get("value")
    domain = item.get("domain")
    path = item.get("path") or "/"
    if not name or value is None or not domain:
        return None

    http_only = bool(item.get("httpOnly") or item.get("http_only"))
    secure = bool(item.get("secure"))
    host_only = item.get("hostOnly")
    if host_only is None:
        host_only = not str(domain).startswith(".")

    domain_text = str(domain)
    if http_only:
        domain_text = f"#HttpOnly_{domain_text}"

    expiry = item.get("expiry") or item.get("expirationDate") or item.get("expires") or 0
    try:
        expiry_int = int(float(expiry))
    except Exception:
        expiry_int = 0

    return "\t".join(
        [
            domain_text,
            "FALSE" if host_only else "TRUE",
            str(path),
            "TRUE" if secure else "FALSE",
            str(expiry_int),
            str(name),
            str(value),
        ]
    )
