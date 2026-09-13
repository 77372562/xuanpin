"""每日选品工作流: 读取候选清单 → 逐个关键词采集核算 → 汇总总表"""
from pathlib import Path

from xuanpin import db


def parse_keywords_file(path):
    """解析候选清单: 每行 关键词[,零售价[,重量克]]; #开头为注释; 编码兼容记事本"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"候选清单不存在: {p} (可用记事本编辑 keywords.txt)")
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            raw = p.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raise ValueError(f"无法识别文件编码: {p}")

    entries = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("＜") or line.startswith("<"):
            continue
        parts = [x.strip() for x in line.replace("，", ",").split(",")]
        kw = parts[0]
        retail = weight = None
        if len(parts) >= 2 and parts[1]:
            try:
                retail = float(parts[1])
            except ValueError:
                raise ValueError(f"第{lineno}行零售价无效: {line}")
        if len(parts) >= 3 and parts[2]:
            try:
                weight = float(parts[2])
            except ValueError:
                raise ValueError(f"第{lineno}行重量无效: {line}")
        if kw:
            entries.append({"keyword": kw, "retail": retail, "weight": weight,
                            "line": lineno})
    return entries


def fill_missing_retail(entries, interactive=True):
    """清单里没写零售价的: 命令行模式逐个询问; 界面模式直接报错提示补齐"""
    missing = [e for e in entries if e["retail"] is None]
    if not missing:
        return entries
    if not interactive:
        names = ", ".join(e["keyword"] for e in missing)
        raise ValueError(f"以下候选品没填Temu零售价, 请先在清单里补齐: {names}")
    for e in missing:
        while True:
            s = input(f"  [{e['keyword']}] 在Temu上的零售价(¥): ").strip()
            try:
                v = float(s)
                if v > 0:
                    e["retail"] = v
                    break
            except ValueError:
                pass
            print("  输入无效, 请输入正数, 例如 15.9")
    return entries


def single_search(cfg, keyword, retail, weight=None, pages=None):
    """单品快查: 采集+核算+出报告, 返回报告路径"""
    from xuanpin import report
    from xuanpin.collector1688 import Collector

    pages = pages or cfg["search"]["max_pages"]
    c = Collector(wait_sec=cfg["search"]["wait_results_sec"])
    c.__enter__()
    try:
        if not c.logged_in():
            if not c.login_flow():
                raise SystemExit("登录失败/超时, 请重新执行登录")
        items = c.search(keyword, pages=pages)
    finally:
        c.__exit__()

    if not items:
        print("!! 未采集到商品: 可能是登录过期/滑块验证/页面改版, "
              "把 data/debug/ 下的文件发给维护者即可修复")
        return None
    db.save_products(keyword, items)
    rows = build_rows_local(items, retail, weight, cfg)
    print(f">> 采集入库 {len(items)} 个商品, 其中 {len(rows)} 个可核算")
    path = report.build(keyword, retail, rows, cfg)
    db.save_run(keyword, retail, str(path))
    for i, r in enumerate(sorted(rows, key=lambda x: x["profit_low"], reverse=True)[:5], 1):
        fl = " 【" + "; ".join(r["flags"]) + "】" if r["flags"] else ""
        print(f"{i:>2}. 保守利润 {r['profit_low']:+8.2f}元 | 利润率 {r['margin_low']*100:5.1f}% "
              f"| {r['title'][:36]}{fl}")
    print(f">> 报告: {path}")
    return path


def build_rows_local(items, retail, weight_override, cfg):
    """与main.build_rows一致的行构造, 避免循环导入"""
    from xuanpin import costing, risk
    from datetime import datetime as _dt
    rows = []
    now = _dt.now().strftime("%Y-%m-%d %H:%M")
    for it in items:
        pmn, pmx = it.get("price_min"), it.get("price_max")
        if pmn is None and pmx is None:
            continue
        pmn = pmn if pmn is not None else pmx
        pmx = pmx if pmx is not None else pmn
        w = weight_override or it.get("weight_g")
        est_low = costing.estimate(pmx, w, retail, cfg)
        est_high = costing.estimate(pmn, w, retail, cfg)
        rows.append({**it, "weight_g": est_low["weight_g"], "landed": est_low["landed"],
                     "supply_cap": est_low["supply_cap"], "profit_low": est_low["profit"],
                     "profit_high": est_high["profit"], "margin_low": est_low["margin"],
                     "flags": risk.flags_for(it.get("title") or "", cfg["risk"]),
                     "captured_at": now})
    return rows


def run_batch(cfg, list_file="keywords.txt", demo=False, top_n=3,
              interactive=True, cancel_check=None):
    """执行批量工作流, 返回 (汇总报告路径, entries)"""
    from xuanpin import report
    from xuanpin.mock import MOCK_ITEMS

    entries = parse_keywords_file(list_file)
    if not entries:
        raise ValueError(f"清单里没有候选品, 请先编辑 {list_file}")
    fill_missing_retail(entries, interactive=interactive)
    print(f"\n>> 共 {len(entries)} 个候选品, 开始工作流…\n")

    collector = None
    if not demo:
        from xuanpin.collector1688 import Collector
        collector = Collector(wait_sec=cfg["search"]["wait_results_sec"])
        collector.__enter__()
        if not collector.logged_in():
            if not collector.login_flow():
                collector.__exit__()
                raise SystemExit("登录失败, 请重跑登录")

    results = []
    try:
        for i, e in enumerate(entries, 1):
            if cancel_check and cancel_check():
                print(">> 收到停止指令, 中止剩余关键词")
                break
            kw, retail = e["keyword"], e["retail"]
            print(f"===== [{i}/{len(entries)}] {kw} (对标零售价 {retail}元) =====")
            if demo:
                items = MOCK_ITEMS
            else:
                items = collector.search(kw, pages=cfg["search"]["max_pages"])
                if items:
                    db.save_products(kw, items)
            if not items:
                print("  !! 未采集到商品, 跳过 (细节见上方日志)\n")
                results.append({**e, "rows": [], "per_report": None})
                continue

            rows = build_rows_local(items, retail, e["weight"], cfg)
            per_path = None
            if not demo:
                per_path = report.build(kw, retail, rows, cfg)
            results.append({**e, "rows": rows, "per_report": per_path})
            top = sorted(rows, key=lambda r: r["profit_low"], reverse=True)[:top_n]
            for j, r in enumerate(top, 1):
                fl = " 【" + "; ".join(r["flags"]) + "】" if r["flags"] else ""
                print(f"  {j}. 保守利润 {r['profit_low']:+7.2f}元 | {r['title'][:30]}{fl}")
            print()

        summary_path = report.build_summary(results, cfg)
        if not demo:
            db.save_run(f"批量×{len(entries)}", None, str(summary_path))
    finally:
        if collector is not None:
            collector.__exit__()

    ok = [r for r in results if r["rows"]]
    print(f">> 工作流完成: {len(ok)}/{len(results)} 个品有结果")
    print(f">> 汇总报告: {summary_path}")
    return summary_path, results
