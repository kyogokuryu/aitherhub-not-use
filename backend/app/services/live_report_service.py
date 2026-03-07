"""
AitherHub Live Report v1 – 3層レポート生成サービス

Layer 1: 事実レイヤー（ルールベース + 時系列集計）
Layer 2: 解釈レイヤー（event_type + human tag + moments 組み合わせ）
Layer 3: 提案レイヤー（ルールベース改善提案）

出力: 勝ち区間TOP3 / 弱い区間TOP3 / 改善提案3つ
"""

import json
import logging
from typing import Optional

logger = logging.getLogger("live_report")

# ──────────────────────────────────────────────
# Signal Weights (ユーザー定義)
# ──────────────────────────────────────────────
SIGNAL_WEIGHTS = {
    "csv_order":           1.0,   # CSV order_count > 0
    "csv_gmv":             1.0,   # CSV GMV > 0
    "csv_product_clicks":  0.75,  # CSV product_clicks spike
    "purchase_popup":      0.9,   # screen_metrics purchase_notifications
    "product_viewers":     0.75,  # screen_metrics product browsing
    "human_rating":        0.8,   # user_rating >= 4
    "cta_high":            0.6,   # cta_score >= 4
    "viewer_spike":        0.4,   # viewer_count above average
    "comment_spike":       0.4,   # comment_count above average
}

# ──────────────────────────────────────────────
# Strong Segment Conditions
# ──────────────────────────────────────────────
# A segment is "strong" if:
#   - csv_order OR csv_gmv present (最強)
#   - OR purchase_popup present (最強)
#   - OR (product_viewers + cta_high + has price mention)
STRONG_THRESHOLD = 1.5  # minimum weighted score to be "strong"
WEAK_THRESHOLD = 0.3    # below this = "weak"


def _fmt_time(seconds: float) -> str:
    """秒数を MM:SS 形式に変換"""
    if seconds is None:
        return "00:00"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"


def _safe_float(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val) -> int:
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _parse_json_field(val) -> dict | list | None:
    """JSON文字列をパースする"""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


# ──────────────────────────────────────────────
# Layer 1: 事実レイヤー – Segment Scoring
# ──────────────────────────────────────────────

def score_segment(phase: dict, averages: dict) -> dict:
    """
    1つのフェーズ（区間）にスコアと信号フラグを付与する。

    Args:
        phase: video_phases row (dict)
        averages: 全フェーズの平均値 dict

    Returns:
        {
            "phase_index": int,
            "time_start": float,
            "time_end": float,
            "score": float,
            "signals": list[str],
            "reason_flags": list[str],
            "metrics": dict,
            ...
        }
    """
    signals = []
    reason_flags = []
    score = 0.0

    # CSV metrics
    gmv = _safe_float(phase.get("gmv"))
    order_count = _safe_int(phase.get("order_count"))
    viewer_count = _safe_int(phase.get("viewer_count"))
    comment_count = _safe_int(phase.get("comment_count"))
    product_clicks = _safe_int(phase.get("product_clicks"))
    new_followers = _safe_int(phase.get("new_followers"))
    conversion_rate = _safe_float(phase.get("conversion_rate"))
    gpm = _safe_float(phase.get("gpm"))
    importance_score = _safe_float(phase.get("importance_score"))

    # Human tags
    user_rating = _safe_int(phase.get("user_rating"))
    user_comment = phase.get("user_comment") or ""

    # CTA score
    cta_score = _safe_int(phase.get("cta_score"))

    # Audio features
    audio_features = _parse_json_field(phase.get("audio_features"))

    # Phase description (event_type)
    description = phase.get("phase_description") or ""

    # Product names
    product_names = _parse_json_field(phase.get("product_names")) or []

    # ── Signal detection ──

    # 1. CSV order/GMV (最強)
    if order_count > 0:
        signals.append("csv_order")
        reason_flags.append("order_occurred")
        score += SIGNAL_WEIGHTS["csv_order"] * min(order_count, 5)  # cap at 5x

    if gmv > 0:
        signals.append("csv_gmv")
        reason_flags.append("gmv_positive")
        score += SIGNAL_WEIGHTS["csv_gmv"]

    # 2. Product clicks spike
    avg_clicks = averages.get("avg_product_clicks", 0)
    if product_clicks > 0 and (avg_clicks == 0 or product_clicks > avg_clicks * 1.5):
        signals.append("csv_product_clicks")
        reason_flags.append("click_spike")
        score += SIGNAL_WEIGHTS["csv_product_clicks"]

    # 3. Human rating (強い)
    if user_rating >= 4:
        signals.append("human_rating")
        reason_flags.append("human_high_rating")
        score += SIGNAL_WEIGHTS["human_rating"]

    # 4. CTA score (中)
    if cta_score >= 4:
        signals.append("cta_high")
        reason_flags.append("cta_strong")
        score += SIGNAL_WEIGHTS["cta_high"]

    # 5. Viewer spike (補助)
    avg_viewers = averages.get("avg_viewer_count", 0)
    if viewer_count > 0 and avg_viewers > 0 and viewer_count > avg_viewers * 1.3:
        signals.append("viewer_spike")
        reason_flags.append("viewer_above_avg")
        score += SIGNAL_WEIGHTS["viewer_spike"]

    # 6. Comment spike (補助)
    avg_comments = averages.get("avg_comment_count", 0)
    if comment_count > 0 and avg_comments > 0 and comment_count > avg_comments * 1.3:
        signals.append("comment_spike")
        reason_flags.append("comment_above_avg")
        score += SIGNAL_WEIGHTS["comment_spike"]

    return {
        "phase_index": phase.get("phase_index"),
        "phase_description": description,
        "time_start": _safe_float(phase.get("time_start")),
        "time_end": _safe_float(phase.get("time_end")),
        "score": round(score, 2),
        "signals": signals,
        "reason_flags": reason_flags,
        "metrics": {
            "gmv": gmv,
            "order_count": order_count,
            "viewer_count": viewer_count,
            "comment_count": comment_count,
            "product_clicks": product_clicks,
            "new_followers": new_followers,
            "conversion_rate": conversion_rate,
            "gpm": gpm,
            "importance_score": importance_score,
        },
        "human": {
            "user_rating": user_rating,
            "user_comment": user_comment,
        },
        "cta_score": cta_score,
        "audio_features": audio_features,
        "product_names": product_names,
    }


