from __future__ import annotations

import json
import os
import urllib.request
from collections import Counter
from typing import Any


def fallback_analysis(items: list[dict[str, Any]]) -> dict[str, Any]:
    top3 = []
    for item in items[:3]:
        strengths = []
        if item.get("shares", 0) + item.get("saves", 0) > item.get("comments", 0) * 5:
            strengths.append("分享/收藏意图强，说明内容具有参考价值或复看价值")
        if item.get("content_type") in ("video", "reel"):
            strengths.append("视频格式更适合用快速视觉钩子展示佩戴前后变化")
        if item.get("media_notes"):
            strengths.append("画面主题集中在眼睛/镜片本身，产品识别效率高")
        top3.append({
            "rank": item.get("rank"),
            "brand": item.get("brand"),
            "platform": item.get("platform"),
            "headline": "高触达 + 高互动意图的美瞳内容",
            "why_it_won": "；".join(strengths) or "综合触达、互动率和内容相关性领先。",
            "creative_breakdown": item.get("media_notes") or "建议进一步接入视觉帧分析。",
            "what_to_copy": "复制内容结构和钩子逻辑，不直接复制素材；优先测试眼部超近景、对比、明确色彩变化和可收藏的信息结构。",
            "risk": "跨平台指标口径不同，需结合账号规模和投放情况判断。"
        })
    platforms = Counter(str(x.get("platform")) for x in items)
    regions = Counter(str(x.get("region")) for x in items)
    return {
        "weekly_summary": f"本周样本共 {len(items)} 条，内容主要来自 {', '.join(k for k,_ in platforms.most_common())}。高分内容普遍强调眼部近景、佩戴对比、妆容适配或可保存的信息设计。",
        "signals": [
            "眼部超近景与单眼对比仍是最直接的视觉钩子",
            "Pinterest 更偏收藏价值；TikTok 更偏速度和讨论；Instagram 更适合审美与品牌资产沉淀",
            "不同地区应分别观察妆容审美、自然度偏好与语言本地化"
        ],
        "top3_analysis": top3,
        "regional_note": "样本地区分布：" + "，".join(f"{k} {v}条" for k,v in regions.most_common())
    }


def _extract_output_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []) or []:
        if item.get("type") == "message":
            for content in item.get("content", []) or []:
                if content.get("type") in ("output_text", "text") and content.get("text"):
                    return content["text"]
    return ""


def ai_analysis(items: list[dict[str, Any]], date_range: str) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return fallback_analysis(items)
    model = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna").strip()
    compact = []
    for x in items[:30]:
        compact.append({k: x.get(k) for k in (
            "rank","platform","brand","region","content_type","caption","views","likes","comments","shares","saves","followers","engagement_rate","score","media_notes"
        )})
    prompt = f"""你是美瞳/彩色隐形眼镜行业的社交媒体创意策略分析师。分析周报时间 {date_range} 的帖子数据。
请只输出合法 JSON，不要 Markdown。结构必须为：
{{
  "weekly_summary": "120-220字中文总结",
  "signals": ["趋势1","趋势2","趋势3","趋势4"],
  "top3_analysis": [
    {{"rank":1,"brand":"","platform":"","headline":"","why_it_won":"","creative_breakdown":"","what_to_copy":"","risk":""}},
    {{"rank":2,...}},
    {{"rank":3,...}}
  ],
  "regional_note": "东南亚、欧美、中东差异总结"
}}
分析必须区分：内容钩子、拍摄/构图/光影、节奏或信息结构、互动机制、受众心理、可复制方法。不要把高播放直接等同于高质量，要参考互动率、分享、收藏、账号规模和平台差异。
数据：{json.dumps(compact, ensure_ascii=False)}"""
    body = {"model": model, "input": prompt}
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = _extract_output_text(payload).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:].lstrip()
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("top3_analysis"):
            return parsed
    except Exception:
        pass
    return fallback_analysis(items)
