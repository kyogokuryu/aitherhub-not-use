"""
CSV Slot Filter – CSVデータから注目タイムスロットを検出し、
解析対象のフレーム範囲を決定するモジュール。

売上・エンゲージメント指標で「注目すべき」タイムスロットだけを
重点解析することで、GPT Vision API呼び出しを大幅に削減する。
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("process_video")


# ======================================================
# MULTILINGUAL KPI COLUMN MAPPING
# ======================================================
# TikTok LIVE Analytics Excelは言語設定によりヘッダーが異なる。
# どの言語のExcelが来ても自動的に正しいKPIにマッピングする。
# 対応言語: 日本語, 中国語(簡体/繁体), 英語, 韓国語, ベトナム語, タイ語, インドネシア語

KPI_ALIASES = {
    "gmv": [
        "GMV", "gmv", "売上", "成交金额", "成交金額", "销售额", "銷售額",
        "Revenue", "revenue", "Sales", "sales",
        "매출액", "매출", "Doanh thu", "doanh thu",
        "ยอดขาย", "Pendapatan", "pendapatan",
        "gmv_metric_name_short_ui",
    ],
    "order_count": [
        "注文", "SKU注文数", "订单数", "訂單數", "成交件数", "成交件數",
        "SKU订单数", "SKU訂單數",
        "Orders", "orders", "Order Count", "order_count", "SKU Orders",
        "주문수", "주문", "Đơn hàng", "Số đơn hàng",
        "คำสั่งซื้อ", "Pesanan", "pesanan", "Jumlah Pesanan",
        "live_workbench_metric_orders_new", "orders_metric_name_short_ui",
        "seller_screen_live_core_data_created_orders",
    ],
    "viewer_count": [
        "視聴者", "視聴者数", "視聴数", "视聴者",
        "观看人数", "观众数", "在线人数", "觀看人數", "觀眾數",
        "Viewers", "viewers", "Viewer Count", "viewer_count",
        "Live Viewers", "live_viewers",
        "시청자", "시청자수", "Người xem", "Lượt xem",
        "ผู้ชม", "Penonton", "penonton", "Jumlah Penonton",
        "live_dashboard_follower_analytics_viewer", "live_workbench_metric_views",
    ],
    "like_count": [
        "いいね数", "いいね", "点赞数", "点赞", "點讚數", "按讚數",
        "Likes", "likes", "Like Count", "like_count",
        "좋아요", "좋아요수", "Lượt thích",
        "ถูกใจ", "ยอดไลค์", "Suka", "suka", "Jumlah Suka",
        "live_workbench_metric_likes",
    ],
    "comment_count": [
        "コメント数", "评论数", "评论", "評論數",
        "Comments", "comments", "Comment Count", "comment_count",
        "댓글수", "댓글", "Bình luận", "Lượt bình luận",
        "ความคิดเห็น", "Komentar", "komentar", "Jumlah Komentar",
        "live_workbench_metric_comments",
    ],
    "share_count": [
        "シェア数", "分享次数", "分享数", "分享數", "分享次數",
        "Shares", "shares", "Share Count", "share_count",
        "공유수", "공유", "Chia sẻ", "Lượt chia sẻ",
        "แชร์", "Bagikan", "bagikan", "Jumlah Bagikan",
        "live_workbench_metric_shares",
    ],
    "new_followers": [
        "新規フォロワー数", "新增粉丝数", "新增关注", "新增粉絲數",
        "New Followers", "new_followers", "Follower Gain",
        "신규 팔로워", "새 팔로워", "Người theo dõi mới",
        "ผู้ติดตามใหม่", "Pengikut Baru", "pengikut baru",
        "seller_screen_live_core_data_new_followers",
    ],
    "product_clicks": [
        "商品クリック数", "商品点击量", "商品点击数", "商品點擊量",
        "Product Clicks", "product_clicks", "Product Click Count",
        "상품 클릭수", "상품클릭", "Lượt nhấp sản phẩm",
        "คลิกสินค้า", "Klik Produk", "klik produk",
        "live_workbench_metric_product_clicks",
    ],
    "product_impressions": [
        "商品インプレッション", "商品インプレッション数",
        "商品曝光量", "商品展示量", "商品曝光數",
        "Product Impressions", "product_impressions", "Product Impression Count",
        "상품 노출수", "Lượt hiển thị sản phẩm",
        "การแสดงผลสินค้า", "Tayangan Produk", "tayangan produk",
        "live_workbench_metric_product_impressions",
    ],
    "impressions": [
        "インプレッション数", "展示量", "曝光量", "展示數",
        "Impressions", "impressions", "Impression Count",
        "노출수", "Lượt hiển thị", "การแสดงผล", "Tayangan", "tayangan",
    ],
    "gpm": [
        "視聴GPM", "表示GPM", "GPM", "gpm",
        "千次观看成交金额", "千次觀看成交金額",
        "gmv_per_1k_views", "GMV per 1K Views",
        "live_workbench_basic_core_data_gpm", "live_workbench_metric_show_gpm_new",
    ],
    "ctor": [
        "CTOR", "ctor", "CTOR（SKU注文数）", "CVR", "cvr",
        "点击成交转化率", "點擊成交轉化率",
        "Click Through Order Rate", "click_conversion",
        "전환율", "Tỷ lệ chuyển đổi",
        "ctor_metric_name_short_ui", "live_workbench_metric_co_new",
    ],
    "aov": [
        "AOV", "aov", "客単価", "客单价", "客單價",
        "Average Order Value", "average_order_value",
        "평균주문금액", "Giá trị đơn hàng trung bình",
        "aov_metric_name_short_ui",
    ],
    "product_sales": [
        "商品の販売数", "商品销量", "销售量", "商品銷量",
        "Product Sales", "product_sales", "Units Sold", "units_sold",
        "상품 판매수", "Số lượng bán", "ยอดขายสินค้า", "Penjualan Produk",
        "live_dashboard_performance_trends_items_sold",
    ],
    "customers": [
        "カスタマー数", "成交客户数", "买家数", "成交客戶數",
        "Customers", "customers", "Buyers", "buyers",
        "고객수", "구매자수", "Khách hàng", "Người mua",
        "ลูกค้า", "Pelanggan", "pelanggan", "Pembeli",
        "live_workbench_metric_buyers_new",
    ],
    "live_ctr": [
        "LIVE CTR", "live_ctr", "直播点击率", "直播點擊率",
        "Live Click Through Rate", "click_rate",
        "live_ctr_metric_name_short_ui",
    ],
    "comment_rate": [
        "コメント率", "评论率", "評論率",
        "Comment Rate", "comment_rate",
        "댓글률", "Tỷ lệ bình luận",
        "Live comment rate",
    ],
    "payment_rate": [
        "支払率", "支付率",
        "Payment Rate", "payment_rate", "Pay Rate",
        "결제율", "Tỷ lệ thanh toán",
        "อัตราการชำระเงิน", "Tingkat Pembayaran",
        "live_dashboard_product_payment_rate",
    ],
    "cart_adds": [
        "カートに追加された回数", "加购次数", "加入购物车", "加購次數",
        "Add to Cart", "add_to_cart", "Cart Adds", "cart_adds",
        "장바구니 추가", "Thêm vào giỏ hàng",
        "เพิ่มลงตะกร้า", "Tambah ke Keranjang",
    ],
    "tap_through_rate": [
        "タップスルー率", "点击率", "點擊率",
        "Tap Through Rate", "tap_through_rate",
        "탭 스루율", "Tỷ lệ nhấp", "อัตราการแตะ", "Rasio Klik",
        "tap_through_rate_metric_name_short_ui",
    ],
    "time": [
        "時間", "时间", "時間",
        "Time", "time", "Timestamp", "timestamp",
        "시간", "Thời gian", "เวลา", "Waktu", "waktu",
    ],
    "product_name": [
        "商品名", "商品名称", "产品名称", "商品名稱",
        "Product Name", "product_name", "Product Title",
        "상품명", "Tên sản phẩm", "ชื่อสินค้า", "Nama Produk",
    ],
    "product_id": [
        "商品ID", "产品ID", "商品ID",
        "Product ID", "product_id", "SKU ID", "sku_id",
        "상품ID", "ID sản phẩm", "ID สินค้า", "ID Produk",
    ],
}


def get_kpi_aliases(kpi_name: str) -> list[str]:
    """KPI名から多言語候補キーリストを取得する。"""
    return KPI_ALIASES.get(kpi_name, [kpi_name])


# ======================================================
# RULE DEFINITIONS
# ======================================================

# 各ルールの重み（スコアに加算）
RULES = [
    # --- 売上系（最重要）---
    {
        "name": "high_gmv",
        "description": "GMVが高い（売上発生）",
        "keys": KPI_ALIASES["gmv"],
        "condition": "gt_zero",
        "weight": 3,
    },
    {
        "name": "high_orders",
        "description": "成約件数が多い",
        "keys": KPI_ALIASES["order_count"],
        "condition": "gt_zero",
        "weight": 3,
    },
    # --- コンバージョン系 ---
    {
        "name": "high_conversion",
        "description": "クリック成交転化率が高い",
        "keys": KPI_ALIASES["ctor"],
        "condition": "above_mean",
        "weight": 2,
    },
    {
        "name": "high_gmv_per_view",
        "description": "千次観看成交金額が高い",
        "keys": KPI_ALIASES["gpm"],
        "condition": "above_mean",
        "weight": 2,
    },
    # --- エンゲージメント系 ---
    {
        "name": "high_viewers",
        "description": "観看人数が多い",
        "keys": KPI_ALIASES["viewer_count"],
        "condition": "above_mean",
        "weight": 1,
    },
    {
        "name": "high_comments",
        "description": "コメント率が高い",
        "keys": KPI_ALIASES["comment_rate"],
        "condition": "above_mean",
        "weight": 1,
    },
    {
        "name": "high_click_rate",
        "description": "直播点击率が高い",
        "keys": KPI_ALIASES["live_ctr"],
        "condition": "above_mean",
        "weight": 1,
    },
    {
        "name": "new_followers",
        "description": "新規フォロワー獲得",
        "keys": KPI_ALIASES["new_followers"],
        "condition": "gt_zero",
        "weight": 2,
    },
]


# ======================================================
# HELPER FUNCTIONS
# ======================================================

def _find_key(entry: dict, candidate_keys: list[str]) -> str | None:
    """エントリから候補キーにマッチするキーを探す（大文字小文字無視）"""
    entry_keys_lower = {k.lower(): k for k in entry.keys()}
    for ck in candidate_keys:
        if ck.lower() in entry_keys_lower:
            return entry_keys_lower[ck.lower()]
    return None


def _safe_float(val) -> float | None:
    """安全にfloatに変換"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_time_to_seconds(val) -> float | None:
    """
    時刻文字列を秒数に変換。
    対応形式: HH:MM, HH:MM:SS, MM:SS, 直接秒数
    """
    if val is None:
        return None

    # datetime.time オブジェクトの場合
    if hasattr(val, 'hour') and hasattr(val, 'minute'):
        return val.hour * 3600 + val.minute * 60 + getattr(val, 'second', 0)

    val_str = str(val).strip()

    # 直接数値の場合
    try:
        return float(val_str)
    except (ValueError, TypeError):
        pass

    # HH:MM:SS or HH:MM or MM:SS
    parts = val_str.split(":")
    try:
        if len(parts) == 2:
            h, m = int(parts[0]), int(parts[1])
            # HH:MM形式（時間が24未満ならHH:MM）
            if h < 24:
                return h * 3600 + m * 60
            else:
                return h * 60 + m
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, TypeError):
        pass

    return None


