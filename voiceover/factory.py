from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import VoiceoverEngine
from .cosyvoice import CosyVoiceSettings, CosyVoiceVoiceoverEngine, reexec_in_cosyvoice_python


DEFAULT_ENGINE = "cosyvoice"


def create_voiceover_engine(
    config: dict[str, Any],
    base_path: Path = Path("."),
) -> VoiceoverEngine:
    engine_name = str(config.get("voiceover_engine", DEFAULT_ENGINE)).lower()
    if engine_name == "cosyvoice":
        return CosyVoiceVoiceoverEngine(CosyVoiceSettings.from_config(config, base_path))

    raise SystemExit(f"Unsupported voiceover_engine: {engine_name}")


def prepare_voiceover_runtime(
    config: dict[str, Any],
    script_path: Path,
    argv: list[str],
    base_path: Path = Path("."),
) -> None:
    engine_name = str(config.get("voiceover_engine", DEFAULT_ENGINE)).lower()
    if engine_name == "cosyvoice":
        settings = CosyVoiceSettings.from_config(config, base_path)
        reexec_in_cosyvoice_python(settings, script_path, argv)
        return

    raise SystemExit(f"Unsupported voiceover_engine: {engine_name}")
