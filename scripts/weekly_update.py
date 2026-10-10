#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, time, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analyzer import ai_analysis
from src.collectors import collect_apify, collect_demo, collect_incoming, normalize
from src.date_window import previous_tuesday_to_sunday
from src.ranking import score_items
from src.utils import load_json, save_json


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build the weekly Lens Social Radar dataset")
    p.add_argument("--today", help="Override today in YYYY-MM-DD for testing")
    p.add_argument("--mode", choices=["demo", "incoming", "apify"], help="Override COLLECTION_MODE")
    return p.parse_args()


def in_window(item: dict, start: date, end: date) -> bool:
    raw_value = item.get("posted_at")
    if raw_value in (None, ""):
        return False

    # Numeric Unix timestamps occasionally arrive from TikTok-style feeds.
    if isinstance(raw_value, (int, float)):
        try:
            d = datetime.fromtimestamp(float(raw_value), tz=timezone.utc).date()
            return start <= d <= end
        except (OverflowError, OSError, ValueError):
            return True

    raw = str(raw_value).strip()
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        return start <= d <= end
    except ValueError:
        pass

    # Pinterest commonly returns RFC 2822 dates such as:
    # "Fri, 28 Aug 2026 03:05:27 +0000".
    try:
        d = parsedate_to_datetime(raw).date()
        return start <= d <= end
    except (TypeError, ValueError, OverflowError):
        return True


def assign_brand_region(items: list[dict], brand_cfg: dict) -> list[dict]:
    lookup = {}
    for region, brands in (brand_cfg.get("regions") or {}).items():
        for entry in brands:
            brand = entry.get("brand")
            for platform, handle in (entry.get("handles") or {}).items():
                if handle:
                    lookup[(platform.lower(), handle.lower().lstrip("@"))] = (brand, region)
    out = []
    for item in items:
        x = normalize(item, item.get("platform"))
        key = (str(x.get("platform", "")).lower(), str(x.get("author", "")).lower().lstrip("@"))
        if key in lookup:
            brand, region = lookup[key]
            x["brand"] = brand
            x["region"] = region
        out.append(x)
    return out


def _visible_in_dashboard(item: dict) -> bool:
    return int(item.get("platform_rank") or 9999) <= 10 or int(item.get("rank") or 9999) <= 3


def hydrate_tiktok_oembed(items: list[dict]) -> None:
    """For public TikTok posts, use official oEmbed metadata as a cover fallback.

    This does not bypass login, age, regional or privacy restrictions. Failures are
    deliberately non-fatal because ranking can still use metrics/caption metadata.
    """
    for item in items:
        if item.get("platform") != "tiktok" or not _visible_in_dashboard(item):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        item.setdefault("mobile_url", url)
        if item.get("thumbnail"):
            continue
        endpoint = "https://www.tiktok.com/oembed?url=" + urllib.parse.quote(url, safe="")
        req = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0 LensSocialRadar/1.1"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("thumbnail_url"):
                item["thumbnail"] = payload["thumbnail_url"]
            if not item.get("caption") and payload.get("title"):
                item["caption"] = payload["title"]
            if not item.get("author") and payload.get("author_name"):
                item["author"] = payload["author_name"]
        except Exception:
            item.setdefault("access_mode", "mobile")


