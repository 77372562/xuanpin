"""1688 采集器: 关键词搜索 → 商品数据(标题/批发价/起订量/销量/店铺)

解析策略两层, 自动降级:
  1) 拦截页面 XHR 返回的 JSON —— 不受 CSS 类名混淆影响, 首选
  2) DOM 解析兜底 —— JSON 抓不到时用
两层都失败时自动保存 截图+HTML+原始JSON 到 data/debug/, 便于快速修选择器。

登录: 使用持久化浏览器配置(data/browser_profile), 首次 `login` 后长期免登录。
"""
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from xuanpin.paths import DATA_DIR

PROFILE_DIR = DATA_DIR / "browser_profile"
DEBUG_DIR = DATA_DIR / "debug"

RESULTS_ANCHOR = "a[href*='detail.1688.com/offer'], a[href*='detail.m.1688.com'], a[href*='offerId=']"


def _kw_quote(keyword):
    # 1688搜索接口按GBK解码关键词, UTF-8百分号编码会被解成乱码
    try:
        return quote(keyword, encoding="gbk")
    except UnicodeEncodeError:
        return quote(keyword)


def _parse_price(v):
    """价格解析: 支持 '10' / '1.2~3.5' / 数字 / [{price,...}] / {'price':...} → (min,max)或None"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return (f, f) if 0.01 < f < 1e6 else None
    if isinstance(v, str):
        nums = re.findall(r"\d+(?:\.\d+)?", v)
        prices = [float(x) for x in nums if 0.01 <= float(x) <= 1e6]
        return (min(prices), max(prices)) if prices else None
    if isinstance(v, dict):
        return _parse_price(v.get("price")) if "price" in v else None
    if isinstance(v, list):
        vals = []
        for x in v:
            p = _parse_price(x.get("price") if isinstance(x, dict) else x)
            if p:
                vals.extend(p)
        return (min(vals), max(vals)) if vals else None
    return None

# 从<a>向上找商品卡片节点, 取整卡文本+图片alt/src用于解析
_CARD_JS = """
el => {
  const card = el.closest('[class*="offer" i]') || el.closest('[class*="Offer"]')
            || (el.parentElement ? el.parentElement.parentElement : null) || el;
  const img = card.querySelector('img');
  return {
    text: card.innerText || '',
    alt: img ? (img.alt || '') : '',
    src: img ? (img.src || img.getAttribute('data-src') || '') : ''
  };
}
"""


def _clean(s):
    if not s:
        return ""
    return re.sub(r"<[^>]+>", "", str(s)).replace("&nbsp;", " ").strip()


def _f(v):
    try:
        return float(str(v).replace(",", "").replace("¥", "").replace("￥", ""))
    except Exception:
        return None


def _walk(obj, pred, out):
    if isinstance(obj, dict):
        if pred(obj):
            out.append(obj)
        for v in obj.values():
            _walk(v, pred, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, pred, out)


def _looks_like_offer(d):
    title = d.get("title") or d.get("subject")
    if not title:
        return False
    url = str(d.get("detailUrl") or d.get("url") or d.get("offerUrl") or "")
    price_keys = ("price", "priceInfo", "priceDisplay", "priceRange", "priceInfoStr")
    has_price = any(k in d for k in price_keys)
    return has_price or ("detail.1688.com" in url) or re.search(r"offer/\d{6,}", url) is not None


def _price_range_in(d):
    """在商品dict内部找价格阶梯 [{beginAmount, price}, ...] → (最低价, 最高价, 起订量)"""
    ladders = []
    _walk(d, lambda x: ("beginAmount" in x or "begin_amount" in x)
          and any(k in x for k in ("price", "priceDisplay")), ladders)
    if ladders:
        prices = [_f(x.get("price") or x.get("priceDisplay")) for x in ladders]
        prices = [p for p in prices if p]
        if prices:
            moqs = [_f(x.get("beginAmount") or x.get("begin_amount")) for x in ladders]
            moqs = [m for m in moqs if m]
            return min(prices), max(prices), (int(min(moqs)) if moqs else None)
    for k in ("priceDisplay", "price", "currentPrice"):
        f = _f(d.get(k))
        if f is not None and 0.01 < f < 100000:
            return f, f, None
    return None


def _offer_from_json(d):
    title = _clean(d.get("title") or d.get("subject"))
    url = str(d.get("detailUrl") or d.get("url") or d.get("offerUrl") or "")
    if len(title) < 4:
        return None
    if not ("1688.com" in url or re.search(r"offer/\d{6,}", url)):
        return None
    pr = _price_range_in(d)
    if not pr:
        return None
    image = d.get("image") or d.get("imgUrl") or d.get("picUrl") or d.get("imageUrl") or ""
    shop = d.get("companyName") or d.get("shopName") or None
    sales = d.get("gmvInfo") or d.get("saleCount") or d.get("tradeQuantityDesc") or ""
    return dict(
        title=title,
        url=url.split("?")[0],
        image_url=str(image)[:500],
        price_min=pr[0], price_max=pr[1], moq=pr[2],
        sales_text=_clean(sales)[:80],
        shop_name=_clean(shop) if shop else None,
    )


def _loads_lenient(text):
    t = text.strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.match(r"^[\w.$]+\((.*)\)\s*;?$", t, re.S)  # 剥掉 JSONP 包裹
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


def _dedupe(offers):
    seen, out = set(), []
    for o in offers:
        m = re.search(r"(\d{6,})", o.get("url", ""))
        key = m.group(1) if m else o.get("url")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(o)
    return out


class Collector:
    def __init__(self, wait_sec=150, headless=False):
        self.wait_sec = wait_sec
        self.headless = headless
        self._pw = None
        self.ctx = None
        self._payloads = []
        self._login_hint = False

    def __enter__(self):
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=self.headless,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        self.ctx.on("response", self._on_response)
        return self

    def __exit__(self, *exc):
        try:
            self.ctx.close()
        finally:
            self._pw.stop()

    # ---------- 登录 ----------

    def logged_in(self):
        # 淘系登录标识: unb=用户ID, _nk_=账号名, login_id=旧版; 需传完整URL
        try:
            names = {c.get("name") for c in self.ctx.cookies("https://1688.com")}
            return bool(names & {"unb", "_nk_", "login_id", "cookie17"})
        except Exception:
            return False

    def login_flow(self, timeout_sec=300):
        page = self.ctx.new_page()
        page.goto("https://login.1688.com/member/signin.htm", wait_until="domcontentloaded")
        print(">> 已打开浏览器, 请在窗口中登录1688 (扫码或账密), 最长等待5分钟…")
        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            if self.logged_in():
                print(">> 登录成功! 登录态已保存, 之后无需重复登录")
                page.close()
                return True
            time.sleep(3)
        print(">> 超时未登录, 请重新运行 login")
        page.close()
        return False

    # ---------- 搜索 ----------

    def search(self, keyword, pages=1):
        all_offers = []
        for p in range(1, pages + 1):
            url = (f"https://s.1688.com/selloffer/offer_search.htm"
                   f"?keywords={_kw_quote(keyword)}&beginPage={p}")
            self._payloads = []
            page = self.ctx.new_page()
            try:
                print(f"  第{p}/{pages}页 打开: {keyword}")
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                self._wait_results(page)
                offers = self._from_mtop()
                src = "mtop接口"
                if not offers:
                    offers = self._from_json()
                    src = "XHR通用解析"
                if not offers:
                    offers = self._from_dom(page)
                    src = "DOM"
                if offers:
                    print(f"  第{p}页: {src} 解析到 {len(offers)} 个商品")
                else:
                    self._dump_debug(page, keyword, p)
                    print(f"  第{p}页: 未解析到商品, 调试文件已存到 {DEBUG_DIR}")
                all_offers += offers
            finally:
                page.close()
            if p < pages:
                time.sleep(random.uniform(2.0, 4.0))
        return _dedupe(all_offers)

    def _wait_results(self, page):
        t0 = time.time()
        while time.time() - t0 < self.wait_sec:
            try:
                if page.locator(RESULTS_ANCHOR).count() > 0:
                    return True
            except Exception:
                pass
            need_login = "login.1688.com" in page.url
            try:
                need_login = need_login or page.locator("text=登录").first.is_visible()
            except Exception:
                pass
            if need_login and not self._login_hint:
                self._login_hint = True
                print("  [需要登录] 请在浏览器窗口中登录1688, 登录后脚本自动继续…")
            time.sleep(2)
        return False

    # ---------- 解析: mtop 搜索接口 (最稳, 2026-09实测结构) ----------

    def _from_mtop(self):
        """data.data.OFFER.items[].data 里是商品真实字段"""
        out = []
        for text in self._payloads:
            data = _loads_lenient(text)
            if not isinstance(data, dict):
                continue
            outer = data.get("data")
            if not isinstance(outer, dict):
                continue
            inner = outer.get("data")
            if not isinstance(inner, dict):
                continue
            offer = inner.get("OFFER")
            if not isinstance(offer, dict):
                continue
            items = offer.get("items")
            if not isinstance(items, list):
                continue
            for it in items:
                d = it.get("data") or {}
                oid = str(d.get("offerId") or "")
                title = _clean(d.get("title") or "")
                if not oid.isdigit() or len(title) < 4:
                    continue
                pr = _parse_price((d.get("priceInfo") or {}).get("price"))
                img = ""
                lst = d.get("list")
                if isinstance(lst, dict):
                    img = str((lst.get("cover") or {}).get("pic") or "")[:500]
                sales = ""
                ap = d.get("afterPrice")
                if isinstance(ap, dict):
                    sales = _clean(ap.get("text") or "")
                bc = d.get("bookedCount")
                sales_full = " ".join(x for x in (f"成交{bc}笔" if bc else "", sales) if x)
                city = _clean(d.get("city") or "")
                shop = _clean(d.get("loginId") or "") or None
                shop_full = f"{shop}({city})" if shop and city else shop
                out.append(dict(
                    title=title,
                    url=f"https://detail.1688.com/offer/{oid}.html",
                    image_url=img,
                    price_min=pr[0] if pr else None,
                    price_max=pr[1] if pr else None,
                    moq=None,  # 搜索接口不含起订量, 需要时从详情页补
                    sales_text=sales_full[:80],
                    shop_name=shop_full,
                ))
        return _dedupe(out)

    # ---------- 解析: 通用 XHR JSON ----------

    def _on_response(self, resp):
        try:
            url = resp.url
            if "1688.com" not in url:
                return
            # mtop推荐接口URL里不含search/offer等词, 必须按host/api名匹配
            if not any(k in url for k in ("async", "rpc", "search", "offer",
                                          "mtop", "h5api", "recommend")):
                return
            ctype = (resp.headers or {}).get("content-type", "")
            if "json" not in ctype and "javascript" not in ctype:
                return
            text = resp.text()
            if text and len(text) < 5_000_000:
                self._payloads.append(text)
        except Exception:
            pass

    def _from_json(self):
        out = []
        for text in self._payloads:
            data = _loads_lenient(text)
            if data is None:
                continue
            dicts = []
            _walk(data, _looks_like_offer, dicts)
            for d in dicts:
                o = _offer_from_json(d)
                if o:
                    out.append(o)
        return _dedupe(out)

    # ---------- 解析: DOM 兜底 ----------

    def _from_dom(self, page):
        out, seen = [], set()
        try:
            anchors = page.locator(RESULTS_ANCHOR).all()
        except Exception:
            return out
        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
                m = (re.search(r"offer/(\d{6,})\.html", href)
                     or re.search(r"[?&]offerId=(\d{6,})", href))
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                info = a.evaluate(_CARD_JS) or {}
                text = info.get("text", "")
                # 价格被渲染成多行(如 "¥\n2\n.8"), 缝合回 "¥2.8"; 保留换行
                # 避免压平后把"¥15.9"+"1.1万+件"粘成15.91
                flat = re.sub(r"[¥￥]\s*(\d+)\s*\.\s*(\d+)", r"¥\1.\2", text)
                title = _clean(info.get("alt") or (text.split("\n")[0] if text else ""))
                if len(title) < 4:
                    continue
                # 只认¥符号紧跟的数字, 避免把"全网10万+件"/"1件起购"当成价格
                pairs = re.findall(
                    r"[¥￥]\s*(\d{1,6}(?:\.\d{1,2})?)(?:\s*[~～\-]\s*(\d{1,6}(?:\.\d{1,2})?))?",
                    flat)
                vals = []
                for lo, hi in pairs:
                    vals.append(float(lo))
                    if hi:
                        vals.append(float(hi))
                prices = [v for v in vals if 0.05 <= v <= 99999]
                pmn, pmx = (min(prices), max(prices)) if prices else (None, None)
                moq_m = re.search(r"(\d+)\s*件起", flat, re.I)
                sal_m = re.search(
                    r"(已售[\d\.万亿\+]*\+?|近\d+天成交[\d\.万]*|[\d\.]+[万亿]?\+\s*件)", flat)
                out.append(dict(
                    title=title,
                    url=f"https://detail.1688.com/offer/{m.group(1)}.html",
                    image_url=(info.get("src") or "")[:500],
                    price_min=pmn, price_max=pmx,
                    moq=int(moq_m.group(1)) if moq_m else None,
                    sales_text=sal_m.group(1) if sal_m else "",
                    shop_name=None,
                ))
            except Exception:
                continue
        return out

    # ---------- 详情页增强: 重量/起订量阶梯 ----------

    def detail(self, offer_id, timeout=30):
        """打开商品详情页, 从mtop接口抓真实重量和价格阶梯; 失败返回尽力而为的dict"""
        self._payloads = []
        page = self.ctx.new_page()
        try:
            page.goto(f"https://detail.1688.com/offer/{offer_id}.html",
                      wait_until="domcontentloaded", timeout=45000)
            # 重量/阶梯价分属不同接口, 反复解析直到齐全或超时
            t0 = time.time()
            info = {}
            while time.time() - t0 < timeout:
                info = self._detail_from_json()
                if info.get("weight_g") and info.get("tier_min") is not None:
                    break
                page.wait_for_timeout(1500)
            if not info.get("moq"):
                try:
                    txt = page.inner_text("body")[:6000]
                    m = re.search(r"(\d+)\s*件\s*起批", txt)
                    if m:
                        info["moq"] = int(m.group(1))
                except Exception:
                    pass
            return info
        finally:
            page.close()

    def _detail_from_json(self):
        out = {"weight_g": None, "moq": None, "tier_min": None, "tier_max": None}
        for text in self._payloads:
            data = _loads_lenient(text)
            if data is None:
                continue
            dicts = []
            _walk(data, lambda x: isinstance(x, dict), dicts)
            for d in dicts:
                if out["weight_g"] is None and "unitWeight" in d:
                    f = _f(d.get("unitWeight"))
                    if f and 0.001 < f < 5000:
                        # 1688详情重量单位为kg(小数), <100视为kg换算成克
                        out["weight_g"] = round(f * 1000 if f < 100 else f, 1)
                if out["tier_min"] is None and "minPrice" in d and "maxPrice" in d:
                    lo, hi = _f(d.get("minPrice")), _f(d.get("maxPrice"))
                    if lo and 0.01 < lo < 100000:
                        out["tier_min"], out["tier_max"] = lo, hi or lo
                        if out["moq"] is None:
                            m = _f(d.get("beginAmount"))
                            if m and m >= 1:
                                out["moq"] = int(m)
                if out["moq"] is None and ("beginAmount" in d or "begin_amount" in d):
                    m = _f(d.get("beginAmount") or d.get("begin_amount"))
                    if m and m >= 1:
                        out["moq"] = int(m)
                if all(out[k] is not None for k in ("weight_g", "moq", "tier_min")):
                    return out
        return out

    # ---------- 调试 ----------

    def _dump_debug(self, page, keyword, pno):
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        base = DEBUG_DIR / f"kw{pno}_{datetime.now().strftime('%H%M%S')}"
        try:
            page.screenshot(path=str(base) + ".png")
        except Exception:
            pass
        try:
            base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        try:
            payload_file = base.parent / (base.name + "_payloads.json")
            payload_file.write_text(
                json.dumps(self._payloads[:10], ensure_ascii=False)[:3_000_000],
                encoding="utf-8")
        except Exception:
            pass