def _detect_time_key(entries: list[dict]) -> str | None:
    """トレンドデータから時刻カラムを自動検出（多言語対応）"""
    if not entries:
        return None
    sample = entries[0]
    # KPI_ALIASES["time"]を先にチェック
    found = _find_key(sample, KPI_ALIASES["time"])
    if found:
        return found
    # フォールバック: 部分一致
    for k in sample.keys():
        kl = k.lower()
        if any(w in kl for w in ["时间", "時間", "time", "timestamp", "시간", "waktu", "เวลา"]):
            return k
    return None


# ======================================================
# MAIN FUNCTIONS
# ======================================================

def compute_slot_scores(trends: list[dict]) -> list[dict]:
    """
    各タイムスロットにスコアを付与。
    ルールに基づいて各指標を評価し、重み付きスコアを算出。

    Returns:
        list of {time_key, time_sec, score, matched_rules, raw_entry}
    """
    if not trends:
        return []

    time_key = _detect_time_key(trends)
    if not time_key:
        logger.warning("[CSV_FILTER] No time column found in trend data")
        return []

    # 全エントリの数値を事前計算（平均算出用）
    all_values = {}
    for rule in RULES:
        matched_key = _find_key(trends[0], rule["keys"])
        if matched_key:
            vals = [_safe_float(t.get(matched_key)) for t in trends]
            vals = [v for v in vals if v is not None]
            if vals:
                all_values[rule["name"]] = {
                    "key": matched_key,
                    "mean": sum(vals) / len(vals),
                    "values": vals,
                }

    # 各スロットにスコアを付与
    scored_slots = []
    for entry in trends:
        time_val = entry.get(time_key)
        time_sec = _parse_time_to_seconds(time_val)
        if time_sec is None:
            continue

        score = 0
        matched_rules = []

        for rule in RULES:
            rule_info = all_values.get(rule["name"])
            if not rule_info:
                continue

            val = _safe_float(entry.get(rule_info["key"]))
            if val is None:
                continue

            triggered = False
            if rule["condition"] == "gt_zero":
                triggered = val > 0
            elif rule["condition"] == "above_mean":
                triggered = val > rule_info["mean"]

            if triggered:
                score += rule["weight"]
                matched_rules.append(rule["name"])

        scored_slots.append({
            "time_key": str(time_val),
            "time_sec": time_sec,
            "score": score,
            "matched_rules": matched_rules,
            "raw_entry": entry,
        })

    return scored_slots


