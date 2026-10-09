from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from .utils import load_json, safe_num


COMMON_KEYS = {
    "platform": ["platform", "source"],
    "url": ["url", "share_url", "shareUrl", "webVideoUrl", "postUrl", "pinUrl", "canonicalUrl"],
    "mobile_url": ["mobile_url", "mobileUrl", "appUrl", "deepLink", "deeplink"],
    "embed_url": ["embed_url", "embedUrl", "embed_link", "embedLink"],
    "video_id": ["video_id", "videoId", "id", "item_id", "itemId"],
    "author": ["author", "username", "ownerUsername", "profileName", "channelName"],
    "caption": ["caption", "text", "description", "title", "video_description"],
    "posted_at": ["posted_at", "createTimeISO", "timestamp", "date", "created_at", "create_time"],
    "views": ["views", "playCount", "videoPlayCount", "view_count", "impressions"],
    "likes": ["likes", "diggCount", "like_count", "reactions"],
    "comments": ["comments", "commentCount", "comment_count"],
    "shares": ["shares", "shareCount", "share_count", "reposts"],
    "saves": ["saves", "collectCount", "favorites_count", "favourites_count", "save_count"],
    "followers": ["followers", "authorMeta.fans", "follower_count"],
    "thumbnail": ["thumbnail", "thumbnail_url", "thumbnailUrl", "cover_image_url", "coverImageUrl", "displayUrl", "cover", "image", "image_url", "firstFrame", "first_frame"],
    "local_video": ["local_video", "localVideo", "downloadedVideo", "video_path"],
    "access_mode": ["access_mode", "accessMode", "playback_mode"],
    "content_type": ["content_type", "type", "media_type", "format"],
    "hashtags": ["hashtags", "hashtag_names", "tags"],
    "search_tags": ["search_tags", "keywords", "topics"],
    "visual_tags": ["visual_tags", "visual_keywords"],
    "shooting_tags": ["shooting_tags", "camera_tags", "shooting_techniques"],
    "editing_tags": ["editing_tags", "edit_tags", "editing_techniques"],
    "hook_tags": ["hook_tags", "hook_keywords"],
}


