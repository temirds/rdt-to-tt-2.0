from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import VoiceoverRequest, VoiceoverResult, VoiceoverSequenceRequest
from .config import resolve_project_path


COSYVOICE_PYTHON = Path(".venv-cosyvoice") / "Scripts" / "python.exe"
REEXEC_ENV = "RDT_TTS_REEXEC"


@dataclass(frozen=True)
class CosyVoiceSettings:
    repo_path: Path
    model_path: Path
    python_path: Path
    cache_dir: Path
    device: str
    default_instruction: str
    max_prompt_seconds: float = 12.0
    max_prompt_chars: int = 80
    log_level: str = "INFO"
    modelscope_local_files_only: bool = True
    show_progress: bool = False

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        base_path: Path = Path("."),
    ) -> "CosyVoiceSettings":
        required = ["cosyvoice_repo", "cosyvoice_model", "default_instruction"]
        missing = [key for key in required if key not in config]
        if missing:
            raise SystemExit(f"Config is missing required keys: {', '.join(missing)}")

        return cls(
            repo_path=resolve_project_path(config["cosyvoice_repo"], base_path),
            model_path=resolve_project_path(config["cosyvoice_model"], base_path),
            python_path=resolve_project_path(config.get("python", COSYVOICE_PYTHON), base_path),
            cache_dir=resolve_project_path(config.get("cache_dir", ".cache"), base_path),
            device=str(config.get("device", "cpu")),
            default_instruction=str(config["default_instruction"]),
            max_prompt_seconds=float(config.get("max_prompt_seconds", 12.0)),
            max_prompt_chars=int(config.get("max_prompt_chars", 80)),
            log_level=str(config.get("log_level", "INFO")),
            modelscope_local_files_only=bool(config.get("modelscope_local_files_only", True)),
            show_progress=bool(config.get("show_progress", False)),
        )


class CosyVoiceVoiceoverEngine:
    def __init__(self, settings: CosyVoiceSettings) -> None:
        self.settings = settings

    def synthesize_to_file(self, request: VoiceoverRequest) -> VoiceoverResult:
        apply_local_cache(self.settings)
        add_cosyvoice_to_path(self.settings)

        prompt_audio, prompt_text, temp_prompt = prepare_prompt(
            audio_path=request.voice.audio_path,
            prompt_text=request.voice.prompt_text,
            instruction=self.settings.default_instruction,
            max_seconds=self.settings.max_prompt_seconds,
            max_chars=self.settings.max_prompt_chars,
            target_text=request.text,
        )
        try:
            model = load_model(self.settings)
            audio, sample_rate = synthesize_with_model(
                model=model,
                text=request.text,
                prompt_audio=prompt_audio,
                prompt_text=prompt_text,
                speed=request.speed,
            )
            audio = apply_pitch_shift(audio, sample_rate, request.pitch)

            duration = audio.shape[-1] / sample_rate
            if duration < 0.5:
                raise SystemExit(f"CosyVoice returned too short audio: {duration:.2f}s")

            save_wav(request.output_path, audio, sample_rate)
            return VoiceoverResult(
                output_path=request.output_path,
                duration_seconds=duration,
                sample_rate=sample_rate,
            )
        finally:
            if temp_prompt is not None:
                temp_prompt.cleanup()

    def synthesize_sequence_to_file(self, request: VoiceoverSequenceRequest) -> VoiceoverResult:
        apply_local_cache(self.settings)
        add_cosyvoice_to_path(self.settings)

        prompt_audio, prompt_text, temp_prompt = prepare_prompt(
            audio_path=request.voice.audio_path,
            prompt_text=request.voice.prompt_text,
            instruction=self.settings.default_instruction,
            max_seconds=self.settings.max_prompt_seconds,
            max_chars=self.settings.max_prompt_chars,
            target_text=min((segment.text for segment in request.segments), key=len),
        )
        try:
            model = load_model(self.settings)
            audio_parts, sample_rate, timings = synthesize_sequence(
                model=model,
                request=request,
                prompt_audio=prompt_audio,
                prompt_text=prompt_text,
            )
            audio = concatenate_audio_parts(audio_parts)
            audio = apply_pitch_shift(audio, sample_rate, request.pitch)

            duration = audio.shape[-1] / sample_rate
            if duration < 0.5:
                raise SystemExit(f"CosyVoice returned too short audio: {duration:.2f}s")

            save_wav(request.output_path, audio, sample_rate)
            save_sequence_timings(request.output_path, timings)
            return VoiceoverResult(
                output_path=request.output_path,
                duration_seconds=duration,
                sample_rate=sample_rate,
            )
        finally:
            if temp_prompt is not None:
                temp_prompt.cleanup()


