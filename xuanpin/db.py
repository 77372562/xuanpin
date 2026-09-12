"""SQLite 存储层: 商品、供应商、价格历史、运行记录"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "xuanpin.db"

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
