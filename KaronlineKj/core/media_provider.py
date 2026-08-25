from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path


class MediaProvider(ABC):
    @abstractmethod
    def fetch_mp4(self, title: str) -> Path:
        """Download or resolve one MP4 file and return a local path."""


class LanMediaProvider(MediaProvider):
    def __init__(self, server: str, port: int = 8765, cache_dir: Path | None = None):
        self.server = str(server).strip().rstrip("/")
        self.port = int(port)
        default_cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "KaronlineBox" / "cache"
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache

    @staticmethod
    def _safe_filename(value: str, fallback: str = "downloaded.mp4") -> str:
        name = str(value or fallback).strip()
        if not name:
            name = fallback
        name = name.replace("\\", "/")
        name = name.split("/")[-1]
        name = name.strip()
        if not name.lower().endswith(".mp4"):
            name = f"{name}.mp4"
        name = re.sub(r"[^A-Za-z0-9._()\- \[\]@]+", "_", name)
        return name or fallback

    def fetch_mp4(self, title: str) -> Path:
        if not str(title or "").strip():
            raise ValueError("TITLE_REQUIRED")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"title": str(title).strip()}).encode("utf-8")
        request = urllib.request.Request(
            f"http://{self.server}:{self.port}/request",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            print(f"REQUEST SENT = {title}", flush=True)
            with urllib.request.urlopen(request, timeout=30) as response:
                filename = response.headers.get_filename() or str(title).strip()
                target_name = self._safe_filename(filename, str(title).strip() or "downloaded.mp4")
                target = self.cache_dir / target_name
                print(f"FILE FOUND = {target_name}", flush=True)
                print("DOWNLOAD STARTED", flush=True)
                with target.open("wb") as destination:
                    while chunk := response.read(1024 * 1024):
                        destination.write(chunk)
                print(f"DOWNLOAD COMPLETED = {target}", flush=True)
                if not target.exists():
                    raise FileNotFoundError(f"CACHE FILE MISSING = {target}")
                print(f"CACHE FILE = {target}", flush=True)
                print(f"CACHE SIZE = {target.stat().st_size}", flush=True)
                return target
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError("FILE NOT FOUND") from exc
            raise RuntimeError(f"DOWNLOAD ERROR = HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise ConnectionError(f"SERVER INACCESSIBLE = {exc}") from exc
