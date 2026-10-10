from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from .utils import clamp, log_scale, safe_num


METRICS = ("views", "likes", "comments", "shares", "saves", "followers")


def _available(item: dict[str, Any], metric: str) -> bool:
    availability = item.get("metric_availability")
    if isinstance(availability, dict) and metric in availability:
        return bool(availability.get(metric))
    return item.get(metric) not in (None, "")


def _metric(item: dict[str, Any], metric: str) -> float | None:
    if not _available(item, metric):
        return None
    return safe_num(item.get(metric))


def _percentiles(values: list[float | None]) -> list[float | None]:
    """Percentile ranks with proper tie handling and missing-value support.

    Equal values receive the same average percentile. Missing values receive None
    and therefore cannot accidentally become "zero" or win a tie by list order.
    """
    result: list[float | None] = [None] * len(values)
    valid = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if not valid:
        return result
    if len(valid) == 1:
        result[valid[0][0]] = 50.0
        return result

    valid.sort(key=lambda pair: pair[1])
    n = len(valid)
    pos = 0
    while pos < n:
        end = pos
        value = valid[pos][1]
        while end + 1 < n and valid[end + 1][1] == value:
            end += 1
        avg_rank = (pos + end) / 2.0
        pct = 100.0 * avg_rank / (n - 1)
        for j in range(pos, end + 1):
            result[valid[j][0]] = pct
        pos = end + 1
    return result


def _age_hours(item: dict[str, Any], as_of: datetime) -> float:
    raw = item.get("posted_at")
    if not raw:
        return 24.0
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(str(raw))
        except (TypeError, ValueError, OverflowError):
            return 24.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(1.0, (as_of - dt.astimezone(timezone.utc)).total_seconds() / 3600)


def _interaction_volume(item: dict[str, Any]) -> float:
    return sum(
        _metric(item, metric) or 0.0
        for metric in ("likes", "comments", "shares", "saves")
    )


def _weighted_engagement(item: dict[str, Any]) -> float:
    return (
        (_metric(item, "likes") or 0.0)
        + 2.0 * (_metric(item, "comments") or 0.0)
        + 3.0 * (_metric(item, "shares") or 0.0)
        + 2.5 * (_metric(item, "saves") or 0.0)
    )


def _intent_volume(item: dict[str, Any]) -> float:
    return 1.5 * (_metric(item, "shares") or 0.0) + (_metric(item, "saves") or 0.0)


def _is_video(item: dict[str, Any]) -> bool:
    kind = str(item.get("content_type") or "").lower()
    return kind in {"video", "reel", "reels", "clip"} or item.get("platform") == "tiktok"


def _confidence_weights(platform: str, item: dict[str, Any]) -> dict[str, float]:
    if platform == "pinterest":
        return {
            "views": 0.15,
            "likes": 0.10,
            "comments": 0.10,
            "shares": 0.25,
            "saves": 0.30,
            "followers": 0.10,
        }
    if platform == "instagram" and not _is_video(item):
        return {
            "views": 0.05,
            "likes": 0.30,
            "comments": 0.15,
            "shares": 0.20,
            "saves": 0.20,
            "followers": 0.10,
        }
    return {
        "views": 0.30,
        "likes": 0.20,
        "comments": 0.10,
        "shares": 0.15,
        "saves": 0.15,
        "followers": 0.10,
    }


def _data_confidence(platform: str, item: dict[str, Any]) -> float:
    weights = _confidence_weights(platform, item)
    score = sum(weight for metric, weight in weights.items() if _available(item, metric))
    return round(100.0 * score / sum(weights.values()), 1)


