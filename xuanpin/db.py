"""SQLite 存储层: 商品、供应商、价格历史、运行记录"""
import sqlite3

from xuanpin.paths import DATA_DIR

DB_PATH = DATA_DIR / "xuanpin.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_name TEXT UNIQUE,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT,
    supplier_id INTEGER REFERENCES suppliers(id),
    title TEXT,
    url TEXT UNIQUE,
    image_url TEXT,
    price_min REAL,
    price_max REAL,
    moq INTEGER,
    sales_text TEXT,
    weight_g REAL,
    first_seen TEXT DEFAULT (datetime('now','localtime')),
    last_seen TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    price_min REAL,
    price_max REAL,
    captured_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT,
    target_price REAL,
    report_path TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS my_supplies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    title TEXT,
    url TEXT,
    purchase_price REAL,
    supply_price REAL,
    retail_ref REAL,
    weight_g REAL,
    active INTEGER DEFAULT 1,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def save_products(keyword, items):
    """按 url 去重入库; 已存在的商品更新快照并追加价格历史"""
    con = connect()
    for it in items:
        supplier_id = None
        shop = it.get("shop_name")
        if shop:
            con.execute("INSERT OR IGNORE INTO suppliers(shop_name) VALUES (?)", (shop,))
            row = con.execute("SELECT id FROM suppliers WHERE shop_name=?", (shop,)).fetchone()
            supplier_id = row[0] if row else None
        row = con.execute("SELECT id FROM products WHERE url=?", (it["url"],)).fetchone()
        if row:
            pid = row[0]
            con.execute(
                """UPDATE products SET last_seen=datetime('now','localtime'), keyword=?,
                   supplier_id=COALESCE(?, supplier_id), price_min=?, price_max=?,
                   moq=?, sales_text=? WHERE id=?""",
                (keyword, supplier_id, it.get("price_min"), it.get("price_max"),
                 it.get("moq"), it.get("sales_text"), pid),
            )
        else:
            cur = con.execute(
                """INSERT INTO products(keyword, supplier_id, title, url, image_url,
                   price_min, price_max, moq, sales_text, weight_g)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (keyword, supplier_id, it.get("title"), it["url"], it.get("image_url"),
                 it.get("price_min"), it.get("price_max"), it.get("moq"),
                 it.get("sales_text"), it.get("weight_g")),
            )
            pid = cur.lastrowid
        con.execute(
            "INSERT INTO price_history(product_id, price_min, price_max) VALUES (?,?,?)",
            (pid, it.get("price_min"), it.get("price_max")),
        )
    con.commit()
    con.close()


def save_run(keyword, target_price, report_path):
    con = connect()
    con.execute(
        "INSERT INTO runs(keyword, target_price, report_path) VALUES (?,?,?)",
        (keyword, target_price, report_path),
    )
    con.commit()
    con.close()


# ---------- 供货管理(上下架建议) ----------

def add_supply(title, url, purchase_price, supply_price, retail_ref=None,
               weight_g=None, product_id=None, note=None):
    con = connect()
    cur = con.execute(
        """INSERT INTO my_supplies(title, url, purchase_price, supply_price,
           retail_ref, weight_g, product_id, note) VALUES (?,?,?,?,?,?,?,?)""",
        (title, url, purchase_price, supply_price, retail_ref, weight_g,
         product_id, note),
    )
    con.commit()
    sid = cur.lastrowid
    con.close()
    return sid


def set_supply_active(sid, active):
    con = connect()
    con.execute("UPDATE my_supplies SET active=? WHERE id=?", (1 if active else 0, sid))
    con.commit()
    con.close()


def delete_supply(sid):
    con = connect()
    con.execute("DELETE FROM my_supplies WHERE id=?", (sid,))
    con.commit()
    con.close()


def list_supplies(active_only=False):
    con = connect()
    sql = "SELECT * FROM my_supplies"
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY id DESC"
    cols = [c[0] for c in con.execute(sql).description]
    rows = [dict(zip(cols, r)) for r in con.execute(sql).fetchall()]
    con.close()
    return rows


def recent_products(limit=30):
    con = connect()
    cols = [c[0] for c in con.execute(
        "SELECT id,title,url,price_min,price_max,keyword FROM products "
        "ORDER BY id DESC LIMIT ?", (limit,)).description]
    rows = [dict(zip(cols, r)) for r in con.execute(
        "SELECT id,title,url,price_min,price_max,keyword FROM products "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    con.close()
    return rows
