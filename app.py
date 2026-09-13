"""选品助手 Web界面 —— 双击 选品界面.bat 启动, 浏览器里完成全部操作

功能: 候选清单管理 / 登录1688 / 一键批量工作流 / 单品快查 / 实时日志 / 报告下载
"""
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import yaml
from flask import Flask, jsonify, render_template_string, request, send_from_directory

from xuanpin import db
from xuanpin.batch import parse_keywords_file, run_batch, single_search
from xuanpin.paths import BASE_DIR, CONFIG_PATH, KEYWORDS_PATH, REPORTS_DIR

PORT = 8765

app = Flask(__name__)

JOB = {"running": False, "kind": None, "logs": [], "log_count": 0,
       "error": None, "summary": None, "cancel": False, "finished_at": None}


def load_cfg():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


class Tee:
    """线程内stdout重定向: 打印同时进控制台和界面日志"""
    def __init__(self, sink, original):
        self._sink = sink
        self._orig = original

    def write(self, s):
        try:
            self._orig.write(s)
            self._orig.flush()
        except Exception:
            pass
        if s and s.strip():
            self._sink.append(s.rstrip("\n"))
            if len(self._sink) > 3000:
                del self._sink[: len(self._sink) - 3000]
        return len(s)

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass


def start_job(kind, fn):
    with threading.Lock():
        if JOB["running"]:
            return False, "已有任务在运行中, 请先等它完成或点停止"
        JOB.update(running=True, kind=kind, logs=[], log_count=0,
                   error=None, summary=None, cancel=False, finished_at=None)

    def worker():
        original = sys.stdout
        sys.stdout = Tee(JOB["logs"], original)
        try:
            fn()
        except SystemExit as e:
            JOB["error"] = str(e) or "任务中止"
        except Exception as e:
            JOB["error"] = f"{type(e).__name__}: {e}"
            JOB["logs"].append(f"[异常] {JOB['error']}")
            traceback.print_exc()
        finally:
            sys.stdout = original
            JOB["running"] = False
            JOB["finished_at"] = datetime.now().strftime("%H:%M:%S")

    threading.Thread(target=worker, daemon=True).start()
    return True, None


# ---------------- API ----------------

@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/api/status")
def api_status():
    return jsonify(running=JOB["running"], kind=JOB["kind"], logs=JOB["logs"][-400:],
                   log_count=len(JOB["logs"]), error=JOB["error"], summary=JOB["summary"],
                   finished_at=JOB["finished_at"])


@app.post("/api/keywords")
def api_keywords_save():
    items = (request.get_json(force=True) or {}).get("items", [])
    cleaned = []
    for it in items:
        kw = str(it.get("keyword", "")).strip()
        if not kw:
            continue
        cleaned.append({"keyword": kw,
                        "retail": it.get("retail") or None,
                        "weight": it.get("weight") or None})
    if not cleaned:
        return jsonify(ok=False, error="清单为空"), 400
    lines = ["# 每日选品候选清单 (由界面自动维护, 也可手工编辑)",
             "# 格式: 关键词,Temu零售价,重量克"]
    for it in cleaned:
        parts = [it["keyword"]]
        parts.append(str(it["retail"]) if it["retail"] else "")
        if it["weight"]:
            parts.append(str(it["weight"]))
        lines.append(",".join(parts))
    KEYWORDS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonify(ok=True, count=len(cleaned))


@app.get("/api/keywords")
def api_keywords_load():
    try:
        entries = parse_keywords_file(KEYWORDS_PATH)
    except FileNotFoundError:
        return jsonify(items=[])
    return jsonify(items=[{k: e[k] for k in ("keyword", "retail", "weight")}
                          for e in entries])


@app.post("/api/login")
def api_login():
    def fn():
        from xuanpin.collector1688 import Collector
        with Collector() as c:
            if c.logged_in():
                print(">> 已处于登录状态, 无需重复登录")
                return
            c.login_flow()
    ok, err = start_job("登录1688", fn)
    return (jsonify(ok=True), 200) if ok else (jsonify(ok=False, error=err), 409)


@app.post("/api/batch")
def api_batch():
    demo = bool((request.get_json(silent=True) or {}).get("demo"))

    def fn():
        cfg = load_cfg()
        summary, _ = run_batch(cfg, list_file=str(KEYWORDS_PATH), demo=demo,
                               interactive=False, cancel_check=lambda: JOB["cancel"])
        JOB["summary"] = Path(summary).name
        if JOB["cancel"]:
            JOB["cancel"] = False

    ok, err = start_job("演示工作流" if demo else "批量工作流", fn)
    return (jsonify(ok=True), 200) if ok else (jsonify(ok=False, error=err), 409)