def _eligibility(platform: str, item: dict[str, Any], confidence: float) -> tuple[bool, str]:
    interactions = _interaction_volume(item)
    views = _metric(item, "views")
    likes = _metric(item, "likes")
    comments = _metric(item, "comments")
    shares = _metric(item, "shares")
    saves = _metric(item, "saves")

    if not item.get("posted_at"):
        return False, "缺少发布时间"

    if platform == "tiktok":
        if views is None or views <= 0:
            return False, "缺少有效播放量"
        if likes is None or comments is None:
            return False, "缺少点赞或评论数据"
        if views < 500 and interactions < 20:
            return False, "样本量过小"
        if confidence < 50:
            return False, "数据完整度不足"
        return True, ""

    if platform == "instagram":
        if _is_video(item):
            if views is None or views <= 0:
                return False, "视频缺少有效播放量"
            if likes is None:
                return False, "缺少点赞数据"
            if views < 500 and interactions < 20:
                return False, "样本量过小"
            if confidence < 45:
                return False, "数据完整度不足"
            return True, ""

        # Static/carousel posts often have no public reach. They may still rank
        # from interactions, but only when the interaction data is substantial.
        if likes is None or comments is None:
            return False, "图文缺少点赞或评论数据"
        if interactions < 10:
            return False, "互动样本量过小"
        if confidence < 35:
            return False, "数据完整度不足"
        return True, ""

    if platform == "pinterest":
        intent_available = _available(item, "saves") or _available(item, "shares")
        other_available = _available(item, "likes") or _available(item, "comments") or _available(item, "views")
        if not intent_available:
            return False, "缺少收藏/转存信号"
        if not other_available:
            return False, "缺少辅助互动数据"
        if interactions < 5:
            return False, "互动样本量过小"
        if confidence < 35:
            return False, "数据完整度不足"
        return True, ""

    if interactions < 5 or confidence < 50:
        return False, "数据不足"
    return True, ""


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return num / den


def _component_values(platform: str, item: dict[str, Any], as_of: datetime) -> dict[str, float | None]:
    views = _metric(item, "views")
    comments = _metric(item, "comments")
    interactions = _interaction_volume(item)
    weighted = _weighted_engagement(item)
    intent = _intent_volume(item)
    age = _age_hours(item, as_of)

    if platform == "tiktok" or (platform == "instagram" and _is_video(item)):
        return {
            "reach": log_scale(views) if views is not None else None,
            "engagement": _ratio(weighted, views),
            "share_save": (
                _ratio(intent, views)
                if (_available(item, "shares") or _available(item, "saves"))
                else None
            ),
            "velocity": weighted / age,
        }

    if platform == "instagram":
        return {
            "reach": None,
            "engagement": log_scale(weighted),
            "share_save": log_scale(intent) if (_available(item, "shares") or _available(item, "saves")) else None,
            "comments": log_scale(comments) if comments is not None else None,
            "velocity": weighted / age,
        }

    if platform == "pinterest":
        if views is not None and views > 0:
            return {
                "reach": log_scale(views),
                "engagement": _ratio(weighted, views),
                "share_save": _ratio(intent, views),
                "comments": _ratio(comments, views),
                "velocity": weighted / age,
            }
        return {
            "reach": None,
            "engagement": log_scale(weighted),
            "share_save": log_scale(intent) if (_available(item, "shares") or _available(item, "saves")) else None,
            "comments": log_scale(comments) if comments is not None else None,
            "velocity": weighted / age,
        }

    return {
        "reach": log_scale(views) if views is not None else None,
        "engagement": log_scale(weighted),
        "share_save": log_scale(intent),
        "velocity": weighted / age,
    }


def _weights(platform: str, item: dict[str, Any], has_reach: bool) -> dict[str, float]:
    if platform == "tiktok":
        return {"reach": 0.40, "engagement": 0.25, "share_save": 0.20, "velocity": 0.15}
    if platform == "instagram" and _is_video(item):
        return {"reach": 0.35, "engagement": 0.25, "share_save": 0.25, "velocity": 0.15}
    if platform == "instagram":
        return {"engagement": 0.45, "share_save": 0.25, "comments": 0.10, "velocity": 0.20}
    if platform == "pinterest" and has_reach:
        return {"reach": 0.20, "engagement": 0.25, "share_save": 0.35, "comments": 0.05, "velocity": 0.15}
    if platform == "pinterest":
        return {"engagement": 0.30, "share_save": 0.45, "comments": 0.10, "velocity": 0.15}
    return {"reach": 0.35, "engagement": 0.30, "share_save": 0.20, "velocity": 0.15}


