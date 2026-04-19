from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime

from voiceover import VoiceoverOptions, generate_voiceover


def main() -> int:
    # args = parse_args()

    root_path = Path(__file__).resolve().parent

    result = generate_voiceover(
            f"Мне тогда было 10 лет, я пришёл на первую тренировку по бейсболу в сезоне. Большую часть времени я был кетчером, и отец сказал: Если у них не будет экипировки для кетчера, не становись кетчером.",
            VoiceoverOptions(
                voice="upvote_2",
                pitch=0,
                speed=0.98,
                output_path = root_path / "out/output-{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
            ),
        )

    print(f"Готово: {result.output_path}")
    print(f"Длительность: {result.duration_seconds:.2f} сек")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Russian TTS with CosyVoice3.")
    parser.add_argument("--voice", required=True, help="Voice name from voiceover voices config.")
    parser.add_argument("--pitch", type=float, default=0.0, help="Pitch shift in semitones, from -12 to 12.")
    parser.add_argument("--speed", type=float, default=1.0, help="Speech speed multiplier, from 0.5 to 2.0.")
    parser.add_argument("--text", help="Text to synthesize.")
    parser.add_argument("--file", help="UTF-8 text file to synthesize.")
    parser.add_argument("-o", "--output", help="Output WAV path.")
    return parser.parse_args()


def resolve_text(args: argparse.Namespace) -> str:
    if args.text:
        text = args.text
    elif args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise SystemExit("Pass --text, --file, or pipe stdin.")

    text = text.strip()
    if not text:
        raise SystemExit("Text is empty.")
    return text


if __name__ == "__main__":
    raise SystemExit(main())
