from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from .collectors import _apify_run
from .utils import clamp, safe_num

VIDEO_TYPES = {"video", "reel", "reels", "clip"}
CREATIVE_WEIGHTS = {
    "hook": 0.20,
    "composition": 0.15,
    "shooting": 0.15,
    "editing": 0.20,
    "product_reveal": 0.10,
    "retention_loop": 0.10,
    "replicability": 0.10,
}


def _is_video(item: dict[str, Any]) -> bool:
    if str(item.get("platform") or "").lower() == "tiktok":
        return True
    return str(item.get("content_type") or "").lower() in VIDEO_TYPES


def _interaction_volume(item: dict[str, Any]) -> float:
    return (
        safe_num(item.get("likes"))
        + safe_num(item.get("comments"))
        + safe_num(item.get("shares"))
        + safe_num(item.get("saves"))
    )


def _candidate_sort(item: dict[str, Any]) -> tuple[float, float, float]:
    return (
        safe_num(item.get("heat_score")),
        safe_num(item.get("views")),
        _interaction_volume(item),
    )


def select_creative_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    max_total = max(1, int(os.environ.get("CREATIVE_MAX_VIDEOS", "12") or "12"))
    max_per_platform = max(1, int(os.environ.get("CREATIVE_MAX_PER_PLATFORM", "8") or "8"))
    videos = [x for x in items if _is_video(x) and x.get("url")]
    videos.sort(key=_candidate_sort, reverse=True)
    selected: list[dict[str, Any]] = []
    counts = {"instagram": 0, "tiktok": 0}
    for item in videos:
        platform = str(item.get("platform") or "").lower()
        if platform not in counts or counts[platform] >= max_per_platform:
            continue
        selected.append(item)
        counts[platform] += 1
        if len(selected) >= max_total:
            break
    return selected


def _nested(raw: dict[str, Any], dotted: str) -> Any:
    cur: Any = raw
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _tiktok_video_sources(items: list[dict[str, Any]]) -> dict[str, str]:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    urls = [str(x.get("url") or "") for x in items if x.get("platform") == "tiktok" and x.get("url")]
    if not token or not urls:
        return {}
    actor = os.environ.get("APIFY_TIKTOK_CREATIVE_ACTOR", "clockworks/tiktok-video-scraper").strip()
    payload = _apify_run(
        actor,
        token,
        {
            "postURLs": urls,
            "scrapeRelatedVideos": False,
            "resultsPerPage": 1,
            "shouldDownloadVideos": True,
            "shouldDownloadCovers": False,
            "shouldDownloadSlideshowImages": False,
            "downloadSubtitlesOptions": "NEVER_DOWNLOAD_SUBTITLES",
        },
    )
    mapped: dict[str, str] = {}
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        source = _nested(raw, "videoMeta.downloadAddr") or _nested(raw, "videoMeta.originalDownloadAddr")
        media = raw.get("mediaUrls")
        if not source and isinstance(media, list) and media:
            source = media[0]
        if not source:
            continue
        for key in (raw.get("submittedVideoUrl"), raw.get("webVideoUrl"), raw.get("url")):
            if key:
                mapped[str(key)] = str(source)
        video_id = str(raw.get("id") or "")
        if video_id:
            mapped[video_id] = str(source)
    return mapped


def _resolve_video_source(item: dict[str, Any], tiktok_map: dict[str, str]) -> str:
    if item.get("platform") == "tiktok":
        return tiktok_map.get(str(item.get("url") or "")) or tiktok_map.get(str(item.get("video_id") or "")) or ""
    return str(item.get("local_video") or "")


def _download_video(url: str, target: Path) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 LensSocialRadar/1.4", "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp, target.open("wb") as out:
            remaining = 80 * 1024 * 1024
            while remaining > 0:
                chunk = resp.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                out.write(chunk)
                remaining -= len(chunk)
        return target.exists() and target.stat().st_size > 1024
    except Exception:
        return False


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            timeout=30,
        ).strip()
        return max(0.0, float(out))
    except Exception:
        return 0.0


def _scene_cuts(path: Path) -> list[float]:
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-vf", "select='gt(scene,0.30)',showinfo",
        "-an", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception:
        return []
    cuts: list[float] = []
    for match in re.finditer(r"pts_time:([0-9.]+)", proc.stderr or ""):
        try:
            cuts.append(float(match.group(1)))
        except ValueError:
            pass
    return cuts[:40]


def _frame_times(duration: float, cuts: list[float], max_frames: int = 8) -> list[float]:
    if duration <= 0:
        return []
    base = [
        0.05,
        min(0.8, duration * 0.10),
        min(1.8, duration * 0.22),
        duration * 0.40,
        duration * 0.60,
        duration * 0.80,
        max(0.05, duration - 0.25),
    ]
    scene = [x + 0.03 for x in cuts[:4] if 0.05 < x < duration - 0.05]
    values = sorted({round(max(0.02, min(duration - 0.02, x)), 2) for x in base + scene})
    if len(values) <= max_frames:
        return values
    step = (len(values) - 1) / (max_frames - 1)
    return [values[round(i * step)] for i in range(max_frames)]


def _extract_frames(video: Path, directory: Path, times: list[float]) -> list[tuple[float, Path]]:
    out: list[tuple[float, Path]] = []
    for i, timestamp in enumerate(times):
        target = directory / f"frame-{i:02d}.jpg"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{timestamp:.2f}", "-i", str(video),
            "-frames:v", "1", "-vf", "scale='min(720,iw)':-2",
            "-q:v", "4", str(target),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=30)
        except Exception:
            continue
        if target.exists() and target.stat().st_size > 1000:
            out.append((timestamp, target))
    return out


def _image_data_url(path: Path) -> str:
    blob = path.read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(blob).decode("ascii")


def _extract_output_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []) or []:
        if item.get("type") == "message":
            for content in item.get("content", []) or []:
                if content.get("type") in ("output_text", "text") and content.get("text"):
                    return str(content["text"])
    return ""


def _clean_json_text(text: str) -> str:
    value = text.strip()
    fence = chr(96) * 3
    if value.startswith(fence):
        value = value.strip(chr(96)).strip()
        if value.startswith("json"):
            value = value[4:].lstrip()
    first = value.find("{")
    last = value.rfind("}")
    if first >= 0 and last > first:
        value = value[first:last + 1]
    return value
