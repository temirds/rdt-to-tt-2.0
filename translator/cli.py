from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_translation_settings, merge_config
from .service import TranslatorService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate text to Russian with a local NLLB model.")
    parser.add_argument("--config", default=None, help="Path to translator config.json.")
    parser.add_argument("--text", default=None, help="Text to translate.")
    parser.add_argument("--file", default=None, help="Path to a UTF-8 text file to translate.")
    parser.add_argument("--source-language", default=None, help="Source language, e.g. en.")
    parser.add_argument("--target-language", default=None, help="Target language, e.g. ru.")
    parser.add_argument("--model-dir", default=None, help="Local directory for the model files.")
    parser.add_argument("--local-files-only", action="store_true", help="Do not download files from Hugging Face.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    loaded = load_translation_settings(Path(args.config) if args.config else Path("translator/config.json"))
    config = merge_config(
        loaded.config,
        model_dir=Path(args.model_dir).resolve() if args.model_dir else None,
        local_files_only=True if args.local_files_only else None,
    )
    source_language = args.source_language or config.default_source_language
    target_language = args.target_language or config.target_language
    text = _load_text(args)
    service = TranslatorService(config=config)
    translated = service.translate(text, source_language=source_language, target_language=target_language)
    print(f"Config: {loaded.path}")
    print(f"Model dir: {config.model_dir}")
    print()
    print(translated)
    return 0


def _load_text(args: argparse.Namespace) -> str:
    if args.text:
        return args.text
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    raise SystemExit("Pass --text or --file.")


if __name__ == "__main__":
    raise SystemExit(main())