def compute_averages(phases: list[dict]) -> dict:
    """全フェーズの平均値を計算する"""
    n = len(phases)
    if n == 0:
        return {}

    total_viewers = sum(_safe_int(p.get("viewer_count")) for p in phases)
    total_comments = sum(_safe_int(p.get("comment_count")) for p in phases)
    total_clicks = sum(_safe_int(p.get("product_clicks")) for p in phases)
    total_orders = sum(_safe_int(p.get("order_count")) for p in phases)
    total_gmv = sum(_safe_float(p.get("gmv")) for p in phases)

    return {
        "avg_viewer_count": total_viewers / n,
        "avg_comment_count": total_comments / n,
        "avg_product_clicks": total_clicks / n,
        "avg_order_count": total_orders / n,
        "avg_gmv": total_gmv / n,
        "total_viewers_peak": max((_safe_int(p.get("viewer_count")) for p in phases), default=0),
        "total_orders": total_orders,
        "total_gmv": total_gmv,
    }


# ──────────────────────────────────────────────
# Layer 2: 解釈レイヤー – Why Analysis
# ──────────────────────────────────────────────

EVENT_TYPE_MAP = {
    "つかみ": "hook",
    "問題": "problem",
    "解決": "solution",
    "デモ": "demo",
    "価格提示": "price_reveal",
    "特典": "bonus",
    "CTA": "cta",
    "雑談": "chat",
    "商品説明": "product_explanation",
    "hook": "hook",
    "problem": "problem",
    "solution": "solution",
    "demo": "demo",
    "price_reveal": "price_reveal",
    "bonus": "bonus",
    "cta": "cta",
    "chat": "chat",
}


def _detect_event_type(description: str) -> str:
    """phase_descriptionからevent_typeを推定する"""
    if not description:
        return "unknown"
    desc_lower = description.lower()
    for key, event_type in EVENT_TYPE_MAP.items():
        if key.lower() in desc_lower:
            return event_type
    return "other"


