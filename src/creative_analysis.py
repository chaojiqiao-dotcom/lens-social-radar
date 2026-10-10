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
    """Analyze only the overall Heat Score #1 post.

    If overall #1 is not a video (or has no usable URL), Creative Analysis is
    skipped for that week instead of silently analyzing another post.
    """
    overall_top = next(
        (
            x for x in items
            if int(safe_num(x.get("rank"))) == 1
        ),
        None,
    )
    if not overall_top:
        return []
    if not _is_video(overall_top):
        return []
    if not overall_top.get("url"):
        return []
    return [overall_top]


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


def _call_openai(
    item: dict[str, Any],
    frames: list[tuple[float, Path]],
    duration: float,
    cuts: list[float],
) -> dict[str, Any] | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or not frames:
        return None

    model = os.environ.get("OPENAI_CREATIVE_MODEL", "gpt-5.6-sol").strip()
    estimated_cut_count = len(cuts)
    avg_shot = round(duration / (estimated_cut_count + 1), 2) if duration > 0 else None

    rubric = """你是一名专做 TikTok / Instagram 美妆、美瞳内容的资深剪辑师与导演。
你正在为剪辑师建立“值得研究”的案例库，而不是判断这个人长得好不好看。

根据按时间顺序提供的视频关键帧，以及视频时长、估算剪切点、平台数据和文案，分析：
1. 首帧与前3秒 Hook；
2. 景别、构图、机位、光线、人物/产品位置；
3. 拍摄动作设计与镜头执行；
4. 剪辑节奏、Jump Cut/动作匹配/遮挡/卡点/速度变化等；
5. 美瞳/眼睛/产品第一次被有效看清的效率；
6. 完播、复播、Loop、评论/分享驱动；
7. 这个做法对美瞳品牌是否容易复刻。

不要因为播放量高就给创意高分；热度数据仅作为平台语境参考。
只根据可观察证据判断。关键帧不足以确认的剪辑技巧必须写“无法确认”或“推测”，不要伪装成确定事实。

只输出合法 JSON，结构：
{
  "scores":{
    "hook":0,
    "composition":0,
    "shooting":0,
    "editing":0,
    "product_reveal":0,
    "retention_loop":0,
    "replicability":0
  },
  "one_line":"一句话说明为什么值得或不值得剪辑师研究",
  "hook_analysis":"首帧+前3秒",
  "shooting_analysis":"拍摄/构图/机位/光线/动作",
  "editing_analysis":"剪辑节奏、切点、转场、速度、字幕/画面节奏",
  "platform_burst_analysis":"为什么这种结构在对应平台容易或不容易获得推荐/互动",
  "product_display_analysis":"美瞳/眼睛展示效率",
  "loop_analysis":"完播、复播、loop设计",
  "replication_advice":"UYAAI可复制的具体做法",
  "risk":"不可复制因素/判断限制",
  "timeline":[
    {"time":"00:00-00:01","visual":"画面","edit":"剪辑/动作目的"}
  ],
  "visual_tags":["眼部特写"],
  "shooting_tags":["自然光"],
  "editing_tags":["Jump Cut"],
  "hook_tags":["大脸近景"]
}"""

    metadata = {
        "platform": item.get("platform"),
        "brand": item.get("brand"),
        "author": item.get("author"),
        "caption": item.get("caption"),
        "duration_seconds": round(duration, 2),
        "estimated_scene_cuts": estimated_cut_count,
        "estimated_average_shot_seconds": avg_shot,
        "views": item.get("views"),
        "likes": item.get("likes"),
        "comments": item.get("comments"),
        "shares": item.get("shares"),
        "saves": item.get("saves"),
        "heat_score": item.get("heat_score"),
    }

    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": rubric + "\n\n视频信息：" + json.dumps(metadata, ensure_ascii=False),
        }
    ]
    for timestamp, frame in frames:
        content.append({"type": "input_text", "text": f"关键帧时间：{timestamp:.2f}s"})
        content.append({"type": "input_image", "image_url": _image_data_url(frame), "detail": "low"})

    body = {
        "model": model,
        "input": [{"role": "user", "content": content}],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        parsed = json.loads(_clean_json_text(_extract_output_text(payload)))
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        print(f"WARNING: creative OpenAI analysis failed for {item.get('url')}: {exc}")
        return None


def _score(parsed: dict[str, Any]) -> tuple[float, dict[str, float]]:
    raw = parsed.get("scores") if isinstance(parsed.get("scores"), dict) else {}
    components: dict[str, float] = {}
    total = 0.0
    for name, weight in CREATIVE_WEIGHTS.items():
        value = clamp(safe_num(raw.get(name)))
        components[name] = round(value, 1)
        total += weight * value
    return round(clamp(total), 1), components


def enrich_creative_analysis(items: list[dict[str, Any]]) -> dict[str, Any]:
    enabled = os.environ.get("CREATIVE_ANALYSIS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    status: dict[str, Any] = {
        "enabled": enabled,
        "api_key_present": bool(api_key),
        "analyzed": 0,
        "attempted": 0,
        "failed": 0,
        "editors_picks": 0,
    }
    if not enabled or not api_key:
        return status

    candidates = select_creative_candidates(items)
    status["attempted"] = len(candidates)
    if not candidates:
        return status

    tiktok_map: dict[str, str] = {}
    try:
        tiktok_map = _tiktok_video_sources(candidates)
    except Exception as exc:
        print(f"WARNING: TikTok creative video fetch failed: {exc}")

    for item in candidates:
        item["creative_analysis_status"] = "pending"
        source = _resolve_video_source(item, tiktok_map)
        if not source:
            item["creative_analysis_status"] = "video_unavailable"
            status["failed"] += 1
            continue

        with tempfile.TemporaryDirectory(prefix="lens-radar-creative-") as tmp:
            tmpdir = Path(tmp)
            video = tmpdir / "video.mp4"
            if not _download_video(source, video):
                item["creative_analysis_status"] = "download_failed"
                status["failed"] += 1
                continue

            duration = _probe_duration(video)
            cuts = _scene_cuts(video)
            times = _frame_times(duration, cuts)
            frames = _extract_frames(video, tmpdir, times)
            parsed = _call_openai(item, frames, duration, cuts)
            if not parsed:
                item["creative_analysis_status"] = "analysis_failed"
                status["failed"] += 1
                continue

            creative_score, score_components = _score(parsed)
            avg_shot = round(duration / (len(cuts) + 1), 2) if duration > 0 else None

            item["creative_score"] = creative_score
            item["creative_score_breakdown"] = score_components
            item["creative_analysis_status"] = "complete"
            item["video_analysis"] = {
                "duration_seconds": round(duration, 2),
                "estimated_cut_count": len(cuts),
                "estimated_average_shot_seconds": avg_shot,
                "sampled_frame_times": [round(t, 2) for t, _ in frames],
                "one_line": parsed.get("one_line") or "",
                "hook_analysis": parsed.get("hook_analysis") or "",
                "shooting_analysis": parsed.get("shooting_analysis") or "",
                "editing_analysis": parsed.get("editing_analysis") or "",
                "platform_burst_analysis": parsed.get("platform_burst_analysis") or "",
                "product_display_analysis": parsed.get("product_display_analysis") or "",
                "loop_analysis": parsed.get("loop_analysis") or "",
                "replication_advice": parsed.get("replication_advice") or "",
                "risk": parsed.get("risk") or "",
                "timeline": parsed.get("timeline") if isinstance(parsed.get("timeline"), list) else [],
            }

            for key in ("visual_tags", "shooting_tags", "editing_tags", "hook_tags"):
                value = parsed.get(key)
                if isinstance(value, list):
                    item[key] = [str(x).strip() for x in value if str(x).strip()][:12]

            status["analyzed"] += 1

    picks = [x for x in items if x.get("creative_score") is not None]
    picks.sort(
        key=lambda x: (
            safe_num(x.get("creative_score")),
            safe_num(x.get("heat_score")),
        ),
        reverse=True,
    )
    for idx, item in enumerate(picks, start=1):
        item["editors_pick_rank"] = idx
    status["editors_picks"] = min(10, len(picks))
    return status