def get_important_time_ranges(
    trends: list[dict],
    video_duration_sec: float,
    video_start_time_sec: float | None = None,
    margin_sec: float = 600,  # 前後10分
    min_score: int = 1,
) -> list[dict]:
    """
    CSVデータから注目タイムスロットを検出し、
    動画内の解析対象フレーム範囲を返す。

    Args:
        trends: CSVから読み込んだトレンドデータ
        video_duration_sec: 動画の長さ（秒）
        video_start_time_sec: 動画の開始時刻（秒、実際の時刻）。
            Noneの場合、CSVの最初のエントリの時刻を使用。
        margin_sec: 各スロットの前後マージン（秒）
        min_score: 注目と判定する最低スコア

    Returns:
        list of {start_sec, end_sec, start_frame, end_frame, score, reasons}
        ※ start_frame/end_frame は動画内のフレーム番号（fps=1前提）
    """
    scored = compute_slot_scores(trends)
    if not scored:
        logger.info("[CSV_FILTER] No scored slots, analyzing all frames")
        return []

    # 動画開始時刻の推定
    if video_start_time_sec is None:
        # CSVの最初のエントリの時刻を動画開始時刻と仮定
        video_start_time_sec = scored[0]["time_sec"]
        logger.info(
            "[CSV_FILTER] Estimated video start time: %s (%d sec)",
            scored[0]["time_key"], video_start_time_sec,
        )

    # 注目スロットをフィルタ
    important = [s for s in scored if s["score"] >= min_score]

    if not important:
        logger.info("[CSV_FILTER] No important slots found (all scores < %d)", min_score)
        return []

    logger.info(
        "[CSV_FILTER] Found %d important slots out of %d total",
        len(important), len(scored),
    )

    # 各スロットの前後マージンを含む範囲を計算
    ranges = []
    for slot in important:
        # CSVの時刻を動画内の秒数に変換
        slot_video_sec = slot["time_sec"] - video_start_time_sec

        range_start = max(0, slot_video_sec - margin_sec)
        range_end = min(video_duration_sec, slot_video_sec + margin_sec)

        ranges.append({
            "start_sec": range_start,
            "end_sec": range_end,
            "start_frame": int(range_start),  # fps=1
            "end_frame": int(range_end),       # fps=1
            "score": slot["score"],
            "reasons": slot["matched_rules"],
            "slot_time": slot["time_key"],
        })

    # 重複範囲をマージ
    merged = _merge_overlapping_ranges(ranges)

    for r in merged:
        logger.info(
            "[CSV_FILTER] Range: %d-%d sec (frame %d-%d), score=%d, reasons=%s",
            r["start_sec"], r["end_sec"],
            r["start_frame"], r["end_frame"],
            r["score"], r["reasons"],
        )

    skipped_slots = [s for s in scored if s["score"] < min_score]
    for s in skipped_slots:
        logger.info(
            "[CSV_FILTER] SKIP slot %s (score=%d < %d)",
            s["time_key"], s["score"], min_score,
        )

    return merged