@app.post("/api/search")
def api_search():
    d = request.get_json(force=True) or {}
    kw = str(d.get("keyword", "")).strip()
    retail = d.get("retail")
    weight = d.get("weight")
    if not kw:
        return jsonify(ok=False, error="请填写关键词"), 400
    if not retail or float(retail) <= 0:
        return jsonify(ok=False, error="请填写Temu零售价(先去Temu看一眼同款)"), 400

    def fn():
        cfg = load_cfg()
        single_search(cfg, kw, float(retail),
                      float(weight) if weight else None)

    ok, err = start_job(f"单品:{kw}", fn)
    return (jsonify(ok=True), 200) if ok else (jsonify(ok=False, error=err), 409)


@app.post("/api/cancel")
def api_cancel():
    if not JOB["running"]:
        return jsonify(ok=False, error="当前没有运行中的任务")
    JOB["cancel"] = True
    return jsonify(ok=True)


@app.get("/api/reports")
def api_reports():
    REPORTS_DIR.mkdir(exist_ok=True)
    out = []
    for f in sorted(REPORTS_DIR.glob("*.xlsx"), key=lambda x: x.stat().st_mtime, reverse=True):
        st = f.stat()
        out.append({"name": f.name,
                    "size_kb": round(st.st_size / 1024, 1),
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
                    "is_summary": f.name.startswith("汇总")})
    return jsonify(items=out[:60])


@app.get("/reports/<path:name>")
def api_report_file(name):
    return send_from_directory(REPORTS_DIR, name, as_attachment=True)


@app.post("/api/open_folder")
def api_open_folder():
    REPORTS_DIR.mkdir(exist_ok=True)
    try:
        os.startfile(str(REPORTS_DIR))  # noqa: 仅Windows运行环境
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# ---------------- 供货管理(上下架建议) ----------------

@app.get("/api/supplies")
def api_supplies():
    from xuanpin import supply
    rows = supply.analyze(load_cfg())
    return jsonify(items=rows)


@app.post("/api/supplies")
def api_supplies_add():
    from xuanpin import db as _db
    d = request.get_json(force=True) or {}
    title = str(d.get("title", "")).strip()
    supply_price = d.get("supply_price")
    if not title:
        return jsonify(ok=False, error="请填商品标题/备注"), 400
    if not supply_price or float(supply_price) <= 0:
        return jsonify(ok=False, error="请填你的Temu供货价(核价/报价)"), 400
    purchase = d.get("purchase_price")
    if not purchase or float(purchase) <= 0:
        return jsonify(ok=False, error="请填拿货价(1688采购价)"), 400
    sid = _db.add_supply(
        title=title,
        url=str(d.get("url") or "").strip() or None,
        purchase_price=float(purchase),
        supply_price=float(supply_price),
        retail_ref=float(d["retail_ref"]) if d.get("retail_ref") else None,
        weight_g=float(d["weight_g"]) if d.get("weight_g") else None,
        product_id=int(d["product_id"]) if d.get("product_id") else None,
        note=d.get("note"),
    )
    return jsonify(ok=True, id=sid)


@app.post("/api/supplies/<int:sid>/toggle")
def api_supply_toggle(sid):
    from xuanpin import db as _db
    row = next((s for s in _db.list_supplies() if s["id"] == sid), None)
    if not row:
        return jsonify(ok=False, error="不存在"), 404
    _db.set_supply_active(sid, not row["active"])
    return jsonify(ok=True, active=not row["active"])


@app.delete("/api/supplies/<int:sid>")
def api_supply_delete(sid):
    from xuanpin import db as _db
    _db.delete_supply(sid)
    return jsonify(ok=True)


@app.get("/api/products/recent")
def api_products_recent():
    from xuanpin import db as _db
    return jsonify(items=_db.recent_products(30))


@app.post("/api/supply_report")
def api_supply_report():
    from xuanpin import supply
    path = supply.monitor_and_report(load_cfg())
    if path is None:
        return jsonify(ok=False, error="还没有登记任何在供商品"), 400
    return jsonify(ok=True, name=Path(path).name)


