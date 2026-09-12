"""外贸选品助手 v1 —— 1688找货 → 成本利润核算 → Excel报告

用法:
  python main.py login                            # 首次使用: 打开浏览器登录1688
  python main.py search "沥水篮" --retail 29.9     # 采集 + 算账 + 出报告
  python main.py search "沥水篮" --retail 29.9 --weight 350 --pages 3
  python main.py test                             # 模拟数据自检(不开浏览器)
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import yaml

from xuanpin import costing, db, report, risk

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


def load_cfg():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_rows(items, retail, weight_override, cfg):
    rows = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for it in items:
        pmn, pmx = it.get("price_min"), it.get("price_max")
        if pmn is None and pmx is None:
            continue  # 没有价格的无法核算, 跳过(库里仍保留)
        pmn = pmn if pmn is not None else pmx
        pmx = pmx if pmx is not None else pmn
        w = weight_override or it.get("weight_g")
        est_low = costing.estimate(pmx, w, retail, cfg)    # 保守: 采购取高价
        est_high = costing.estimate(pmn, w, retail, cfg)   # 乐观: 采购取低价
        rows.append({
            **it,
            "weight_g": est_low["weight_g"],
            "landed": est_low["landed"],
            "supply_cap": est_low["supply_cap"],
            "profit_low": est_low["profit"],
            "profit_high": est_high["profit"],
            "margin_low": est_low["margin"],
            "flags": risk.flags_for(it.get("title") or "", cfg["risk"]),
            "captured_at": now,
        })
    return rows


def print_top(rows, n):
    rows_sorted = sorted(rows, key=lambda r: r["profit_low"], reverse=True)
    print(f"\n===== 候选TOP{min(n, len(rows_sorted))} (按保守利润排序) =====")
    for i, r in enumerate(rows_sorted[:n], 1):
        fl = " 【" + "; ".join(r["flags"]) + "】" if r["flags"] else ""
        print(f"{i:>2}. 保守利润 {r['profit_low']:+8.2f}元 | 利润率 {r['margin_low']*100:5.1f}% "
              f"| {r['title'][:36]}{fl}")


def ask_retail():
    while True:
        s = input("请输入该品在Temu上的目标零售价(¥, 例如 29.9): ").strip()
        try:
            v = float(s)
            if v > 0:
                return v
        except ValueError:
            pass
        print("  输入无效, 请输入正数")


def cmd_login(cfg):
    from xuanpin.collector1688 import Collector
    with Collector() as c:
        if c.logged_in():
            print(">> 已处于登录状态, 无需重复登录")
            return
        c.login_flow()


def cmd_search(cfg, args):
    from xuanpin.collector1688 import Collector
    retail = args.retail if args.retail else ask_retail()
    pages = args.pages or cfg["search"]["max_pages"]

    with Collector(wait_sec=cfg["search"]["wait_results_sec"]) as c:
        if not c.logged_in():
            if not c.login_flow():
                sys.exit(1)
        items = c.search(args.keyword, pages=pages)

    if not items:
        print("\n!! 未采集到商品。可能是登录过期/风控验证/页面改版, "
              "可先重跑 login; 若仍失败, 把 data/debug/ 下的文件发我修解析器。")
        sys.exit(1)

    db.save_products(args.keyword, items)
    rows = build_rows(items, retail, args.weight, cfg)
    print(f"\n>> 采集入库 {len(items)} 个商品, 其中 {len(rows)} 个可核算 "
          f"(价格历史已记录, 下次同关键词采集可比对价格变化)")

    path = report.build(args.keyword, retail, rows, cfg)
    db.save_run(args.keyword, retail, str(path))
    print_top(rows, cfg["report"]["top_n_console"])
    print(f"\n>> 完整报告: {path}")


MOCK_ITEMS = [
    dict(title="不锈钢沥水篮23cm加厚厨房滤水篮", url="https://detail.1688.com/offer/710000000001.html",
         image_url="", price_min=3.2, price_max=4.8, moq=2, sales_text="已售30万+", shop_name="某某厨具厂"),
    dict(title="ins风陶瓷马克杯350ml手绘釉下彩", url="https://detail.1688.com/offer/710000000002.html",
         image_url="", price_min=6.5, price_max=9.0, moq=24, sales_text="已售2万+", shop_name="某某陶瓷厂"),
    dict(title="卡通毛绒挂件小熊背包挂饰", url="https://detail.1688.com/offer/710000000003.html",
         image_url="", price_min=1.8, price_max=3.5, moq=100, sales_text="已售10万+", shop_name=None),
    dict(title="磁吸手机支架铝合金车载重力支架", url="https://detail.1688.com/offer/710000000004.html",
         image_url="", price_min=2.4, price_max=4.0, moq=50, sales_text="已售5万+", shop_name=None),
    dict(title="香薰精油补充瓶100ml滚珠玻璃瓶", url="https://detail.1688.com/offer/710000000005.html",
         image_url="", price_min=1.2, price_max=2.2, moq=200, sales_text="", shop_name=None),
    dict(title="夏季冰感毛巾速干吸汗运动毛巾", url="https://detail.1688.com/offer/710000000006.html",
         image_url="", price_min=2.8, price_max=4.5, moq=100, sales_text="已售8万+", shop_name=None),
    dict(title="儿童发夹套装24个甜美发型配件", url="https://detail.1688.com/offer/710000000007.html",
         image_url="", price_min=1.5, price_max=2.8, moq=300, sales_text="已售1万+", shop_name=None),
    dict(title="简约亚克力收纳盒三层抽屉式", url="https://detail.1688.com/offer/710000000008.html",
         image_url="", price_min=8.5, price_max=12.0, moq=10, sales_text="已售5000+", shop_name=None),
]


def cmd_test(cfg):
    retail = 29.9
    rows = build_rows(MOCK_ITEMS, retail, None, cfg)
    db.save_products("自检测试", MOCK_ITEMS)
    path = report.build("自检测试", retail, rows, cfg)
    db.save_run("自检测试", retail, str(path))
    print_top(rows, cfg["report"]["top_n_console"])
    print(f"\n>> 自检通过: 数据库={db.DB_PATH}")
    print(f">> 报告文件: {path}")


def main():
    ap = argparse.ArgumentParser(description="外贸选品助手 v1 (1688找货→成本利润→Excel报告)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="打开浏览器登录1688 (首次使用先执行一次)")
    sp = sub.add_parser("search", help="关键词采集并生成选品报告")
    sp.add_argument("keyword", help="商品关键词, 建议来自拼多多/榜单看到的爆品")
    sp.add_argument("--retail", type=float, default=None, help="该品在Temu上的目标零售价(¥)")
    sp.add_argument("--pages", type=int, default=None, help="采集页数, 默认取config里的max_pages")
    sp.add_argument("--weight", type=float, default=None,
                    help="预估单件重量(克), 采集不到重量时用它估运费")
    sub.add_parser("test", help="用模拟数据跑通报表流程 (不开浏览器)")

    args = ap.parse_args()
    cfg = load_cfg()
    if args.cmd == "login":
        cmd_login(cfg)
    elif args.cmd == "search":
        cmd_search(cfg, args)
    elif args.cmd == "test":
        cmd_test(cfg)


if __name__ == "__main__":
    main()
