from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .base import VoiceoverRequest, VoiceoverResult
from .config import load_voice_profile, load_voiceover_config_bundle
from .factory import create_voiceover_engine, prepare_voiceover_runtime as prepare_engine_runtime
from .text import normalize_russian_numbers


@dataclass(frozen=True)
class VoiceoverOptions:
    voice: str | None = None
    pitch: float = 0.0
    speed: float = 1.0
    output_path: Path | None = None
    output_dir: Path | None = None
    filename: str | None = None
    normalize_numbers: bool = True


def generate_voiceover(
    text: str,
    options: VoiceoverOptions | Mapping[str, Any] | None = None,
    **overrides: Any,
) -> VoiceoverResult:
    opts = _build_options(options, overrides)
    _validate_voice_controls(opts.pitch, opts.speed)
    config_bundle = load_voiceover_config_bundle()
    config = config_bundle.config
    base_path = config_bundle.base_path

    prepare_engine_runtime(config, Path(sys.argv[0]), sys.argv[1:], base_path)

    prepared_text = text.strip()
    if not prepared_text:
        raise SystemExit("Text is empty.")
    if opts.normalize_numbers:
        prepared_text = normalize_russian_numbers(prepared_text)

    voice = load_voice_profile(config, opts.voice, base_path)
    output_path = _resolve_output_path(base_path, opts)
    engine = create_voiceover_engine(config, base_path)

    return engine.synthesize_to_file(
        VoiceoverRequest(
            text=prepared_text,
            voice=voice,
            output_path=output_path,
            pitch=opts.pitch,
            speed=opts.speed,
        )
    )


def _build_options(
    options: VoiceoverOptions | Mapping[str, Any] | None,
    overrides: dict[str, Any],
) -> VoiceoverOptions:
    values: dict[str, Any]
    if options is None:
        values = {}
    elif isinstance(options, VoiceoverOptions):
        values = {
            "voice": options.voice,
            "pitch": options.pitch,
            "speed": options.speed,
            "output_path": options.output_path,
            "output_dir": options.output_dir,
            "filename": options.filename,
            "normalize_numbers": options.normalize_numbers,
        }
    else:
        values = dict(options)

    values.update({key: value for key, value in overrides.items() if value is not None})
    return VoiceoverOptions(**values)


def _validate_voice_controls(pitch: float, speed: float) -> None:
    if not -12.0 <= float(pitch) <= 12.0:
        raise SystemExit("pitch must be between -12.0 and 12.0 semitones.")
    if not 0.5 <= float(speed) <= 2.0:
        raise SystemExit("speed must be between 0.5 and 2.0.")


def _resolve_output_path(
    base_path: Path,
    options: VoiceoverOptions,
) -> Path:
    if options.output_path is not None:
        output_path = Path(options.output_path)
    else:
        output_dir = Path(options.output_dir) if options.output_dir is not None else None
        filename = options.filename
        if output_dir is None and filename is None:
            output_path = Path("out/output.wav")
        else:
            output_dir = output_dir or Path("out")
            filename = filename or "output.wav"
            output_path = output_dir / filename

    if not output_path.is_absolute():
        output_path = base_path / output_path
    return output_path
