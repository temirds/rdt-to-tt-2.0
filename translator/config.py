from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = MODULE_ROOT / "config.json"


@dataclass(frozen=True)
class TranslationConfig:
    env_path: Path = MODULE_ROOT / ".env"
    backend: str = "local_nllb"
    target_language: str = "ru"
    default_source_language: str = "en"
    timeout_seconds: int = 120
    model_id: str = "facebook/nllb-200-3.3B"
    model_dir: Path = MODULE_ROOT / "models" / "nllb-200-3.3B"
    hf_token_env: str = "HF_TOKEN"
    local_files_only: bool = True
    device: str = "auto"
    device_map: str = "auto"
    torch_dtype: str = "auto"
    max_input_chars: int = 1400
    max_input_tokens: int = 512
    max_new_tokens: int = 512
    batch_size: int = 4


@dataclass(frozen=True)
class LoadedTranslationSettings:
    config: TranslationConfig
    path: Path


def load_translation_settings(path: Path = DEFAULT_CONFIG_PATH) -> LoadedTranslationSettings:
    path = path.resolve()
    raw = _load_json(path)
    translation_raw = raw.get("translation", {})

    config = TranslationConfig(
        env_path=_resolve_path(translation_raw.get("env_path", ".env"), path.parent),
        backend=str(translation_raw.get("backend", TranslationConfig.backend)),
        target_language=str(translation_raw.get("target_language", TranslationConfig.target_language)),
        default_source_language=str(
            translation_raw.get("default_source_language", TranslationConfig.default_source_language)
        ),
        timeout_seconds=int(translation_raw.get("timeout_seconds", TranslationConfig.timeout_seconds)),
        model_id=str(translation_raw.get("model_id", TranslationConfig.model_id)),
        model_dir=_resolve_path(translation_raw.get("model_dir", "models/nllb-200-3.3B"), path.parent),
        hf_token_env=str(translation_raw.get("hf_token_env", TranslationConfig.hf_token_env)),
        local_files_only=bool(translation_raw.get("local_files_only", TranslationConfig.local_files_only)),
        device=str(translation_raw.get("device", TranslationConfig.device)),
        device_map=str(translation_raw.get("device_map", TranslationConfig.device_map)),
        torch_dtype=str(translation_raw.get("torch_dtype", TranslationConfig.torch_dtype)),
        max_input_chars=int(translation_raw.get("max_input_chars", TranslationConfig.max_input_chars)),
        max_input_tokens=int(translation_raw.get("max_input_tokens", TranslationConfig.max_input_tokens)),
        max_new_tokens=int(translation_raw.get("max_new_tokens", TranslationConfig.max_new_tokens)),
        batch_size=int(translation_raw.get("batch_size", TranslationConfig.batch_size)),
    )
    return LoadedTranslationSettings(config=config, path=path)


def merge_config(base: TranslationConfig, **overrides: Any) -> TranslationConfig:
    cleaned = {key: value for key, value in overrides.items() if value is not None}
    return replace(base, **cleaned)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Translator config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()
