"""供货监控: 对已登记的"在供商品"跟踪1688采购价, 生成上下架(停供/续供)建议

Temu全托管没有卖家自主上下架, 决策落地方式:
  建议下架 = 去商家后台"停止供货/删除报价"
  建议加量 = 在商家后台按新成本调整报价并扩大备货
"""
from datetime import datetime

import sqlite3

from xuanpin import costing, db

# 默认阈值, 可在config.yaml的supply节覆盖
DEFAULTS = {
    "min_ratio_stop": 0.03,   # 利润率<=3% → 建议下架(停供)
    "min_ratio_warn": 0.10,   # 利润率<10% → 谨慎(核价空间不足)
    "boost_ratio": 0.30,      # 利润率>=30% 且成本下降明显 → 建议加量
    "boost_drop": 0.15,       # 成本下降15%算"明显"
    "stale_days": 14,         # 超过N天没采到该货源价格 → 提示可能断供
}


def _thresholds(cfg):
    t = dict(DEFAULTS)
    t.update(cfg.get("supply", {}) or {})
    return t


def _latest_prices(product_id):
    """返回 (最新最低价, 上一次最低价, 最新快照时间) 或 (None,None,None)"""
    con = db.connect()
    rows = con.execute(
        """SELECT price_min, captured_at FROM price_history
           WHERE product_id=? AND price_min IS NOT NULL
           ORDER BY id DESC LIMIT 2""", (product_id,)).fetchall()
    last_seen = con.execute(
        "SELECT last_seen FROM products WHERE id=?", (product_id,)).fetchone()
    con.close()
    if not rows:
        return None, None, None
    latest = rows[0][0]
    prev = rows[1][0] if len(rows) > 1 else None
    captured = rows[0][1] or (last_seen[0] if last_seen else None)
    return latest, prev, captured


def _days_since(ts):
    if not ts:
        return None
    try:
        d = datetime.strptime(ts[:10], "%Y-%m-%d")
        return (datetime.now() - d).days
    except ValueError:
        return None


def analyze(cfg):
    """对每个在供商品计算现状+建议, 返回行列表(供界面和报表用)"""
    t = _thresholds(cfg)
    c = cfg["costing"]
    out = []
    for s in db.list_supplies(active_only=False):
        row = dict(s)
        purchase_entry = s["purchase_price"] or 0
        supply_price = s["supply_price"] or 0
        weight = s["weight_g"] or c["default_weight_g"]

        pid = s["product_id"]
        price_now, price_prev, captured = (None, None, None)
        if pid:
            price_now, price_prev, captured = _latest_prices(pid)

        purchase_now = price_now if price_now is not None else purchase_entry
        profit_entry = supply_price - costing.landed_cost(purchase_entry, weight, cfg)
        profit_now = supply_price - costing.landed_cost(purchase_now, weight, cfg)
        ratio = profit_now / supply_price if supply_price > 0 else 0

        change = None
        if price_prev:
            change = (price_now - price_prev) / price_prev
        entry_change = ((purchase_now - purchase_entry) / purchase_entry
                        if purchase_entry else None)

        stale_days = _days_since(captured)
        reasons, suggestion = [], "继续供货"

        if supply_price <= 0:
            suggestion, reasons = "待完善", ["请补录供货价"]
        elif not s["active"]:
            suggestion = "已停供"
        elif price_now is None and pid:
            suggestion, reasons = "建议换源/停供", ["1688上未找到该货源价格, 可能已下架"]
        elif stale_days is not None and stale_days > t["stale_days"]:
            suggestion, reasons = "信息过期", [f"已{stale_days}天没采集到该货源价格, 重跑该关键词"]
        elif profit_now <= 0 or ratio <= t["min_ratio_stop"]:
            suggestion = "建议下架(停供)"
            if profit_now <= 0:
                reasons.append("当前成本已亏本")
            else:
                reasons.append(f"利润率{ratio*100:.1f}%≤{t['min_ratio_stop']*100:.0f}%")
        elif ratio < t["min_ratio_warn"]:
            suggestion = "谨慎持有"
            reasons.append(f"利润率{ratio*100:.1f}%<{t['min_ratio_warn']*100:.0f}%, 核价空间不足")
        elif (ratio >= t["boost_ratio"] and entry_change is not None
              and entry_change <= -t["boost_drop"]):
            suggestion = "建议加量/续报"
            reasons.append(f"成本较录入价降{-entry_change*100:.0f}%, 利润率{ratio*100:.0f}%")

        row.update({
            "purchase_now": purchase_now,
            "price_change": change if change is not None else entry_change,
            "profit_entry": round(profit_entry, 2),
            "profit_now": round(profit_now, 2),
            "ratio_now": ratio,
            "captured_at": captured,
            "stale_days": stale_days,
            "suggestion": suggestion,
            "reasons": "; ".join(reasons),
        })
        out.append(row)
    return out


def monitor_and_report(cfg, verbose=True):
    """批量工作流结束后调用: 打印建议, 有在供商品时生成监控Excel"""
    from xuanpin import report

    rows = [r for r in analyze(cfg) if r["active"]]
    if not rows:
        if verbose:
            print(">> 供货监控: 暂无在供商品登记 (界面上添加后自动生效)")
        return None

    if verbose:
        print(f">> 供货监控: {len(rows)} 个在供商品")
        order = {"建议下架(停供)": 0, "建议换源/停供": 1, "谨慎持有": 2,
                 "信息过期": 3, "待完善": 4, "继续供货": 5, "建议加量/续报": 6}
        for r in sorted(rows, key=lambda x: order.get(x["suggestion"], 9)):
            print(f"  [{r['suggestion']}] {r['title'][:28]} | 采购 {r['purchase_now']}元"
                  f" | 当前利润 {r['profit_now']:+.2f}元"
                  f"{(' | ' + r['reasons']) if r['reasons'] else ''}")

    path = report.build_supply_report(analyze(cfg), cfg)
    if verbose:
        print(f">> 供货监控报告: {path}")
    return path