def interpret_strong_segment(scored: dict) -> str:
    """勝ち区間の「なぜ強いか」を生成する"""
    parts = []
    signals = scored["signals"]
    metrics = scored["metrics"]
    event_type = _detect_event_type(scored["phase_description"])

    # 主要な理由
    if "csv_order" in signals or "csv_gmv" in signals:
        gmv_str = f"¥{metrics['gmv']:,.0f}" if metrics['gmv'] > 0 else ""
        order_str = f"{metrics['order_count']}件" if metrics['order_count'] > 0 else ""
        parts.append(f"売上が発生しています（{order_str} {gmv_str}）".strip())

    if "csv_product_clicks" in signals:
        parts.append(f"商品クリックが急増しています（{metrics['product_clicks']}回）")

    if "cta_high" in signals:
        parts.append(f"CTAが強く入っています（スコア: {scored['cta_score']}）")

    if "human_rating" in signals:
        rating = scored["human"]["user_rating"]
        comment = scored["human"]["user_comment"]
        msg = f"人間評価が高い（★{rating}）"
        if comment:
            msg += f"：{comment}"
        parts.append(msg)

    if "viewer_spike" in signals:
        parts.append(f"視聴者数が平均を上回っています（{metrics['viewer_count']}人）")

    if "comment_spike" in signals:
        parts.append(f"コメントが活発です（{metrics['comment_count']}件）")

    # Event type context
    event_labels = {
        "price_reveal": "価格提示が入った区間です",
        "demo": "商品デモが行われた区間です",
        "cta": "購買を促すCTAが入った区間です",
        "bonus": "特典の提示が行われた区間です",
        "hook": "視聴者の注意を引くつかみの区間です",
    }
    if event_type in event_labels:
        parts.append(event_labels[event_type])

    # Product names
    if scored["product_names"]:
        names = ", ".join(scored["product_names"][:3])
        parts.append(f"対象商品: {names}")

    if not parts:
        parts.append("複数の指標が同時に高くなっています")

    return "。".join(parts) + "。"


def interpret_weak_segment(scored: dict, averages: dict) -> str:
    """弱い区間の「何が足りないか」を生成する"""
    parts = []
    metrics = scored["metrics"]
    event_type = _detect_event_type(scored["phase_description"])

    # Duration analysis
    duration = scored["time_end"] - scored["time_start"]

    if event_type == "chat":
        parts.append(f"雑談が{duration:.0f}秒続いています")
        if duration > 120:
            parts.append("長すぎる雑談は視聴者離脱の原因になります")

    if metrics["order_count"] == 0 and metrics["gmv"] == 0:
        parts.append("この区間では売上が発生していません")

    if metrics["product_clicks"] == 0:
        parts.append("商品クリックがありません")

    if scored["cta_score"] <= 1:
        parts.append("CTAがほとんど入っていません")

    if metrics["viewer_count"] > 0 and averages.get("avg_viewer_count", 0) > 0:
        if metrics["viewer_count"] < averages["avg_viewer_count"] * 0.7:
            parts.append(f"視聴者数が平均を下回っています（{metrics['viewer_count']}人）")

    if metrics["comment_count"] == 0:
        parts.append("コメントがありません（視聴者との対話不足）")

    event_weak_labels = {
        "chat": "雑談が長く、商品訴求がありません",
        "other": "配信の方向性が不明確です",
        "unknown": "コンテンツの分類ができていません",
    }
    if event_type in event_weak_labels and not parts:
        parts.append(event_weak_labels[event_type])

    if not parts:
        parts.append("エンゲージメント指標が全体的に低い区間です")

    return "。".join(parts) + "。"


# ──────────────────────────────────────────────
# Layer 3: 提案レイヤー – Improvement Suggestions
# ──────────────────────────────────────────────