# ---------------- 前端页面 ----------------

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>选品助手 · xuanpin</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft YaHei", system-ui, sans-serif; background: #f2f4f8; color: #1f2430; padding: 20px; }
  .wrap { max-width: 980px; margin: 0 auto; }
  h1 { font-size: 22px; margin-bottom: 4px; }
  .sub { color: #7a8194; font-size: 13px; margin-bottom: 16px; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; vertical-align: middle; }
  .idle { background: #e2f5e9; color: #1a7f45; }
  .busy { background: #fdeaea; color: #c0392b; }
  .card { background: #fff; border-radius: 10px; padding: 16px 18px; margin-bottom: 14px; box-shadow: 0 1px 4px rgba(20,30,60,.08); }
  .card h2 { font-size: 15px; margin-bottom: 10px; color: #2c3550; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 6px 8px; border-bottom: 1px solid #eef0f4; text-align: left; }
  th { color: #7a8194; font-weight: 500; }
  input { border: 1px solid #d8dce6; border-radius: 6px; padding: 6px 8px; font-size: 13px; outline: none; }
  input:focus { border-color: #4a7dfc; }
  input.kw { width: 100%; } input.num { width: 90px; }
  button { border: 0; border-radius: 7px; padding: 8px 16px; font-size: 13px; cursor: pointer; background: #4a7dfc; color: #fff; }
  button:hover { filter: brightness(1.08); }
  button:disabled { background: #b9c4dd; cursor: not-allowed; }
  button.gray { background: #6b7488; }
  button.green { background: #1a9c5d; }
  button.red { background: #d05a4e; }
  button.ghost { background: #eef1f7; color: #42507a; }
  .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
  #log { background: #14181f; color: #c8d3e0; font: 12px/1.55 Consolas, monospace; border-radius: 8px; padding: 12px; height: 260px; overflow-y: auto; white-space: pre-wrap; }
  .err { color: #ff8a80; }
  .hint { font-size: 12px; color: #9aa1b2; margin-top: 8px; }
  a.rep { color: #2a5db0; text-decoration: none; }
  a.rep:hover { text-decoration: underline; }
  .tag { font-size: 11px; background: #fff3e0; color: #b26a00; border-radius: 4px; padding: 1px 6px; margin-left: 6px; }
  .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); background: #2c3550; color: #fff; padding: 9px 20px; border-radius: 8px; font-size: 13px; display: none; }
  .inline { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
</style>
</head>
<body>
<div class="wrap">
  <h1>选品助手 <span class="badge idle" id="badge">空闲</span></h1>
  <div class="sub">拼多多找词 → Temu查价 → 这里找货源算利润 → 报价</div>

  <div class="card">
    <h2>① 候选清单 <span class="hint" style="display:inline">（来自拼多多热销榜/直播间, Temu零售价请实地查看后填写）</span></h2>
    <table id="kwtable">
      <thead><tr><th style="width:52%">关键词</th><th>Temu零售价 ¥</th><th>重量 g(可选)</th><th></th></tr></thead>
      <tbody></tbody>
    </table>
    <div class="row" style="margin-top:10px">
      <button class="ghost" onclick="addRow()">+ 加一行</button>
      <button class="green" id="saveKw" onclick="saveKeywords()">保存清单</button>
      <button class="ghost" onclick="loadKeywords()">重置</button>
    </div>
  </div>

  <div class="card">
    <h2>② 开始跑</h2>
    <div class="row">
      <button class="green" id="runBatch" onclick="startJob('/api/batch')">▶ 跑批量工作流</button>
      <button class="ghost" id="runDemo" onclick="startJob('/api/batch', {demo:true})">演示模式(模拟数据)</button>
      <button id="loginBtn" onclick="startJob('/api/login')">登录/检查1688</button>
      <button class="red" id="cancelBtn" onclick="cancelJob()" disabled>■ 停止</button>
    </div>
    <div class="row" style="margin-top:12px; padding-top:12px; border-top:1px dashed #eef0f4">
      <b style="font-size:13px">单品快查:</b>
      <input class="kw" id="oneKw" placeholder="关键词, 例如 沥水篮" style="width:220px">
      <input class="num" id="oneRetail" type="number" step="0.1" placeholder="Temu零售价">
      <input class="num" id="oneWeight" type="number" step="10" placeholder="重量g">
      <button class="gray" id="runOne" onclick="runOne()">查这一个</button>
    </div>
    <div class="hint">跑之前先点一次「登录/检查1688」, 登录态长期有效; 弹出的浏览器里如有滑块请手动拖一下。</div>
  </div>

  <div class="card">
    <h2>③ 运行日志</h2>
    <div id="log">就绪。先维护上面的候选清单, 然后点「跑批量工作流」。</div>
  </div>

  <div class="card">
    <h2>④ 报告文件 <button class="ghost" style="float:right" onclick="openFolder()">打开报告文件夹</button></h2>
    <table id="reptable">
      <thead><tr><th>文件</th><th>大小</th><th>时间</th><th></th></tr></thead>
      <tbody></tbody>
    </table>
    <div class="hint" id="sumline"></div>
  </div>

  <div class="card">
    <h2>⑤ 供货管理（自动上下架建议）</h2>
    <div class="inline" style="margin-bottom:10px">
      <select id="impProd" style="max-width:340px; padding:6px; border:1px solid #d8dce6; border-radius:6px" onchange="fillFromProduct()"></select>
      <span class="hint">从最近采集选品自动填入, 或直接手填 ↓</span>
    </div>
    <div class="inline">
      <input id="supTitle" class="kw" placeholder="商品标题/备注" style="width:200px">
      <input id="supPurchase" class="num" type="number" step="0.1" placeholder="拿货价¥">
      <input id="supSupply" class="num" type="number" step="0.1" placeholder="供货价¥">
      <input id="supWeight" class="num" type="number" step="10" placeholder="重量g">
      <button class="green" onclick="addSupply()">登记在供</button>
      <button class="ghost" onclick="genSupplyReport()">生成供货监控报告</button>
    </div>
    <table style="margin-top:10px">
      <thead><tr><th>商品</th><th>拿货价→现价</th><th>供货价</th><th>当前利润</th><th>建议</th><th>操作</th></tr></thead>
      <tbody id="supbody"></tbody>
    </table>
    <div class="hint">登记你已报价/在供的商品后, 每次跑工作流会自动比对1688最新采购价: 涨价→建议下架(停供), 降价→建议加量, 断货→建议换源。Temu全托管的上下架由平台控制, 建议需到商家后台人工执行。</div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
let running = false, lastLogCount = -1;

function $(id){ return document.getElementById(id); }
function toast(msg){
  const t = $('toast'); t.textContent = msg; t.style.display = 'block';
  clearTimeout(t._h); t._h = setTimeout(()=>t.style.display='none', 2200);
}
function setButtons(){
  ['runBatch','runDemo','loginBtn','runOne','saveKw'].forEach(id => $(id).disabled = running);
  $('cancelBtn').disabled = !running;
  const b = $('badge');
  if (running){ b.textContent = '运行中: ' + (curKind||''); b.className = 'badge busy'; }
  else { b.textContent = '空闲'; b.className = 'badge idle'; }
}
let curKind = '';

function addRow(kw, retail, weight){
  const tb = $('kwtable').querySelector('tbody');
  const tr = document.createElement('tr');
  const c1 = document.createElement('td'), c2 = document.createElement('td'),
        c3 = document.createElement('td'), c4 = document.createElement('td');
  const i1 = document.createElement('input'); i1.className='kw'; i1.placeholder='关键词'; i1.value = kw||'';
  const i2 = document.createElement('input'); i2.className='num'; i2.type='number'; i2.step='0.1'; i2.placeholder='如15.9'; if(retail!=null) i2.value=retail;
  const i3 = document.createElement('input'); i3.className='num'; i3.type='number'; i3.step='10'; if(weight!=null) i3.value=weight;
  const del = document.createElement('button'); del.textContent='删'; del.className='ghost'; del.onclick=()=>tr.remove();
  c1.appendChild(i1); c2.appendChild(i2); c3.appendChild(i3); c4.appendChild(del);
  tr.append(c1,c2,c3,c4); tb.appendChild(tr);
}

async function loadKeywords(){
  const r = await fetch('/api/keywords'); const d = await r.json();
  $('kwtable').querySelector('tbody').innerHTML = '';
  (d.items||[]).forEach(e => addRow(e.keyword, e.retail, e.weight));
  if (!(d.items||[]).length) addRow();
}

async function saveKeywords(){
  const items = [];
  $('kwtable').querySelectorAll('tbody tr').forEach(tr => {
    const ins = tr.querySelectorAll('input');
    items.push({keyword: ins[0].value.trim(), retail: ins[1].value || null, weight: ins[2].value || null});
  });
  const r = await fetch('/api/keywords', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({items})});
  const d = await r.json();
  d.ok ? toast('清单已保存 (' + d.count + '个品)') : toast('保存失败: ' + d.error);
}

async function startJob(url, body){
  if (running) return;
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
  const d = await r.json();
  if (d.ok){ running = true; lastLogCount = -1; $('log').textContent=''; setButtons(); toast('任务已启动'); }
  else toast(d.error);
}

async function runOne(){
  const kw = $('oneKw').value.trim(), rt = $('oneRetail').value, wg = $('oneWeight').value;
  if (!kw) return toast('请填关键词');
  if (!rt) return toast('请填Temu零售价(先去Temu看一眼同款)');
  await startJob('/api/search', {keyword: kw, retail: rt, weight: wg});
}

async function cancelJob(){
  await fetch('/api/cancel', {method:'POST'});
  toast('停止指令已发送, 当前关键词跑完即止');
}

async function openFolder(){ await fetch('/api/open_folder', {method:'POST'}); }

// ---------- 供货管理 ----------
let recentProds = [];

async function loadRecent(){
  const r = await fetch('/api/products/recent'); const d = await r.json();
  recentProds = d.items || [];
  const sel = $('impProd');
  sel.innerHTML = '<option value="">— 从最近采集导入 —</option>';
  recentProds.forEach((p, i) => {
    const o = document.createElement('option');
    o.value = i;
    o.textContent = (p.title||'').slice(0,30) + ' [¥' + (p.price_min??'?') + '~' + (p.price_max??'?') + ']';
    sel.appendChild(o);
  });
}

function fillFromProduct(){
  const i = $('impProd').value;
  if (i === '') return;
  const p = recentProds[i];
  $('supTitle').value = (p.title||'').slice(0,60);
  $('supPurchase').value = p.price_min ?? '';
  $('supWeight').value = '';
  $('impProd')._pid = p.id;
  $('impProd')._url = p.url || '';
}

async function addSupply(){
  const body = {
    title: $('supTitle').value.trim(),
    purchase_price: $('supPurchase').value,
    supply_price: $('supSupply').value,
    weight_g: $('supWeight').value || null,
    product_id: $('impProd')._pid || null,
    url: $('impProd')._url || '',
  };
  const r = await fetch('/api/supplies', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  if (d.ok){ toast('已登记在供商品 ✓'); $('supTitle').value=''; $('supPurchase').value=''; $('supSupply').value=''; loadSupplies(); }
  else toast(d.error);
}

const SUG_COLOR = {'建议下架(停供)':'#c0392b','建议换源/停供':'#c0392b','谨慎持有':'#b26a00','信息过期':'#7030a0','建议加量/续报':'#1a7f45','继续供货':'#1a7f45','已停供':'#9aa1b2','待完善':'#9aa1b2'};

async function loadSupplies(){
  const r = await fetch('/api/supplies'); const d = await r.json();
  const tb = $('supbody'); tb.innerHTML = '';
  (d.items||[]).forEach(s => {
    const tr = document.createElement('tr');
    function td(txt){ const t=document.createElement('td'); t.textContent=txt; return t; }
    tr.appendChild(td((s.title||'').slice(0,24)));
    tr.appendChild(td('¥' + (s.purchase_price??'?') + ' → ¥' + (s.purchase_now??'?')));
    tr.appendChild(td('¥' + (s.supply_price??'?')));
    const pt = td((s.profit_now>=0?'+':'') + (s.profit_now??'?') + ' (' + ((s.ratio_now||0)*100).toFixed(1) + '%)');
    if (s.profit_now < 0) pt.style.color = '#c0392b';
    tr.appendChild(pt);
    const sug = td(s.suggestion + (s.reasons? '：'+s.reasons : ''));
    sug.style.color = SUG_COLOR[s.suggestion] || '#1f2430';
    if (s.active) sug.style.fontWeight = '600';
    tr.appendChild(sug);
    const op = document.createElement('td');
    const tg = document.createElement('button'); tg.className='ghost'; tg.textContent = s.active?'停供':'启用';
    tg.onclick = async()=>{ await fetch('/api/supplies/'+s.id+'/toggle', {method:'POST'}); loadSupplies(); };
    const dl = document.createElement('button'); dl.className='ghost'; dl.textContent='删'; dl.style.marginLeft='6px';
    dl.onclick = async()=>{ await fetch('/api/supplies/'+s.id, {method:'DELETE'}); loadSupplies(); };
    op.appendChild(tg); op.appendChild(dl); tr.appendChild(op);
    tb.appendChild(tr);
  });
  if (!(d.items||[]).length) tb.innerHTML = '<tr><td colspan=6 style="color:#9aa1b2">还没有登记在供商品 — 跑完选品后, 把决定报价的品登记到这里</td></tr>';
}

async function genSupplyReport(){
  const r = await fetch('/api/supply_report', {method:'POST'});
  const d = await r.json();
  if (d.ok){ toast('供货监控报告已生成'); refreshReports(); } else toast(d.error);
}

function esc(s){ const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

async function refreshReports(){
  const r = await fetch('/api/reports'); const d = await r.json();
  const tb = $('reptable').querySelector('tbody'); tb.innerHTML = '';
  (d.items||[]).forEach(f => {
    const tr = document.createElement('tr');
    const a = document.createElement('a'); a.className='rep'; a.href='/reports/'+encodeURIComponent(f.name); a.textContent=f.name;
    if (f.is_summary){ const s=document.createElement('span'); s.className='tag'; s.textContent='汇总'; a.appendChild(s); }
    const t1=document.createElement('td'); t1.appendChild(a);
    const t2=document.createElement('td'); t2.textContent=f.size_kb+' KB';
    const t3=document.createElement('td'); t3.textContent=f.mtime;
    const t4=document.createElement('td'); const dl=document.createElement('a'); dl.className='rep'; dl.href='/reports/'+encodeURIComponent(f.name); dl.textContent='下载'; t4.appendChild(dl);
    tr.append(t1,t2,t3,t4); tb.appendChild(tr);
  });
  if (!(d.items||[]).length){ tb.innerHTML = '<tr><td colspan=4 style="color:#9aa1b2">还没有报告</td></tr>'; }
}

async function poll(){
  try {
    const r = await fetch('/api/status'); const s = await r.json();
    curKind = s.kind;
    if (s.log_count !== lastLogCount){
      lastLogCount = s.log_count;
      const el = $('log');
      el.innerHTML = s.logs.map(esc).join('\\n');
      el.scrollTop = el.scrollHeight;
    }
    if (running && !s.running){
      running = false; setButtons(); refreshReports(); loadSupplies();
      if (s.error){ $('log').innerHTML += '\\n<span class=err>[失败] ' + esc(s.error) + '</span>'; toast('任务失败: ' + s.error); }
      else toast('任务完成 ✓');
    }
    if (s.summary) $('sumline').textContent = '最近汇总: ' + s.summary + ' (下方表格可下载)';
    setButtons();
  } catch(e) {}
}

loadKeywords(); refreshReports(); loadSupplies(); loadRecent(); setButtons();
setInterval(poll, 1200); setInterval(refreshReports, 6000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    import socket
    import webbrowser

    # 打包成exe(--noconsole)后没有控制台, 把输出转存日志便于排障
    if getattr(sys, "frozen", False):
        LOG_DIR = BASE_DIR / "data"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(LOG_DIR / "ui.log", "a", encoding="utf-8", buffering=1)

    REPORTS_DIR.mkdir(exist_ok=True)
    url = f"http://127.0.0.1:{PORT}"

    s = socket.socket()
    s.settimeout(0.5)
    port_busy = s.connect_ex(("127.0.0.1", PORT)) == 0
    s.close()

    if port_busy:
        print(f"界面已经在运行, 直接打开: {url}")
        webbrowser.open(url)
        sys.exit(0)

    def server_ready():
        for _ in range(50):
            try:
                c = socket.socket()
                c.settimeout(0.3)
                if c.connect_ex(("127.0.0.1", PORT)) == 0:
                    return True
            finally:
                c.close()
            time.sleep(0.2)
        return False

    if "--server" in sys.argv:
        print(f"选品助手界面: {url}  (保持本窗口开着, 关闭即退出)")
        app.run(host="127.0.0.1", port=PORT, threaded=True)
    else:
        # 桌面窗口模式: 内嵌WebView, 关窗即退出; 不可用时回退浏览器
        threading.Thread(target=lambda: app.run(host="127.0.0.1", port=PORT, threaded=True),
                         daemon=True).start()
        if server_ready():
            try:
                import webview
                webview.create_window("选品助手 · xuanpin", url, width=1120, height=920,
                                      min_size=(900, 700))
                webview.start()
                sys.exit(0)
            except Exception:
                print("桌面窗口不可用(可能缺WebView2), 改用浏览器模式")
        webbrowser.open(url)
        threading.Event().wait()  # 浏览器模式下服务保活
