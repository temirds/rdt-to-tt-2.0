from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Protocol

from .config import TranslationConfig
from .env import load_env_file


class Translator(Protocol):
    def translate(self, text: str, source_language: str, target_language: str) -> str:
        ...


@dataclass(frozen=True)
class PassthroughTranslator:
    def translate(self, text: str, source_language: str, target_language: str) -> str:
        return text


@dataclass
class LocalNllbTranslator:
    config: TranslationConfig
    _tokenizer: object | None = field(init=False, default=None, repr=False)
    _model: object | None = field(init=False, default=None, repr=False)
    _torch: object | None = field(init=False, default=None, repr=False)
    _runtime_device: str | None = field(init=False, default=None, repr=False)

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        text = text.strip()
        if not text:
            return text

        self._ensure_loaded()
        lines = text.splitlines()
        translated_lines: list[str] = []

        for line in lines:
            if not line.strip():
                translated_lines.append("")
                continue
            translated_lines.extend(self._translate_line(line, source_language, target_language))

        return "\n".join(translated_lines).strip()

    def _translate_line(self, line: str, source_language: str, target_language: str) -> list[str]:
        match = re.match(r"^(\d+\.\s+)([^:\n]{1,80}:)(.*)$", line)
        if match:
            index_prefix, author_prefix, body = match.groups()
            translated_body = self._translate_chunks(body.strip(), source_language, target_language)
            if not translated_body:
                return [line]
            return [f"{index_prefix}{author_prefix} {translated_body}".rstrip()]

        if re.match(r"^(u/|r/|https?://)", line.strip(), flags=re.IGNORECASE):
            return [line]

        return [self._translate_chunks(line, source_language, target_language)]

    def _translate_chunks(self, text: str, source_language: str, target_language: str) -> str:
        chunks = self._split_text(text)
        translated_chunks: list[str] = []

        for start in range(0, len(chunks), self.config.batch_size):
            batch = chunks[start : start + self.config.batch_size]
            translated_chunks.extend(self._translate_batch(batch, source_language, target_language))

        return " ".join(chunk.strip() for chunk in translated_chunks if chunk.strip()).strip()

    def _split_text(self, text: str) -> list[str]:
        if len(text) <= self.config.max_input_chars:
            return [text]

        parts: list[str] = []
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            sentence = sentence.strip()
            if not sentence:
                continue
            if not current:
                current = sentence
                continue
            candidate = f"{current} {sentence}"
            if len(candidate) <= self.config.max_input_chars:
                current = candidate
                continue
            parts.append(current)
            current = sentence
        if current:
            parts.append(current)
        return parts or [text[: self.config.max_input_chars]]

    def _translate_batch(self, texts: list[str], source_language: str, target_language: str) -> list[str]:
        tokenizer = self._tokenizer
        model = self._model
        torch = self._torch
        assert tokenizer is not None and model is not None and torch is not None

        source_code = self._resolve_language_code(source_language)
        target_code = self._resolve_language_code(target_language)
        tokenizer.src_lang = source_code

        encoded = tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=self.config.max_input_tokens,
        )
        if self._runtime_device and self._runtime_device != "cpu":
            encoded = {key: value.to(self._runtime_device) for key, value in encoded.items()}

        forced_bos_token_id = tokenizer.convert_tokens_to_ids(target_code)
        with torch.inference_mode():
            generated = model.generate(
                **encoded,
                forced_bos_token_id=forced_bos_token_id,
                max_new_tokens=self.config.max_new_tokens,
            )
        return tokenizer.batch_decode(generated, skip_special_tokens=True)

    def _ensure_loaded(self) -> None:
        if self._tokenizer is not None and self._model is not None and self._torch is not None:
            return

        try:
            import torch
            from huggingface_hub import snapshot_download
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError as exc:
            raise SystemExit(
                "Translator dependencies are missing. Install translator/requirements.txt first."
            ) from exc

        self.config.model_dir.mkdir(parents=True, exist_ok=True)
        token = os.getenv(self.config.hf_token_env, "").strip() or None
        local_config_path = self.config.model_dir / "config.json"
        if not local_config_path.exists():
            if self.config.local_files_only:
                raise SystemExit(
                    f"Model files not found in {self.config.model_dir} and local_files_only=true."
                )
            snapshot_download(
                repo_id=self.config.model_id,
                local_dir=str(self.config.model_dir),
                token=token,
            )
        model_source = str(self.config.model_dir)

        tokenizer = AutoTokenizer.from_pretrained(
            model_source,
            token=token,
            local_files_only=self.config.local_files_only,
        )
        model_kwargs = {
            "token": token,
            "local_files_only": self.config.local_files_only,
            "low_cpu_mem_usage": True,
        }
        torch_dtype = self._resolve_torch_dtype(torch)
        if torch_dtype is not None:
            model_kwargs["torch_dtype"] = torch_dtype

        runtime_device = self._resolve_runtime_device(torch)
        if self.config.device_map and self.config.device_map != "none":
            model_kwargs["device_map"] = self.config.device_map

        model = AutoModelForSeq2SeqLM.from_pretrained(model_source, **model_kwargs)
        if "device_map" not in model_kwargs and runtime_device != "cpu":
            model = model.to(runtime_device)

        self._tokenizer = tokenizer
        self._model = model
        self._torch = torch
        self._runtime_device = runtime_device

    def _resolve_runtime_device(self, torch: object) -> str:
        requested = self.config.device.strip().lower()
        if requested != "auto":
            return requested
        return "cuda" if torch.cuda.is_available() else "cpu"

    def _resolve_torch_dtype(self, torch: object) -> object | None:
        requested = self.config.torch_dtype.strip().lower()
        if requested == "auto":
            return torch.float16 if torch.cuda.is_available() else torch.float32
        if requested == "float16":
            return torch.float16
        if requested == "bfloat16":
            return torch.bfloat16
        if requested == "float32":
            return torch.float32
        return None

    @staticmethod
    def _resolve_language_code(language: str) -> str:
        normalized = language.strip().lower()
        mapping = {
            "en": "eng_Latn",
            "english": "eng_Latn",
            "ru": "rus_Cyrl",
            "russian": "rus_Cyrl",
        }
        if normalized not in mapping:
            raise SystemExit(f"Unsupported translation language: {language}")
        return mapping[normalized]


class TranslatorService:
    def __init__(self, config: TranslationConfig | None = None, translator: Translator | None = None) -> None:
        self.config = config or TranslationConfig()
        load_env_file(self.config.env_path)
        self.translator = translator or self._build_translator(self.config)

    def translate(self, text: str, source_language: str, target_language: str | None = None) -> str:
        return self.translator.translate(
            text,
            source_language=source_language or self.config.default_source_language,
            target_language=target_language or self.config.target_language,
        )

    @staticmethod
    def _build_translator(config: TranslationConfig) -> Translator:
        if config.backend == "none":
            return PassthroughTranslator()
        if config.backend == "local_nllb":
            return LocalNllbTranslator(config)
        raise SystemExit(f"Unsupported translator backend: {config.backend}")
