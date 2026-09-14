"""Temu 自动比价: 浏览器渲染搜索页 → 商品价格带 (技术思路致谢 n1ceh4t/Temu-Item-Scraper)

实测要点(2026-09): temu.com SSR页只有空壳, 商品列表由客户端API渲染, 必须开真浏览器;
中国大陆直连时页面内部API不通(显示No internet), 需系统代理或config指定代理。
无代理/失败时优雅降级, 不阻塞选品工作流。
"""
import json
import re
import statistics
from urllib.parse import quote

USD_TO_CNY = 7.2
CAD_TO_CNY = 5.2
PRICE_RE = re.compile(r"(?<![\w.])C?\$\s*(\d{1,4}\.\d{2})")


def _resolve_proxy(cfg):
    """代理优先级: config.temu.proxy > Windows系统代理 > 无"""
    p = (cfg.get("temu", {}) or {}).get("proxy", "")
    if p:
        return p if p.startswith("http") else "http://" + p
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if not enable:
            return None
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if "=" in server:
            for part in server.split(";"):
                if part.startswith(("https=", "http=")):
                    server = part.split("=", 1)[1]
                    break
        return ("http://" + server) if server else None
    except Exception:
        return None


def compare(keyword, cfg, headless=False):
    """返回 {ok, count, min, median, max, currency, note}; 失败时ok=False不抛异常"""
    from playwright.sync_api import sync_playwright

    proxy = _resolve_proxy(cfg)
    wait_sec = int((cfg.get("temu", {}) or {}).get("wait_sec", 30))
    prices, saw_no_internet = [], False

    with sync_playwright() as p:
        kw_args = {"headless": headless,
                   "args": ["--disable-blink-features=AutomationControlled"]}
        if proxy:
            kw_args["proxy"] = {"server": proxy}
        b = p.chromium.launch(**kw_args)
        try:
            ctx = b.new_context(
                viewport={"width": 1400, "height": 900},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
                locale="en-US")
            page = ctx.new_page()
            page.goto(f"https://www.temu.com/search_result.html?search_key={quote(keyword)}",
                      wait_until="domcontentloaded", timeout=45000)
            deadline = wait_sec
            while deadline > 0:
                page.wait_for_timeout(2000)
                deadline -= 2
                try:
                    body = page.inner_text("body")
                except Exception:
                    continue
                if "No internet" in body or "couldn't connect" in body.lower():
                    saw_no_internet = True
                    continue
                prices = sorted(set(round(float(x), 2)
                                    for x in PRICE_RE.findall(body) if 0.3 <= float(x) <= 5000))
                if len(prices) >= 5:
                    break
        finally:
            b.close()

    if saw_no_internet and len(prices) < 5:
        tip = "Temu页面内部API不通" + ("(已用代理仍不通, 换个节点试试)" if proxy else "(未检测到代理, 开代理后自动生效)")
        return {"ok": False, "count": len(prices), "note": tip}
    if len(prices) < 5:
        return {"ok": False, "count": len(prices), "note": "价格样本不足, 请人工看Temu"}

    # 多数情况下北美站按USD标价; 若你所在站点为加元等, 到config调汇率口径即可
    return {"ok": True, "count": len(prices),
            "min": prices[0], "median": round(statistics.median(prices), 2),
            "max": prices[-1], "currency": "USD", "note": ""}


def suggest_retail(result):
    """外币中位价 → 建议对标人民币零售价(粗略换算, 供人工复核)"""
    if not result.get("ok"):
        return None
    rate = CAD_TO_CNY if result.get("currency") == "CAD" else USD_TO_CNY
    return round(result["median"] * rate, 1)
