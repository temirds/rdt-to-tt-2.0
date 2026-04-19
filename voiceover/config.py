from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import VoiceProfile


MODULE_ROOT = Path(__file__).resolve().parent
DEFAULT_VOICEOVER_CONFIG_PATH = MODULE_ROOT / "configs" / "cosyvoice.json"


@dataclass(frozen=True)
class LoadedVoiceoverConfig:
    config: dict[str, Any]
    base_path: Path


def load_json_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Config not found: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def load_voiceover_config(path: Path = DEFAULT_VOICEOVER_CONFIG_PATH) -> dict[str, Any]:
    return load_voiceover_config_bundle(path).config


def load_voiceover_config_bundle(path: Path = DEFAULT_VOICEOVER_CONFIG_PATH) -> LoadedVoiceoverConfig:
    path = path.resolve()
    config = load_json_config(path)

    _load_external_voices(config, path.parent)
    _validate_voiceover_config(config)
    base_path = resolve_project_path(config.get("module_root", "."), MODULE_ROOT).resolve()
    return LoadedVoiceoverConfig(config=config, base_path=base_path)


def _validate_voiceover_config(config: dict[str, Any]) -> None:
    required = ["cosyvoice_repo", "cosyvoice_model", "default_instruction", "voices"]
    missing = [key for key in required if key not in config]
    if missing:
        raise SystemExit(f"Config is missing required keys: {', '.join(missing)}")


def _load_external_voices(config: dict[str, Any], config_dir: Path) -> None:
    if "voices" in config or "voices_config" not in config:
        return

    voices_config_path = resolve_project_path(config["voices_config"], config_dir)
    config["voices"] = load_json_config(voices_config_path)


def resolve_project_path(path: str | Path, base_path: Path) -> Path:
    resolved = Path(path)
    if resolved.is_absolute():
        return resolved
    return base_path / resolved


def load_voice_profile(
    config: dict[str, Any],
    voice_name: str | None,
    base_path: Path = Path("."),
) -> VoiceProfile:
    if not voice_name:
        raise SystemExit("Voice is not set. Pass voice to generate_voiceover(...) or --voice.")
    if "voices" not in config:
        raise SystemExit("Config is missing required key: voices")

    voices = config["voices"]
    if voice_name not in voices:
        available = ", ".join(sorted(voices)) or "no voices"
        raise SystemExit(f"Voice '{voice_name}' not found. Available: {available}")

    voice = dict(voices[voice_name])
    if "audio" not in voice or "prompt_text" not in voice:
        raise SystemExit(f"Voice '{voice_name}' must contain 'audio' and 'prompt_text'.")

    audio_path = Path(voice["audio"])
    if not audio_path.is_absolute():
        candidates = [base_path / audio_path]
        if len(audio_path.parts) == 1:
            candidates.append(base_path / "voices" / audio_path)
        audio_path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    if not audio_path.exists():
        raise SystemExit(f"Voice audio not found: {audio_path}")

    prompt_text = str(voice["prompt_text"]).strip()
    if not prompt_text:
        raise SystemExit(f"Voice '{voice_name}' has empty prompt_text.")

    return VoiceProfile(name=voice_name, audio_path=audio_path.resolve(), prompt_text=prompt_text)