def _merge_overlapping_ranges(ranges: list[dict]) -> list[dict]:
    """重複する範囲をマージ"""
    if not ranges:
        return []

    # start_secでソート
    sorted_ranges = sorted(ranges, key=lambda r: r["start_sec"])
    merged = [sorted_ranges[0].copy()]

    for r in sorted_ranges[1:]:
        last = merged[-1]
        if r["start_sec"] <= last["end_sec"]:
            # 重複 → マージ
            last["end_sec"] = max(last["end_sec"], r["end_sec"])
            last["end_frame"] = max(last["end_frame"], r["end_frame"])
            last["score"] = max(last["score"], r["score"])
            last["reasons"] = list(set(last["reasons"] + r["reasons"]))
        else:
            merged.append(r.copy())

    return merged


def is_phase_in_important_range(
    phase_start_frame: int,
    phase_end_frame: int,
    important_ranges: list[dict],
) -> bool:
    """
    フェーズが注目範囲内にあるかチェック。
    フェーズの一部でも注目範囲と重なっていればTrue。
    """
    if not important_ranges:
        # 注目範囲が未設定の場合は全フェーズを解析
        return True

    for r in important_ranges:
        # 範囲が重なるかチェック
        if phase_start_frame <= r["end_frame"] and phase_end_frame >= r["start_frame"]:
            return True

    return False


def filter_phases_by_importance(
    keyframes: list[int],
    total_frames: int,
    important_ranges: list[dict],
) -> list[bool]:
    """
    各フェーズが注目範囲内にあるかのブーリアンリストを返す。

    Args:
        keyframes: フェーズ境界フレーム番号のリスト
        total_frames: 総フレーム数
        important_ranges: get_important_time_ranges()の戻り値

    Returns:
        list[bool] - 各フェーズ（len = len(keyframes)+1）が注目かどうか
    """
    if not important_ranges:
        # 注目範囲が未設定 → 全フェーズを解析
        return [True] * (len(keyframes) + 1)

    extended = [0] + keyframes + [total_frames - 1]
    results = []

    for i in range(len(extended) - 1):
        start = extended[i]
        end = extended[i + 1]
        is_important = is_phase_in_important_range(start, end, important_ranges)
        results.append(is_important)

    important_count = sum(results)
    total_count = len(results)
    logger.info(
        "[CSV_FILTER] Phase filter: %d/%d phases marked as important (%.0f%% reduction)",
        important_count, total_count,
        (1 - important_count / total_count) * 100 if total_count > 0 else 0,
    )

    return results
