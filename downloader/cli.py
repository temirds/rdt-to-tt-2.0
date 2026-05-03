from __future__ import annotations

import argparse
from pathlib import Path

from .api import available_providers, download, fetch_info
from .base import DownloadOptions
from .config import DEFAULT_CONFIG_PATH, load_downloader_settings
from .cookies import convert_json_cookies


def main() -> int:
    args = parse_args()
    if args.parse_cookies:
        output_path = convert_json_cookies(args.parse_cookies, args.cookies_output)
        print(f"Cookies parsed: {output_path}")
        return 0

    if args.config:
        loaded = load_downloader_settings(Path(args.config))
        config = loaded.config
        default_output_dir = config.default_output_dir
    else:
        default_output_dir = load_downloader_settings(DEFAULT_CONFIG_PATH).config.default_output_dir

    output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir
    options = DownloadOptions(
        provider=args.provider,
        config_path=Path(args.config).resolve() if args.config else None,
        output_dir=output_dir,
        quality=args.quality,
        format_selector=args.format,
        with_audio=args.with_audio,
        cookies_path=Path(args.cookies).resolve() if args.cookies else None,
        no_playlist=not args.playlist,
        ffmpeg_location=Path(args.ffmpeg_location).resolve() if args.ffmpeg_location else None,
    )

    if args.info:
        for url in args.urls:
            info = fetch_info(url, options)
            print(f"URL: {url}")
            print(f"Title: {info.get('title')}")
            print(f"ID: {info.get('id')}")
            print(f"Duration: {info.get('duration')}")
        return 0

    results = download(args.urls, options)
    failed = 0
    for result in results:
        if result.success:
            print(f"Downloaded: {result.title or result.url}")
            for path in result.filepaths:
                print(f"- {path}")
        else:
            failed += 1
            print(f"Failed: {result.url}")
            print(result.error)
    return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download media into a local folder.")
    parser.add_argument("urls", nargs="*", help="Media URL. Can be repeated.")
    parser.add_argument("--provider", default="youtube", choices=available_providers(), help="Downloader provider.")
    parser.add_argument("-o", "--output-dir", help="Folder for downloaded videos. Defaults to out/youtube.")
    parser.add_argument("-q", "--quality", default=None, help="Quality: best, 2160p, 1440p, 1080p, 720p, 480p, 360p.")
    parser.add_argument("--format", help="Raw yt-dlp format selector. Overrides --quality.")
    parser.add_argument("--with-audio", action="store_true", default=None, help="Download video with audio.")
    parser.add_argument("--cookies", help="Path to Netscape cookies.txt.")
    parser.add_argument("--playlist", action="store_true", help="Allow playlist downloads.")
    parser.add_argument("--ffmpeg-location", help="Path to ffmpeg folder or executable for yt-dlp.")
    parser.add_argument("--config", help="Path to downloader config JSON.")
    parser.add_argument("--info", action="store_true", help="Fetch metadata only, do not download.")
    parser.add_argument("--parse-cookies", help="Convert browser-extension cookies.json to Netscape cookies.txt.")
    parser.add_argument("--cookies-output", help="Output path for parsed cookies.txt.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
