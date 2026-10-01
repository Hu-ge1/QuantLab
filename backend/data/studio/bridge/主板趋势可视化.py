# Source template is UTF-8. QuantLab installs it into QMT as GBK.
"""沪深主板趋势可视化（只读，不含任何下单函数）。

用途：在 QMT 日线图中绘制 MA20/MA60/MA120/MA250，并显示趋势评分。
强趋势首次成立时标注“强趋势”，跌破 MA60 时标注“趋势转弱”。
"""


def _mean(values, size):
    if len(values) < size:
        return None
    window = values[-size:]
    return float(sum(window)) / float(size)


def _trend_state(close, ma60, ma120, ma250):
    if not ma60 or not ma120 or not ma250:
        return 0, 0.0, 0.0
    distance = (float(close) / float(ma60) - 1.0) * 100.0
    strict = close > ma60 and ma60 > ma120 and ma120 > ma250
    if not strict:
        return 0, 0.0, distance

    spread1 = (float(ma60) / float(ma120) - 1.0) * 100.0
    spread2 = (float(ma120) / float(ma250) - 1.0) * 100.0
    slope_score = min(100.0, 50.0 + spread1 * 7.0)
    slope_score += min(100.0, 50.0 + spread2 * 5.0)
    slope_score /= 2.0
    if distance <= 5.0:
        distance_score = 70.0 + max(0.0, distance) * 6.0
    elif distance <= 15.0:
        distance_score = 100.0
    elif distance <= 30.0:
        distance_score = 100.0 - (distance - 15.0) * 3.0
    else:
        distance_score = max(0.0, 55.0 - (distance - 30.0) * 2.0)
    score = 45.0 + distance_score * 0.30 + slope_score * 0.25
    return (2 if distance <= 15.0 else 1), score, distance


def _list(values):
    try:
        return [float(x) for x in list(values)]
    except Exception:
        return []


def _is_mainboard(code):
    text = str(code).upper()
    digits = text.split(".")[0]
    return digits.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))


def init(ContextInfo):
    ContextInfo.stock = ContextInfo.stockcode + "." + ContextInfo.market
    ContextInfo.set_universe([ContextInfo.stock])
    ContextInfo.previous_stage = 0
    ContextInfo.previous_close = 0.0
    ContextInfo.previous_ma60 = 0.0
    print("[主板趋势可视化] 已加载：只绘图、不下单")
    if not _is_mainboard(ContextInfo.stock):
        print("[主板趋势可视化] 当前品种不是沪深主板股票")


def handlebar(ContextInfo):
    closes = _list(ContextInfo.get_history_data(260, "1d", "close", 3))
    if len(closes) < 250:
        return

    close = closes[-1]
    ma20 = _mean(closes, 20)
    ma60 = _mean(closes, 60)
    ma120 = _mean(closes, 120)
    ma250 = _mean(closes, 250)
    stage, score, distance = _trend_state(close, ma60, ma120, ma250)

    # 主图均线。
    ContextInfo.paint("MA20", ma20, -1, 0)
    ContextInfo.paint("MA60", ma60, -1, 0)
    ContextInfo.paint("MA120", ma120, -1, 0)
    ContextInfo.paint("MA250", ma250, -1, 0)

    # 副图：趋势分、阶段（2=强趋势，1=多头偏热，0=非严格多头）、距 MA60%。
    ContextInfo.paint("趋势分", round(score, 2), -1, 0, "", "noaxis")
    ContextInfo.paint("趋势阶段", stage, -1, 0, "", "noaxis")
    ContextInfo.paint("距MA60%", round(distance, 2), -1, 0, "", "noaxis")

    if stage == 2 and ContextInfo.previous_stage != 2:
        ContextInfo.draw_text(1, close, "强趋势")
    if (ContextInfo.previous_close >= ContextInfo.previous_ma60 and
            ContextInfo.previous_ma60 > 0 and close < ma60):
        ContextInfo.draw_text(1, close, "趋势转弱")

    ContextInfo.previous_stage = stage
    ContextInfo.previous_close = close
    ContextInfo.previous_ma60 = ma60