def generate_suggestions(
    strong_segments: list[dict],
    weak_segments: list[dict],
    averages: dict,
) -> list[dict]:
    """
    勝ち区間と弱い区間から改善提案を生成する。
    最大3つの実行可能な提案を返す。
    """
    suggestions = []

    # Analyze weak segments for patterns
    weak_event_types = [_detect_event_type(s["phase_description"]) for s in weak_segments]
    weak_signals_flat = []
    for s in weak_segments:
        weak_signals_flat.extend(s.get("reason_flags", []))

    strong_event_types = [_detect_event_type(s["phase_description"]) for s in strong_segments]

    # 1. CTA improvement
    low_cta_count = sum(1 for s in weak_segments if s.get("cta_score", 0) <= 1)
    if low_cta_count > 0:
        # Find best CTA timing from strong segments
        best_cta_time = None
        for s in strong_segments:
            if "cta_high" in s.get("signals", []):
                best_cta_time = _fmt_time(s["time_start"])
                break
        msg = "弱い区間でCTAが不足しています。"
        if best_cta_time:
            msg += f"勝ち区間（{best_cta_time}〜）のようにCTAを入れてください。"
        else:
            msg += "「今だけ」「限定」などの購買を促すフレーズを追加してください。"
        suggestions.append({
            "category": "CTA強化",
            "suggestion": msg,
            "priority": "high",
        })

    # 2. Chat duration reduction
    long_chat_segments = [
        s for s in weak_segments
        if _detect_event_type(s["phase_description"]) == "chat"
        and (s["time_end"] - s["time_start"]) > 90
    ]
    if long_chat_segments:
        total_chat_sec = sum(s["time_end"] - s["time_start"] for s in long_chat_segments)
        suggestions.append({
            "category": "雑談時間の短縮",
            "suggestion": f"雑談区間が合計{total_chat_sec:.0f}秒あります。30秒以内に短縮し、商品説明やデモに切り替えてください。",
            "priority": "high",
        })

    # 3. Price reveal timing
    if "price_reveal" in strong_event_types:
        price_segment = next(
            (s for s in strong_segments if _detect_event_type(s["phase_description"]) == "price_reveal"),
            None
        )
        if price_segment:
            suggestions.append({
                "category": "価格提示タイミング",
                "suggestion": f"価格提示（{_fmt_time(price_segment['time_start'])}〜）で売上が発生しています。次回も同じタイミングで価格を提示してください。",
                "priority": "medium",
            })

    # 4. Comment engagement
    no_comment_weak = [s for s in weak_segments if s["metrics"]["comment_count"] == 0]
    if len(no_comment_weak) >= 2:
        suggestions.append({
            "category": "コメント拾い",
            "suggestion": "弱い区間でコメントへの反応がありません。視聴者のコメントを拾って対話することで、エンゲージメントを高めてください。",
            "priority": "medium",
        })

    # 5. Product comparison
    weak_with_products = [s for s in weak_segments if s.get("product_names")]
    if weak_with_products:
        product_name = weak_with_products[0]["product_names"][0] if weak_with_products[0]["product_names"] else "商品"
        suggestions.append({
            "category": "比較訴求の追加",
            "suggestion": f"「{product_name}」の説明区間で反応が薄いです。競合商品との比較や、ビフォーアフターを見せることで訴求力を高めてください。",
            "priority": "medium",
        })

    # 6. Viewer retention
    if averages.get("total_viewers_peak", 0) > 0:
        viewer_drop_segments = [
            s for s in weak_segments
            if s["metrics"]["viewer_count"] < averages["avg_viewer_count"] * 0.5
            and s["metrics"]["viewer_count"] > 0
        ]
        if viewer_drop_segments:
            suggestions.append({
                "category": "視聴者維持",
                "suggestion": "視聴者が大幅に減少している区間があります。この区間の前にティーザー（次に見せるもの予告）を入れて離脱を防いでください。",
                "priority": "low",
            })

    # Limit to top 3 by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda x: priority_order.get(x["priority"], 3))
    return suggestions[:3]


# ──────────────────────────────────────────────
# Main Report Generator
# ──────────────────────────────────────────────