def score_items(items: list[dict[str, Any]], as_of: datetime | None = None) -> list[dict[str, Any]]:
    """Platform-specific heat ranking with data-quality gates.

    Heat ranking is intentionally NOT an AI/creative-quality ranking.
    Missing metrics stay missing, equal values tie, and incomplete posts remain
    searchable but are excluded from platform TOP 10 / overall TOP 3.
    """
    as_of = as_of or datetime.now(timezone.utc)
    by_platform: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        platform = str(item.get("platform", "unknown")).lower()
        by_platform[platform].append(item)

    scored: list[dict[str, Any]] = []

    for platform, group in by_platform.items():
        prepared: list[dict[str, Any]] = []
        for item in group:
            confidence = _data_confidence(platform, item)
            eligible, reason = _eligibility(platform, item, confidence)
            components = _component_values(platform, item, as_of)
            prepared.append({
                "item": item,
                "confidence": confidence,
                "eligible": eligible,
                "reason": reason,
                "components": components,
            })

        eligible_rows = [row for row in prepared if row["eligible"]]
        component_names = {"reach", "engagement", "share_save", "comments", "velocity"}
        pct_maps: dict[str, dict[int, float | None]] = {}

        for name in component_names:
            values = [row["components"].get(name) for row in eligible_rows]
            pcts = _percentiles(values)
            pct_maps[name] = {id(row): pcts[i] for i, row in enumerate(eligible_rows)}

        for row in prepared:
            item = row["item"]
            enriched = dict(item)
            enriched["platform"] = platform
            enriched["data_confidence"] = row["confidence"]
            enriched["ranking_eligible"] = bool(row["eligible"])
            enriched["ranking_reason"] = row["reason"]
            enriched["engagement_weighted"] = round(_weighted_engagement(item), 2)

            views = _metric(item, "views")
            enriched["engagement_rate"] = (
                round(100.0 * _weighted_engagement(item) / views, 3)
                if views is not None and views > 0
                else None
            )

            if row["eligible"]:
                weights = _weights(platform, item, row["components"].get("reach") is not None)
                weighted_sum = 0.0
                used_weight = 0.0
                breakdown: dict[str, float | None] = {}
                for name, weight in weights.items():
                    pct = pct_maps.get(name, {}).get(id(row))
                    breakdown[name] = None if pct is None else round(pct, 1)
                    if pct is not None:
                        weighted_sum += weight * pct
                        used_weight += weight
                heat = weighted_sum / used_weight if used_weight else 0.0
                enriched["heat_score"] = round(clamp(heat), 1)
                enriched["score"] = enriched["heat_score"]  # compatibility with v1.2 UI/search
                enriched["score_breakdown"] = breakdown
            else:
                enriched["heat_score"] = None
                enriched["score"] = None
                enriched["score_breakdown"] = {}

            enriched["creative_score"] = None
            scored.append(enriched)

    eligible = [x for x in scored if x.get("ranking_eligible")]
    ineligible = [x for x in scored if not x.get("ranking_eligible")]

    # Global order is still based on normalized platform heat. Creative score is
    # deliberately excluded until visual/video analysis is implemented.
    eligible.sort(
        key=lambda x: (
            safe_num(x.get("heat_score")),
            safe_num(x.get("data_confidence")),
            safe_num(x.get("views")),
        ),
        reverse=True,
    )

    platform_counters: dict[str, int] = defaultdict(int)
    for idx, item in enumerate(eligible, start=1):
        item["rank"] = idx
        platform = str(item.get("platform", "unknown")).lower()
        platform_counters[platform] += 1
        item["platform_rank"] = platform_counters[platform]

    for item in ineligible:
        item["rank"] = None
        item["platform_rank"] = None

    ineligible.sort(
        key=lambda x: (
            safe_num(x.get("data_confidence")),
            _interaction_volume(x),
        ),
        reverse=True,
    )
    return eligible + ineligible