def reexec_in_cosyvoice_python(
    settings: CosyVoiceSettings,
    script_path: Path,
    argv: list[str],
) -> None:
    current = Path(sys.executable).resolve()
    target = settings.python_path.resolve()

    if os.environ.get(REEXEC_ENV) == "1" or current == target:
        return
    if not target.exists():
        raise SystemExit(
            f"CosyVoice Python not found: {target}\n"
            "Expected local environment in .venv-cosyvoice."
        )

    env = os.environ.copy()
    env[REEXEC_ENV] = "1"
    result = subprocess.run([str(target), str(script_path.resolve()), *argv], env=env)
    raise SystemExit(result.returncode)


def apply_local_cache(settings: CosyVoiceSettings) -> None:
    cache_root = settings.cache_dir.resolve()
    os.environ.setdefault("HF_HOME", str(cache_root / "huggingface"))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(cache_root / "huggingface" / "hub"))
    os.environ.setdefault("MODELSCOPE_CACHE", str(cache_root / "modelscope"))
    os.environ.setdefault("TORCH_HOME", str(cache_root / "torch"))
    os.environ.setdefault("PIP_CACHE_DIR", str(cache_root / "pip"))

    if settings.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""


def configure_third_party_runtime(settings: CosyVoiceSettings) -> None:
    import logging

    logging.getLogger().setLevel(_resolve_log_level(settings.log_level))
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("modelscope").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.ERROR)

    if settings.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        try:
            import torch

            torch.cuda.is_available = lambda: False
        except ImportError:
            pass

    if settings.modelscope_local_files_only:
        patch_modelscope_snapshot_download_for_cache(settings.cache_dir)


def _resolve_log_level(value: str) -> int:
    import logging

    return getattr(logging, value.strip().upper(), logging.ERROR)


def patch_modelscope_snapshot_download_for_cache(cache_dir: Path) -> None:
    wetext_cache = cache_dir.resolve() / "modelscope" / "hub" / "pengzhendong" / "wetext"
    if not wetext_cache.exists():
        return

    try:
        import modelscope
    except ImportError:
        return

    original_snapshot_download = modelscope.snapshot_download

    def snapshot_download_local_first(repo_id: str, *args, **kwargs):
        if repo_id == "pengzhendong/wetext":
            return str(wetext_cache)
        return original_snapshot_download(repo_id, *args, **kwargs)

    modelscope.snapshot_download = snapshot_download_local_first


def add_cosyvoice_to_path(settings: CosyVoiceSettings) -> None:
    repo = settings.repo_path.resolve()
    if not repo.exists():
        raise SystemExit(f"CosyVoice repo not found: {repo}")

    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "third_party" / "Matcha-TTS"))


def prepare_prompt(
    audio_path: Path,
    prompt_text: str,
    instruction: str,
    max_seconds: float,
    max_chars: int,
    target_text: str | None = None,
) -> tuple[str, str, tempfile.TemporaryDirectory[str] | None]:
    import torchaudio

    audio, sample_rate = torchaudio.load(str(audio_path))
    duration = audio.shape[-1] / sample_rate
    max_chars = resolve_prompt_char_limit(
        prompt_text=prompt_text,
        instruction=instruction,
        configured_max_chars=max_chars,
        target_text=target_text,
    )

    sentences = split_sentences(prompt_text) or [prompt_text]
    total_chars = max(1, sum(len(sentence) for sentence in sentences))

    selected: list[str] = []
    selected_chars = 0
    for sentence in sentences:
        projected_seconds = duration * (selected_chars + len(sentence)) / total_chars
        projected_chars = selected_chars + len(sentence)
        if selected and (projected_seconds > max_seconds or projected_chars > max_chars):
            break
        selected.append(sentence)
        selected_chars += len(sentence)

    shortened_text = " ".join(selected).strip() or sentences[0]
    if len(shortened_text) > max_chars:
        shortened_text = shorten_text_by_chars(shortened_text, max_chars)
        selected_chars = len(shortened_text)

    if duration <= max_seconds and shortened_text == prompt_text.strip():
        return str(audio_path.resolve()), join_instruction(instruction, prompt_text), None

    shortened_seconds = min(duration, max_seconds, duration * selected_chars / total_chars)
    frames = max(1, int(shortened_seconds * sample_rate))

    temp_dir: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
    temp_path = Path(temp_dir.name) / "prompt.wav"
    torchaudio.save(
        str(temp_path),
        audio[..., :frames],
        sample_rate,
        encoding="PCM_S",
        bits_per_sample=16,
    )

    return str(temp_path), join_instruction(instruction, shortened_text), temp_dir


def join_instruction(instruction: str, prompt_text: str) -> str:
    instruction = instruction.strip()
    prompt_text = prompt_text.strip()
    if "<|endofprompt|>" in prompt_text:
        return prompt_text
    if not instruction.endswith("<|endofprompt|>"):
        instruction = f"{instruction}<|endofprompt|>"
    return f"{instruction}{prompt_text}"


