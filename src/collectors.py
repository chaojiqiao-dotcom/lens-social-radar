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
    "platform": ["platform"],
    "url": ["url", "share_url", "shareUrl", "webVideoUrl", "postUrl", "postPage", "pinUrl", "pin_url", "canonicalUrl"],
    "mobile_url": ["mobile_url", "mobileUrl", "appUrl", "deepLink", "deeplink"],
    "embed_url": ["embed_url", "embedUrl", "embed_link", "embedLink"],
    "video_id": ["video_id", "videoId", "id", "item_id", "itemId", "pinId", "node_id"],
    "author": [
        "author", "username", "ownerUsername", "profileName", "channelName",
        "authorMeta.name", "pinner.username", "pinnerUsername", "pinner_username",
        "creator.username", "creator.name", "creator_username",
        "channel.username", "channel.name",
    ],
    "caption": ["caption", "text", "description", "title", "video_description"],
    "posted_at": [
        "posted_at", "createTimeISO", "createTime", "timestamp", "date", "created_at", "create_time",
        "createdAt", "publishedAt", "published_at",
    ],
    "views": [
        "views", "playCount", "videoPlayCount", "videoViewCount", "view_count",
        "viewCount", "impressions",
    ],
    "likes": ["likes", "likesCount", "diggCount", "like_count", "likeCount", "reactions", "reactions_count"],
    "comments": ["comments", "commentsCount", "commentCount", "comment_count", "comments_count"],
    "shares": ["shares", "sharesCount", "shareCount", "share_count", "reposts", "repinCount", "repins_count"],
    "saves": [
        "saves", "saveCount", "savesCount", "collectCount", "bookmarks", "bookmarkCount",
        "favorites_count", "favourites_count", "save_count", "saves_count",
    ],
    "followers": [
        "followers", "authorMeta.fans", "follower_count", "followersCount",
        "pinner.followers", "creator.followers", "channel.followers",
    ],
    "thumbnail": [
        "thumbnail", "thumbnail_url", "thumbnailUrl", "cover_image_url", "coverImageUrl",
        "displayUrl", "cover", "image", "image_url", "imageUrl", "firstFrame",
        "first_frame", "videoMeta.coverUrl", "videoMeta.originalCoverUrl",
        "video.cover", "video.thumbnail",
    ],
    "local_video": ["local_video", "localVideo", "downloadedVideo", "video_path", "videoUrl"],
    "access_mode": ["access_mode", "accessMode", "playback_mode"],
    "content_type": ["content_type", "type", "media_type", "mediaType", "format"],
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

    # Preserve whether a metric was actually supplied by the data source.
    # A missing value is NOT the same thing as a confirmed zero. The numeric
    # field remains 0 for backward-compatible rendering, while ranking uses
    # metric_availability to decide whether the number is trustworthy.
    metric_availability: dict[str, bool] = {}
    for metric in ("views", "likes", "comments", "shares", "saves", "followers"):
        raw_metric = item.get(metric)
        metric_availability[metric] = raw_metric not in (None, "")
        item[metric] = int(safe_num(raw_metric))
    item["metric_availability"] = metric_availability

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
        elif isinstance(value, list):
            if key == "hashtags":
                value = [
                    str(x.get("name") or x.get("title") or "").strip()
                    if isinstance(x, dict) else str(x).strip()
                    for x in value
                ]
                value = [x for x in value if x]
        else:
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
    """Run one Apify Actor and return its default dataset.

    The token is sent in the Authorization header so it does not appear in URLs/logs.
    """
    actor_encoded = urllib.parse.quote(actor, safe="")
    url = f"https://api.apify.com/v2/acts/{actor_encoded}/runs?waitForFinish=180"
    req = urllib.request.Request(
        url,
        data=json.dumps(actor_input).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "LensSocialRadar/1.3",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=210) as resp:
        run = json.loads(resp.read().decode("utf-8"))
    dataset_id = (((run or {}).get("data") or {}).get("defaultDatasetId"))
    if not dataset_id:
        return []
    dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?clean=true"
    dataset_req = urllib.request.Request(
        dataset_url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "LensSocialRadar/1.3",
        },
    )
    with urllib.request.urlopen(dataset_req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload if isinstance(payload, list) else []


def _default_apify_terms(keywords: dict[str, Any]) -> list[str]:
    """Small multilingual discovery set for the first live deployment.

    Keeping this intentionally small controls cost. It can later be expanded via
    APIFY_SEARCH_TERMS without changing code.
    """
    env_terms = os.environ.get("APIFY_SEARCH_TERMS", "").strip()
    if env_terms:
        return [x.strip() for x in env_terms.split("|") if x.strip()]

    preferred = [
        "colored contacts",
        "contact lenses",
        "circle lens",
        "softlens",
        "美瞳",
        "컬러렌즈",
    ]
    configured = [str(x).strip() for x in keywords.get("core", []) if str(x).strip()]
    out: list[str] = []
    for term in preferred + configured:
        if term not in out:
            out.append(term)
    return out[:6]


def _watchlist_handles(brands: dict[str, Any], platform: str) -> list[str]:
    handles: list[str] = []
    for entries in (brands.get("regions") or {}).values():
        for entry in entries or []:
            handle = str(((entry.get("handles") or {}).get(platform)) or "").strip().lstrip("@")
            if handle and handle not in handles:
                handles.append(handle)
    return handles


def _actor_input_for_platform(
    platform: str,
    start: date,
    end: date,
    terms: list[str],
    brands: dict[str, Any],
) -> dict[str, Any]:
    raw_per_query = os.environ.get("APIFY_RESULTS_PER_QUERY", "").strip() or "12"
    per_query = max(1, int(raw_per_query))
    handles = _watchlist_handles(brands, platform)

    hashtag_terms = []
    for x in terms:
        tag = x.replace("#", "").replace(" ", "")
        if tag and tag not in hashtag_terms:
            hashtag_terms.append(tag)

    if platform == "instagram":
        # Use newest-first profile + hashtag inputs and let our exact date-window
        # filter enforce Tuesday-Sunday. This is more reliable than only asking
        # Instagram for generic "top" hashtag results.
        direct_inputs = handles + [f"#{tag}" for tag in hashtag_terms]
        return {
            "directUrls": direct_inputs,
            "resultsType": "posts",
            "resultsLimit": max(12, per_query),
            "onlyPostsNewerThan": start.isoformat(),
            "skipPinnedPosts": True,
        }

    if platform == "tiktok":
        # Combine brand profiles, hashtags, and keyword discovery in one run.
        # PAST_MONTH is intentionally wider than the final weekly window so the
        # exact Tuesday-Sunday filter still has enough candidates.
        return {
            "profiles": handles,
            "profileScrapeSections": ["videos"],
            "profileSorting": "latest",
            "excludePinnedPosts": True,
            "oldestPostDateUnified": start.isoformat(),
            "newestPostDate": end.isoformat(),
            "hashtags": hashtag_terms,
            "searchQueries": terms,
            "searchSection": "/video",
            "videoSearchSorting": "LATEST",
            "videoSearchDateFilter": "PAST_MONTH",
            "resultsPerPage": max(12, per_query),
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "scrapeRelatedVideos": False,
        }

    if platform == "pinterest":
        # Full pin records with clean created_at + engagement fields.
        # A larger candidate pool is needed because Pinterest search is relevance
        # oriented; the weekly filter is applied after collection.
        pinterest_terms = terms[:4]
        return {
            "searchQueries": pinterest_terms,
            "resultsPerQuery": max(30, per_query * 3),
            "country": "US",
        }

    return {}


def collect_apify(
    start: date,
    end: date,
    brands: dict[str, Any],
    keywords: dict[str, Any],
) -> list[dict[str, Any]]:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "APIFY_TOKEN is required for COLLECTION_MODE=apify. "
            "Add it as a GitHub Actions secret; never commit the token to the repository."
        )

    defaults = {
        "instagram": "calm_builder/instagram-scraper",
        "tiktok": "clockworks/tiktok-scraper",
        "pinterest": "scrapeberry/pinterest-search-scraper",
    }
    terms = _default_apify_terms(keywords)
    raw_per_query = os.environ.get("APIFY_RESULTS_PER_QUERY", "").strip() or "12"
    per_query = max(1, int(raw_per_query))
    all_items: list[dict[str, Any]] = []
    failures: list[str] = []

    for platform in ("instagram", "tiktok", "pinterest"):
        actor = os.environ.get(
            f"APIFY_{platform.upper()}_ACTOR",
            defaults[platform],
        ).strip()
        raw_input = os.environ.get(f"APIFY_{platform.upper()}_INPUT", "").strip()

        if raw_input:
            try:
                actor_input = json.loads(raw_input)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"APIFY_{platform.upper()}_INPUT is not valid JSON"
                ) from exc
        else:
            actor_input = _actor_input_for_platform(platform, start, end, terms, brands)

        try:
            payload = _apify_run(actor, token, actor_input)

            if platform == "instagram":
                profile_handles = _watchlist_handles(brands, "instagram")
                if profile_handles:
                    reels_input = {
                        "directUrls": profile_handles,
                        "resultsType": "reels",
                        "resultsLimit": max(8, per_query),
                        "onlyPostsNewerThan": start.isoformat(),
                        "skipPinnedPosts": True,
                    }
                    reels_payload = _apify_run(actor, token, reels_input)
                    payload.extend(reels_payload)

            # TikTok search can occasionally return an empty dataset because of
            # platform-side bot detection/session variance. Use a pay-per-result
            # keyword-search Actor as a low-cost fallback instead of dropping the
            # platform from the weekly report.
            if platform == "tiktok" and not payload:
                fallback_actor = os.environ.get(
                    "APIFY_TIKTOK_FALLBACK_ACTOR",
                    "xmolodtsov/tiktok-search-scraper",
                ).strip()
                fallback_input = {
                    "keywords": terms,
                    "maxItems": min(80, max(40, per_query * len(terms))),
                    "maxItemsPerKeyword": max(8, per_query),
                    "sortType": "DATE_POSTED",
                    "includeSearchKeywords": True,
                }
                print(
                    f"Apify tiktok primary returned 0; "
                    f"trying fallback actor={fallback_actor}"
                )
                payload = _apify_run(
                    fallback_actor,
                    token,
                    fallback_input,
                )
                actor = fallback_actor

            normalized = [
                normalize(x, platform)
                for x in payload
                if isinstance(x, dict)
            ]
            all_items.extend(normalized)
            print(
                f"Apify {platform}: actor={actor}, "
                f"raw={len(payload)}, normalized={len(normalized)}"
            )
        except Exception as exc:
            failures.append(f"{platform}: {exc}")
            print(f"WARNING: Apify {platform} collection failed: {exc}")

    if not all_items:
        detail = " | ".join(failures) if failures else "no items returned"
        raise RuntimeError(f"Apify collection returned zero posts. {detail}")

    if failures:
        print("WARNING: partial Apify collection: " + " | ".join(failures))

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in all_items:
        key = str(item.get("url") or item.get("video_id") or "").strip()
        if not key:
            key = json.dumps(
                [item.get("platform"), item.get("author"), item.get("posted_at"), item.get("caption")],
                ensure_ascii=False,
            )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    print(f"Apify combined: raw-normalized={len(all_items)}, deduped={len(deduped)}")
    return deduped

