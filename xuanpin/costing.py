"""成本/利润计算 (Temu全托管JIT模式)

供货价上限 = 目标零售价 × (1 - platform_take)   [platform_take 含平台利润+国际物流尾程]
落地成本   = 采购价 + 包装 + 国内送仓 (+ 超重附加)
单件利润   = 供货价上限 - 落地成本
"""


def estimate(purchase_price, weight_g, retail_price, cfg):
    c = cfg["costing"]
    take = cfg["fees"]["platform_take"]

    w = weight_g if weight_g and weight_g > 0 else c["default_weight_g"]
    over = max(0.0, w - c["free_weight_g"])
    surcharge = over / 1000.0 * c["overweight_rate_per_kg"]

    landed = purchase_price + c["packaging_fee"] + c["inbound_ship"] + surcharge
    supply_cap = retail_price * (1 - take)
    profit = supply_cap - landed
    margin = profit / supply_cap if supply_cap > 0 else 0.0
    return {
        "weight_g": w,
        "landed": round(landed, 2),
        "supply_cap": round(supply_cap, 2),
        "profit": round(profit, 2),
        "margin": margin,
    }