def _deep_get(obj: dict[str, Any], dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _pick(raw: dict[str, Any], candidates: list[str]) -> Any:
    for key in candidates:
        value = _deep_get(raw, key)
        if value not in (None, "", []):
            return value
    return None


def _normalize_posted_at(value: Any) -> Any:
    if isinstance(value, (int, float)) and value > 1_000_000_000:
        from datetime import datetime, timezone
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return value
    return value


def normalize(raw: dict[str, Any], platform_hint: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {}
    for canonical, candidates in COMMON_KEYS.items():
        item[canonical] = _pick(raw, candidates)
    item["platform"] = str(item.get("platform") or platform_hint or "unknown").lower()
    item["posted_at"] = _normalize_posted_at(item.get("posted_at"))
    for metric in ("views", "likes", "comments", "shares", "saves", "followers"):
        item[metric] = int(safe_num(item.get(metric)))
    item["brand"] = raw.get("brand") or raw.get("brand_name") or item.get("author") or "Unknown"
    item["region"] = raw.get("region") or raw.get("market") or "Unknown"
    item["relevance_score"] = safe_num(raw.get("relevance_score"), 50.0)
    item["media_notes"] = raw.get("media_notes") or raw.get("visual_summary") or ""
    # Preserve searchable creative metadata when a collector or AI enrichment step provides it.
    for key in ("hashtags", "search_tags", "visual_tags", "shooting_tags", "editing_tags", "hook_tags"):
        value = item.get(key)
        if value in (None, ""):
            value = raw.get(key)
        if isinstance(value, str):
            value = [x.strip() for x in value.replace("#", "").replace("，", ",").split(",") if x.strip()]
        elif not isinstance(value, list):
            value = []
        item[key] = value

    # Research-style TikTok feeds may provide only username + video id.
    if item["platform"] == "tiktok":
        author = str(item.get("author") or "").lstrip("@")
        video_id = str(item.get("video_id") or "")
        if not item.get("url") and author and video_id:
            item["url"] = f"https://www.tiktok.com/@{author}/video/{video_id}"
        if item.get("url") and not item.get("mobile_url"):
            item["mobile_url"] = item["url"]
    return item


def collect_demo(start: date, end: date) -> list[dict[str, Any]]:
    """Synthetic 30-post dataset: 10 per platform for UI demonstration only."""
    d = lambda day, hour=12: f"{day}T{hour:02d}:00:00+00:00"
    specs = {
        "instagram": {
            "brands": ["Hapa Kristin","OLENS","Solotica","LensMe","moody","TTDEYE","MOLAK","TOPARDS","ColorCL","Chu's me"],
            "regions": ["Southeast Asia","Southeast Asia","Europe & North America","Middle East","Southeast Asia","Europe & North America","Southeast Asia","Southeast Asia","Europe & North America","Southeast Asia"],
            "captions": [
                "GRWM eye makeup + colored lens transformation","Soft gray lens in daylight vs indoor light","Natural hazel carousel: three makeup pairings","Arabic beauty creator brown lens reveal","Pink-toned eye close-up with glossy makeup","Fast lens shade switch with face close-up","Soft-focus daily lens styling","Clean-girl makeup with subtle enlarging lens","Flash test: warm brown vs cool gray","Campus makeup look with natural brown lens"
            ],
            "types": ["reel","reel","carousel","reel","reel","reel","carousel","reel","carousel","reel"],
            "base_views": 980000,
        },
        "tiktok": {
            "brands": ["OLENS","Hapa Kristin","ColorCL","TTDEYE","moody","Solotica","LensMe","MOLAK","TOPARDS","Chu's me"],
            "regions": ["Southeast Asia","Southeast Asia","Europe & North America","Europe & North America","Southeast Asia","Europe & North America","Middle East","Southeast Asia","Southeast Asia","Southeast Asia"],
            "captions": [
                "One-eye comparison with macro close-up and blink reveal","Which brown lens looks more natural? A/B eye test","POV: the lens shade that changes your whole makeup","Three shades in five seconds","Before/after iris transformation with pink makeup","Outdoor sunlight lens test","Creator reaction to subtle enlarging lens","School-to-night makeup switch","Macro iris reveal with head turn","No-makeup lens check in window light"
            ],
            "types": ["video"]*10,
            "base_views": 2050000,
        },
        "pinterest": {
            "brands": ["LensMe","moody","Solotica","OLENS","Hapa Kristin","TTDEYE","ColorCL","MOLAK","TOPARDS","Chu's me"],
            "regions": ["Middle East","Southeast Asia","Europe & North America","Southeast Asia","Southeast Asia","Europe & North America","Europe & North America","Southeast Asia","Southeast Asia","Southeast Asia"],
            "captions": [
                "Hazel eye moodboard: makeup pairings and shade guide","Pink-water contact lens campaign visual","Blue-gray contact lens macro photography","Brown lens shade chart for everyday makeup","Soft pink eye aesthetic board","Cool gray lens photography reference","Warm brown iris close-up moodboard","Natural lens + blush palette guide","Minimal product flat-lay with eye crop","Daily lens styling save-board"
            ],
            "types": ["pin"]*10,
            "base_views": 590000,
        },
    }
    records: list[dict[str, Any]] = []
    for platform, cfg in specs.items():
        for i in range(10):
            base = cfg["base_views"]
            views = int(base * (1 - i * 0.072) + (i % 3) * 17000)
            likes = int(views * (0.062 + (9-i) * 0.0023))
            comments = int(520 + (9-i) * 170 + (i % 2) * 80)
            shares = int(views * (0.0048 + (9-i) * 0.00055))
            saves = int(views * (0.008 + (9-i) * 0.00125))
            if platform == "pinterest":
                likes = int(views * (0.025 + (9-i) * 0.0012))
                comments = int(70 + (9-i) * 28)
                shares = int(views * (0.0025 + (9-i) * 0.00025))
                saves = int(views * (0.045 + (9-i) * 0.0028))
            creative_tags = {
                "instagram": [
                    ["GRWM","妆容变换","眼部特写","大脸近景","Jump Cut","卡点"],
                    ["灰色美瞳","自然光","室内外对比","眼部特写","daylight"],
                    ["榛色美瞳","carousel","妆容搭配","静态排版","收藏型内容"],
                    ["棕色美瞳","中东妆容","大脸近景","reveal","创作者口播"],
                    ["粉色妆容","眼部超近景","高光妆感","柔光","beauty close-up"],
                    ["快速换色","大脸近景","Jump Cut","卡点","快速剪辑"],
                    ["日常美瞳","柔焦","生活方式","carousel","自然妆"],
                    ["clean girl","扩瞳","近景自拍","自然光","轻妆"],
                    ["闪光灯测试","棕色美瞳","灰色美瞳","A/B对比","flash"],
                    ["校园妆容","自然棕","学生感","自然光","通勤"],
                ],
                "tiktok": [
                    ["单眼对比","微距","眼部特写","眨眼","blink reveal","强Hook","大脸近景"],
                    ["A/B对比","棕色美瞳","提问Hook","眼部特写","评论互动"],
                    ["POV","妆容变换","转场","前后对比","大脸近景"],
                    ["五秒三色","快速剪辑","Jump Cut","卡点","换色"],
                    ["before after","虹膜变化","粉色妆容","match cut","变装"],
                    ["户外测试","自然光","阳光","真实显色","手持"],
                    ["reaction","扩瞳","口播","近景自拍","评论驱动"],
                    ["school to night","妆容切换","转场","卡点","场景变化"],
                    ["微距虹膜","转头","眼部超近景","动作匹配","macro"],
                    ["素颜测试","窗边自然光","真实感","低广告感","近景自拍"],
                ],
                "pinterest": [
                    ["榛色美瞳","moodboard","妆容搭配","色卡","收藏型内容"],
                    ["粉色水面","产品概念","广告视觉","静物摄影","粉色"],
                    ["蓝灰美瞳","微距摄影","虹膜特写","产品细节","macro"],
                    ["棕色美瞳","shade chart","日常妆容","信息图","收藏"],
                    ["粉色眼妆","aesthetic","眼部特写","情绪板","灵感"],
                    ["冷灰美瞳","摄影参考","眼部近景","冷调","reference"],
                    ["暖棕","虹膜特写","moodboard","暖调","微距"],
                    ["自然美瞳","腮红搭配","palette guide","信息排版","收藏"],
                    ["产品平铺","flat lay","眼睛裁切","极简构图","静物"],
                    ["日常搭配","save board","生活方式","参考图","收藏型内容"],
                ],
            }[platform][i]
            records.append({
                "platform": platform,
                "brand": cfg["brands"][i],
                "author": f"@demo_{platform}_{i+1}",
                "region": cfg["regions"][i],
                "content_type": cfg["types"][i],
                "caption": cfg["captions"][i],
                "posted_at": d(start.isoformat() if i % 2 == 0 else end.isoformat(), 8 + i),
                "views": views,
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "saves": saves,
                "followers": int(250000 + (9-i)*70000),
                "relevance_score": 96 - i,
                "media_notes": "Eye-first visual, clear lens result, compact information structure and save/share intent.",
                "search_tags": creative_tags,
                "visual_tags": [t for t in creative_tags if t in {"眼部特写","眼部超近景","大脸近景","微距","虹膜特写","产品平铺","自然光","柔光","闪光灯测试"}],
                "shooting_tags": [t for t in creative_tags if t in {"自然光","手持","窗边自然光","微距","大脸近景","眼部特写","眼部超近景","静物摄影"}],
                "editing_tags": [t for t in creative_tags if t in {"Jump Cut","卡点","快速剪辑","转场","match cut","动作匹配"}],
                "hook_tags": [t for t in creative_tags if "Hook" in t or t in {"单眼对比","A/B对比","before after","POV","reaction"}],
                "thumbnail": f"assets/demo/{platform}-{i+1}.svg",
                "url": None,
                "access_mode": "demo",
            })
    return records


def collect_incoming(folder: str | Path = "data/incoming") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(Path(folder).glob("*.json")):
        data = load_json(path, [])
        if isinstance(data, dict):
            data = data.get("items") or data.get("data") or [data]
        if isinstance(data, list):
            platform_hint = path.stem.split("_")[0].lower()
            items.extend(normalize(x, platform_hint) for x in data if isinstance(x, dict))
    return items


def _apify_run(actor: str, token: str, actor_input: dict[str, Any]) -> list[dict[str, Any]]:
    actor_encoded = urllib.parse.quote(actor, safe="")
    url = f"https://api.apify.com/v2/acts/{actor_encoded}/runs?token={urllib.parse.quote(token)}&waitForFinish=120"
    req = urllib.request.Request(
        url,
        data=json.dumps(actor_input).encode("utf-8"),
        headers={"Content-Type":"application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=150) as resp:
        run = json.loads(resp.read().decode("utf-8"))
    dataset_id = (((run or {}).get("data") or {}).get("defaultDatasetId"))
    if not dataset_id:
        return []
    dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={urllib.parse.quote(token)}&clean=true"
    with urllib.request.urlopen(dataset_url, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload if isinstance(payload, list) else []


def collect_apify(start: date, end: date, brands: dict[str, Any], keywords: dict[str, Any]) -> list[dict[str, Any]]:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise RuntimeError("APIFY_TOKEN is required for COLLECTION_MODE=apify")
    all_items: list[dict[str, Any]] = []
    query_terms = keywords.get("core", [])[:20]
    for platform in ("instagram", "tiktok", "pinterest"):
        actor = os.environ.get(f"APIFY_{platform.upper()}_ACTOR", "").strip()
        if not actor:
            continue
        raw_input = os.environ.get(f"APIFY_{platform.upper()}_INPUT", "{}")
        try:
            actor_input = json.loads(raw_input)
        except json.JSONDecodeError:
            actor_input = {}
        actor_input.setdefault("fromDate", start.isoformat())
        actor_input.setdefault("toDate", end.isoformat())
        actor_input.setdefault("searchTerms", query_terms)
        actor_input.setdefault("maxItems", 500)
        payload = _apify_run(actor, token, actor_input)
        all_items.extend(normalize(x, platform) for x in payload if isinstance(x, dict))
    return all_items