def load_model(settings: CosyVoiceSettings):
    if settings.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    configure_third_party_runtime(settings)
    from cosyvoice.cli import cosyvoice as cosyvoice_cli

    configure_third_party_runtime(settings)
    if not settings.show_progress:
        cosyvoice_cli.tqdm = lambda iterable, *args, **kwargs: iterable
    return cosyvoice_cli.AutoModel(model_dir=str(settings.model_path.resolve()))


def synthesize(
    model_dir: Path,
    text: str,
    prompt_audio: str,
    prompt_text: str,
    device: str,
    speed: float,
):
    settings = CosyVoiceSettings(
        repo_path=Path("."),
        model_path=model_dir,
        python_path=COSYVOICE_PYTHON,
        cache_dir=Path("cache"),
        device=device,
        default_instruction="",
    )
    model = load_model(settings)
    return synthesize_with_model(
        model=model,
        text=text,
        prompt_audio=prompt_audio,
        prompt_text=prompt_text,
        speed=speed,
    )


def synthesize_with_model(
    model,
    text: str,
    prompt_audio: str,
    prompt_text: str,
    speed: float,
):
    import torch

    parts = []
    for item in model.inference_zero_shot(
        text,
        prompt_text,
        str(Path(prompt_audio).resolve()),
        stream=False,
        speed=float(speed),
    ):
        parts.append(item["tts_speech"].detach().cpu())

    if not parts:
        raise SystemExit("CosyVoice returned no audio.")

    audio = parts[0] if len(parts) == 1 else torch.cat(parts, dim=-1)
    return audio, model.sample_rate


def synthesize_sequence(
    model,
    request: VoiceoverSequenceRequest,
    prompt_audio: str,
    prompt_text: str,
):
    import torch

    audio_parts = []
    sample_rate: int | None = None
    channel_count: int | None = None
    cursor_frames = 0
    timings: list[dict[str, float | str]] = []

    for segment in request.segments:
        audio, current_sample_rate = synthesize_with_model(
            model=model,
            text=segment.text,
            prompt_audio=prompt_audio,
            prompt_text=prompt_text,
            speed=request.speed,
        )
        if sample_rate is None:
            sample_rate = current_sample_rate
            channel_count = audio.shape[0]
        audio_parts.append(audio)
        start_frames = cursor_frames
        cursor_frames += audio.shape[-1]
        end_frames = cursor_frames
        timings.append(
            {
                "text": segment.text,
                "start_seconds": start_frames / sample_rate,
                "end_seconds": end_frames / sample_rate,
                "pause_after_seconds": max(0.0, float(segment.pause_after_seconds)),
            }
        )

        pause_seconds = float(segment.pause_after_seconds)
        if pause_seconds > 0:
            pause_frames = max(1, int(round(sample_rate * pause_seconds)))
            audio_parts.append(torch.zeros((channel_count, pause_frames), dtype=audio.dtype))
            cursor_frames += pause_frames

    if sample_rate is None or channel_count is None or not audio_parts:
        raise SystemExit("CosyVoice returned no audio.")

    return audio_parts, sample_rate, timings


def save_sequence_timings(output_path: Path, timings: list[dict[str, float | str]]) -> None:
    timings_path = output_path.with_suffix(".timings.json")
    timings_path.write_text(
        json.dumps({"segments": timings}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def concatenate_audio_parts(audio_parts):
    import torch

    if not audio_parts:
        raise SystemExit("CosyVoice returned no audio.")
    return audio_parts[0] if len(audio_parts) == 1 else torch.cat(audio_parts, dim=-1)


def apply_pitch_shift(audio, sample_rate: int, pitch: float):
    pitch = float(pitch)
    if pitch == 0.0:
        return audio

    try:
        from torchaudio.functional import pitch_shift
    except ImportError as exc:
        raise SystemExit(
            "pitch requires torchaudio.functional.pitch_shift in the voiceover environment."
        ) from exc

    return pitch_shift(audio, sample_rate, n_steps=pitch)


def save_wav(path: Path, audio, sample_rate: int) -> None:
    import torchaudio

    path.parent.mkdir(parents=True, exist_ok=True)
    torchaudio.save(
        str(path),
        audio,
        sample_rate,
        encoding="PCM_S",
        bits_per_sample=16,
    )


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def shorten_text_by_chars(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text.strip()

    shortened = text[:max_chars].rsplit(" ", 1)[0].strip()
    return shortened or text[:max_chars].strip()


def resolve_prompt_char_limit(
    prompt_text: str,
    instruction: str,
    configured_max_chars: int,
    target_text: str | None,
) -> int:
    if target_text is None:
        return configured_max_chars

    instruction_len = len(join_instruction(instruction, ""))
    target_len = len(target_text.strip())
    warning_safe_limit = max(1, 2 * target_len - instruction_len)
    return min(configured_max_chars, len(prompt_text.strip()), warning_safe_limit)