def _image_extension(content_type: str, source_url: str) -> str:
    c = (content_type or "").lower()
    if "webp" in c:
        return ".webp"
    if "png" in c:
        return ".png"
    if "gif" in c:
        return ".gif"
    if "jpeg" in c or "jpg" in c:
        return ".jpg"
    suffix = Path(urllib.parse.urlparse(source_url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def cache_dashboard_thumbnails(items: list[dict]) -> None:
    """Cache covers used by the dashboard so expiring CDN URLs do not break the report."""
    media_dir = ROOT / "docs" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    for item in items:
        if not _visible_in_dashboard(item):
            continue
        src = str(item.get("thumbnail") or "").strip()
        if not src.startswith(("http://", "https://")):
            continue
        key = hashlib.sha1(src.encode("utf-8")).hexdigest()[:18]
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0 LensSocialRadar/1.1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                content_type = resp.headers.get("Content-Type", "")
                blob = resp.read(8 * 1024 * 1024 + 1)
            if len(blob) > 8 * 1024 * 1024:
                continue
            ext = _image_extension(content_type, src)
            path = media_dir / f"{item.get('platform','post')}-{key}{ext}"
            path.write_bytes(blob)
            item["thumbnail_source_url"] = src
            item["thumbnail"] = f"media/{path.name}"
        except Exception:
            # Keep the original URL as a best-effort fallback.
            pass



def _post_archive_key(item: dict) -> str:
    """Stable-enough key for de-duplicating the searchable historical case library."""
    platform = str(item.get("platform") or "unknown").lower()
    if item.get("url"):
        raw = f"{platform}|{item.get('url')}"
    elif item.get("video_id"):
        raw = f"{platform}|{item.get('author')}|{item.get('video_id')}"
    else:
        raw = f"{platform}|{item.get('author')}|{item.get('posted_at')}|{item.get('caption')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def update_search_archive(items: list[dict], start: date, end: date, mode: str) -> Path:
    """Persist a lightweight historical search index used by the homepage case-library search.

    Demo mode intentionally replaces the archive so repeated local testing does not create
    fake historical weeks. Real collection modes merge newly observed posts by stable key.
    """
    path = ROOT / "docs" / "data" / "archive.json"
    existing = {"posts": []}
    if mode != "demo":
        existing = load_json(path, {"posts": []}) or {"posts": []}
    merged = {}
    for old in existing.get("posts", []) if isinstance(existing, dict) else []:
        if not isinstance(old, dict):
            continue
        key = old.get("archive_id") or _post_archive_key(old)
        merged[key] = old
    for item in items:
        snap = dict(item)
        snap["archive_id"] = _post_archive_key(snap)
        snap["week_start"] = start.isoformat()
        snap["week_end"] = end.isoformat()
        merged[snap["archive_id"]] = snap
    rows = list(merged.values())
    rows.sort(key=lambda x: (str(x.get("posted_at") or ""), float(x.get("score") or 0)), reverse=True)
    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "collection_mode": mode,
            "description": "Historical searchable Lens Social Radar case library",
            "count": len(rows),
        },
        "posts": rows,
    }
    save_json(path, payload)
    return path

def build() -> Path:
    args = parse_args()
    today = date.fromisoformat(args.today) if args.today else date.today()
    start, end = previous_tuesday_to_sunday(today)
    mode = args.mode or os.environ.get("COLLECTION_MODE", "demo").strip().lower()

    brands = load_json(ROOT / "config/brands.json", {})
    keywords = load_json(ROOT / "config/keywords.json", {})

    if mode == "demo":
        raw_items = collect_demo(start, end)
    elif mode == "incoming":
        raw_items = collect_incoming(ROOT / "data/incoming")
    elif mode == "apify":
        raw_items = collect_apify(start, end, brands, keywords)
    else:
        raise SystemExit(f"Unsupported mode: {mode}")

    items = assign_brand_region(raw_items, brands)
    items = [x for x in items if in_window(x, start, end)]
    as_of = datetime.combine(end, time(23, 59, 59), tzinfo=timezone.utc)
    ranked = score_items(items, as_of=as_of)

    # Covers and links are presentation metadata; ranking is already complete.
    hydrate_tiktok_oembed(ranked)
    cache_dashboard_thumbnails(ranked)

    date_range = f"{start.isoformat()} 至 {end.isoformat()}"
    eligible_ranked = [x for x in ranked if x.get("ranking_eligible")]
    analysis = ai_analysis(eligible_ranked, date_range)

    platform_counts = Counter(str(x.get("platform")) for x in ranked)
    eligible_platform_counts = Counter(str(x.get("platform")) for x in eligible_ranked)
    region_counts = Counter(str(x.get("region")) for x in ranked)
    type_counts = Counter(str(x.get("content_type") or "unknown") for x in ranked)
    brands_count = len({str(x.get("brand")) for x in ranked if x.get("brand")})

    payload = {
        "meta": {
            "title": "Lens Social Radar",
            "date_start": start.isoformat(),
            "date_end": end.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "collection_mode": mode,
            "is_demo": mode == "demo",
            "ranking_method": "Platform-specific heat ranking. Missing metrics remain missing; tied values share the same percentile; only data-qualified posts can enter platform TOP10 / overall TOP3. Creative/video-analysis score is separate and not yet included in heat ranking.",
            "leaderboard_layout": "Instagram TOP10 + TikTok TOP10 + Pinterest TOP10 + cross-platform TOP3",
        },
        "stats": {
            "posts": len(ranked),
            "ranking_eligible": len(eligible_ranked),
            "brands": brands_count,
            "platforms": dict(platform_counts),
            "eligible_platforms": dict(eligible_platform_counts),
            "regions": dict(region_counts),
            "content_types": dict(type_counts),
        },
        "analysis": analysis,
        "posts": ranked,
    }
    out = ROOT / "docs/data/weekly.json"
    save_json(out, payload)
    archive = update_search_archive(ranked, start, end, mode)
    print(f"Wrote {out} with {len(ranked)} posts ({date_range}, mode={mode})")
    print(f"Updated search archive: {archive}")
    return out


if __name__ == "__main__":
    build()
