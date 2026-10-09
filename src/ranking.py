from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .utils import clamp, log_scale, safe_num


def _percentiles(values: list[float]) -> list[float]:
    if not values:
        return []
    if len(values) == 1:
        return [50.0]
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    for rank, idx in enumerate(order):
        result[idx] = 100.0 * rank / (len(values) - 1)
    return result


def _engagement(item: dict[str, Any]) -> float:
    return (
        safe_num(item.get("likes"))
        + 2.0 * safe_num(item.get("comments"))
        + 3.0 * safe_num(item.get("shares"))
        + 2.5 * safe_num(item.get("saves"))
    )


def _denominator(item: dict[str, Any]) -> float:
    views = safe_num(item.get("views"))
    followers = safe_num(item.get("followers"))
    return max(views, followers * 0.15, 1.0)


def _age_hours(item: dict[str, Any], as_of: datetime) -> float:
    raw = item.get("posted_at")
    if not raw:
        return 24.0
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(1.0, (as_of - dt.astimezone(timezone.utc)).total_seconds() / 3600)
    except ValueError:
        return 24.0


def score_items(items: list[dict[str, Any]], as_of: datetime | None = None) -> list[dict[str, Any]]:
    """Cross-platform ranking via within-platform percentile normalization.

    Weights:
      35% reach, 25% engagement rate, 20% share/save intent,
      10% velocity, 10% relevance/creative fit.
    """
    as_of = as_of or datetime.now(timezone.utc)
    by_platform: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_platform[str(item.get("platform", "unknown")).lower()].append(item)

    scored: list[dict[str, Any]] = []
    for platform, group in by_platform.items():
        reach_vals = [log_scale(safe_num(x.get("views")) or safe_num(x.get("impressions"))) for x in group]
        er_vals = [_engagement(x) / _denominator(x) for x in group]
        intent_vals = [safe_num(x.get("shares")) * 1.5 + safe_num(x.get("saves")) for x in group]
        velocity_vals = [_engagement(x) / _age_hours(x, as_of) for x in group]
        relevance_vals = [safe_num(x.get("relevance_score"), 50.0) for x in group]

        reach_pct = _percentiles(reach_vals)
        er_pct = _percentiles(er_vals)
        intent_pct = _percentiles(intent_vals)
        velocity_pct = _percentiles(velocity_vals)
        relevance_pct = _percentiles(relevance_vals)

        for i, item in enumerate(group):
            score = (
                0.35 * reach_pct[i]
                + 0.25 * er_pct[i]
                + 0.20 * intent_pct[i]
                + 0.10 * velocity_pct[i]
                + 0.10 * relevance_pct[i]
            )
            enriched = dict(item)
            enriched["platform"] = platform
            enriched["engagement_weighted"] = round(_engagement(item), 2)
            enriched["engagement_rate"] = round(100.0 * er_vals[i], 3)
            enriched["score"] = round(clamp(score), 1)
            enriched["score_breakdown"] = {
                "reach": round(reach_pct[i], 1),
                "engagement": round(er_pct[i], 1),
                "share_save": round(intent_pct[i], 1),
                "velocity": round(velocity_pct[i], 1),
                "relevance": round(relevance_pct[i], 1),
            }
            scored.append(enriched)

    scored.sort(key=lambda x: (safe_num(x.get("score")), safe_num(x.get("views"))), reverse=True)
    platform_counters: dict[str, int] = defaultdict(int)
    for idx, item in enumerate(scored, start=1):
        item["rank"] = idx
        platform = str(item.get("platform", "unknown")).lower()
        platform_counters[platform] += 1
        item["platform_rank"] = platform_counters[platform]
    return scored
