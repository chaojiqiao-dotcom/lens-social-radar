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
