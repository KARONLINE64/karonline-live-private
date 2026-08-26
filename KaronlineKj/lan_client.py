from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Karonline LAN MP4 client")
    parser.add_argument("--server", required=True, help="LAN IP address of the server PC")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--request", required=True, help="Exact MP4 filename or title")
    parser.add_argument("--cache", type=Path, default=Path(__file__).resolve().parent / ".karonline_cache")
    parser.add_argument("--no-play", action="store_true")
    args = parser.parse_args()

    print("CLIENT STARTED", flush=True)
    print(f"SERVER = {args.server}:{args.port}", flush=True)
    print(f"REQUEST = {args.request}", flush=True)
    payload = json.dumps({"title": args.request}).encode("utf-8")
    request = urllib.request.Request(
        f"http://{args.server}:{args.port}/request",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print("RESPONSE RECEIVED", flush=True)
            filename = response.headers.get_filename() or Path(args.request).name
            if not filename.casefold().endswith(".mp4"):
                filename += ".mp4"
            args.cache.mkdir(parents=True, exist_ok=True)
            target = args.cache / Path(filename).name
            print("DOWNLOAD STARTED", flush=True)
            with target.open("wb") as destination:
                while chunk := response.read(1024 * 1024):
                    destination.write(chunk)
            print(f"DOWNLOAD COMPLETED = {target}", flush=True)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print("FILE NOT FOUND", flush=True)
        else:
            print(f"TRANSFER ERROR = HTTP {exc.code}", flush=True)
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"SERVER UNREACHABLE = {exc}", flush=True)
        return 1

    if not args.no_play:
        print("PLAYBACK STARTED", flush=True)
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("app.py")), "--file", str(target)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())