def generate_live_report(phases: list[dict]) -> dict:
    """
    動画1本のフェーズデータから Live Report v1 を生成する。

    Args:
        phases: list of video_phases rows (dict形式)
            各dictに以下のキーが必要:
            - phase_index, phase_description, time_start, time_end
            - gmv, order_count, viewer_count, comment_count, product_clicks
            - new_followers, conversion_rate, gpm, importance_score
            - user_rating, user_comment, cta_score, audio_features
            - product_names

    Returns:
        {
            "summary_metrics": {...},
            "strong_segments": [...],  # TOP 3
            "weak_segments": [...],    # TOP 3
            "suggestions": [...],      # TOP 3
            "all_scored": [...],       # 全区間のスコア
        }
    """
    if not phases:
        return {
            "summary_metrics": {},
            "strong_segments": [],
            "weak_segments": [],
            "suggestions": [],
            "all_scored": [],
        }

    # Step 1: Compute averages
    averages = compute_averages(phases)

    # Step 2: Score all segments
    scored_segments = [score_segment(p, averages) for p in phases]

    # Step 3: Classify strong / weak
    strong = sorted(
        [s for s in scored_segments if s["score"] >= STRONG_THRESHOLD],
        key=lambda x: x["score"],
        reverse=True,
    )
    weak = sorted(
        [s for s in scored_segments if s["score"] <= WEAK_THRESHOLD],
        key=lambda x: x["score"],
    )

    # If no strong segments found, relax threshold
    if not strong and scored_segments:
        max_score = max(s["score"] for s in scored_segments)
        if max_score > 0:
            strong = sorted(
                [s for s in scored_segments if s["score"] >= max_score * 0.7],
                key=lambda x: x["score"],
                reverse=True,
            )

    # If no weak segments found, take lowest scoring
    if not weak and scored_segments:
        weak = sorted(scored_segments, key=lambda x: x["score"])[:3]

    # TOP 3
    top_strong = strong[:3]
    top_weak = weak[:3]

    # Step 4: Layer 2 – Interpretation
    for s in top_strong:
        s["interpretation"] = interpret_strong_segment(s)
        s["event_type"] = _detect_event_type(s["phase_description"])
        s["time_range_display"] = f"{_fmt_time(s['time_start'])}〜{_fmt_time(s['time_end'])}"
        s["reproducible_points"] = _get_reproducible_points(s)

    for s in top_weak:
        s["interpretation"] = interpret_weak_segment(s, averages)
        s["event_type"] = _detect_event_type(s["phase_description"])
        s["time_range_display"] = f"{_fmt_time(s['time_start'])}〜{_fmt_time(s['time_end'])}"
        s["cut_points"] = _get_cut_points(s)

    # Step 5: Layer 3 – Suggestions
    suggestions = generate_suggestions(top_strong, top_weak, averages)

    # Step 6: Summary metrics
    viewer_peak_phase = max(scored_segments, key=lambda x: x["metrics"]["viewer_count"]) if scored_segments else None
    comment_peak_phase = max(scored_segments, key=lambda x: x["metrics"]["comment_count"]) if scored_segments else None

    summary = {
        "total_phases": len(phases),
        "total_gmv": averages.get("total_gmv", 0),
        "total_orders": averages.get("total_orders", 0),
        "viewer_peak": averages.get("total_viewers_peak", 0),
        "viewer_peak_time": _fmt_time(viewer_peak_phase["time_start"]) if viewer_peak_phase else None,
        "comment_peak_time": _fmt_time(comment_peak_phase["time_start"]) if comment_peak_phase else None,
        "strong_count": len(strong),
        "weak_count": len(weak),
    }

    return {
        "summary_metrics": summary,
        "strong_segments": top_strong,
        "weak_segments": top_weak,
        "suggestions": suggestions,
        "all_scored": scored_segments,
    }


def _get_reproducible_points(scored: dict) -> list[str]:
    """勝ち区間から「次回も再現すべき点」を抽出する"""
    points = []
    event_type = _detect_event_type(scored["phase_description"])

    if "csv_order" in scored["signals"]:
        points.append("この区間の構成（商品説明→価格提示→CTA）を再現する")

    if "cta_high" in scored["signals"]:
        points.append("CTAのタイミングと表現を維持する")

    if event_type == "price_reveal":
        points.append("価格提示のタイミングを次回も同じにする")

    if event_type == "demo":
        points.append("商品デモの見せ方を次回も同じにする")

    if "viewer_spike" in scored["signals"]:
        points.append("視聴者が集まるこの時間帯を活用する")

    if "human_rating" in scored["signals"]:
        points.append("高評価の理由を分析して再現する")

    if not points:
        points.append("この区間のトーク構成を維持する")

    return points[:3]


def _get_cut_points(scored: dict) -> list[str]:
    """弱い区間から「削るべき点」を抽出する"""
    points = []
    event_type = _detect_event_type(scored["phase_description"])
    duration = scored["time_end"] - scored["time_start"]

    if event_type == "chat" and duration > 60:
        points.append(f"雑談を{max(30, duration - 60):.0f}秒短縮する")

    if scored["cta_score"] <= 1:
        points.append("CTAなしの時間を減らす")

    if scored["metrics"]["product_clicks"] == 0 and scored["metrics"]["order_count"] == 0:
        points.append("商品訴求のない時間を削減する")

    if scored["metrics"]["comment_count"] == 0:
        points.append("視聴者との対話がない区間を見直す")

    if not points:
        points.append("この区間の内容を見直して効率化する")

    return points[:3]
