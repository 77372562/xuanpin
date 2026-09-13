"""外贸选品助手 v1 —— 1688找货 → 成本利润核算 → Excel报告

用法:
  python main.py login                            # 首次使用: 打开浏览器登录1688
  python main.py search "沥水篮" --retail 29.9     # 单品: 采集 + 算账 + 出报告
  python main.py batch                             # 每日工作流: 按keywords.txt批量跑
  python main.py batch --demo                      # 工作流演示(模拟数据, 不开浏览器)
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
from xuanpin.mock import MOCK_ITEMS

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


def load_cfg():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_rows(items, retail, weight_override, cfg):
    from xuanpin.batch import build_rows_local
    return build_rows_local(items, retail, weight_override, cfg)


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


def cmd_batch(cfg, args):
    from xuanpin.batch import run_batch
    try:
        run_batch(cfg, list_file=args.file, demo=args.demo)
    except (FileNotFoundError, ValueError) as e:
        print(f"!! {e}")
        sys.exit(1)


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
    bp = sub.add_parser("batch", help="每日选品工作流: 读候选清单批量采集并汇总")
    bp.add_argument("--file", default="keywords.txt", help="候选清单文件, 默认keywords.txt")
    bp.add_argument("--demo", action="store_true", help="演示模式: 用模拟数据, 不开浏览器")

    args = ap.parse_args()
    cfg = load_cfg()
    if args.cmd == "login":
        cmd_login(cfg)
    elif args.cmd == "search":
        cmd_search(cfg, args)
    elif args.cmd == "test":
        cmd_test(cfg)
    elif args.cmd == "batch":
        cmd_batch(cfg, args)


if __name__ == "__main__":
    main()
