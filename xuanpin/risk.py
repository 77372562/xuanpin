"""风险标记: 标题命中品牌词/认证品类时给出提示, 供人工复核"""


def flags_for(title, risk_cfg):
    t = (title or "").lower()
    out = []

    for w in risk_cfg.get("brand_words", []):
        if str(w).lower() in t:
            out.append(f"疑似品牌词:{w}")
            break

    for label, words in risk_cfg.get("cert_rules", {}).items():
        if any(str(w).lower() in t for w in words):
            out.append(f"需认证/注意:{label}")

    return out
