#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================================
  CAFE POS  -  программа для кафе и ресторана
==========================================================================
Возможности:
  * Меню: категории, блюда, цены, стоп-лист
  * Залы -> кабинки (VIP-комнаты) -> столы, редактирование из интерфейса
  * Официант берёт заказ со СВОЕГО ТЕЛЕФОНА (Wi-Fi, браузер) прямо у стола
  * Заказ сразу уходит на КУХНЮ (экран кухни + авто-печать тикета)
  * Статусы: принят -> готовится -> готов -> подан
  * Касса: расчёт, счёт, сдача, закрытие заказа, выручка за день
  * Настройка принтера: сетевой (IP:9100), Windows-принтер, или "заглушка"
  * Проверка связи с принтером (тестовая печать)

ЗАПУСК:
  python3 server.py                 # сервер поднимется на порту 8080
  python3 server.py --port 8000     # другой порт

Далее:
  Компьютер/касса/кухня : http://localhost:8080
  Телефон официанта      : http://IP-КОМПЬЮТЕРА:8080/waiter
  Экран кухни            : http://IP-КОМПЬЮТЕРА:8080/kitchen
  Всё на одном экране    : http://IP-КОМПЬЮТЕРА:8080/manager
  Касса                  : http://IP-КОМПЬЮТЕРА:8080/cashier
Свой IP узнать: ipconfig (Windows) или ifconfig / ip a (Linux/Mac)

Пароль по умолчанию: 1234   (можно поменять в Настройках)
==========================================================================
"""

import json, os, sqlite3, sys, urllib.parse, urllib.request, urllib.error
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "cafe.db")
DIRECTORY = os.path.join(BASE, "files")          # прикреплённые документы
os.makedirs(DIRECTORY, exist_ok=True)

PORT = 8080
for i, a in enumerate(sys.argv):
    if a == "--port" and i + 1 < len(sys.argv):
        PORT = int(sys.argv[i + 1])

# --------------------------------------------------------------------------
#  База данных
# --------------------------------------------------------------------------
def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        sort INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1);

    CREATE TABLE IF NOT EXISTS dishes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        price REAL NOT NULL DEFAULT 0,
        description TEXT DEFAULT '',
        stop INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        station TEXT DEFAULT 'Кухня',
        sort INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS halls(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        sort INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS rooms(          -- кабинки / VIP-комнаты
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hall_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        seats INTEGER DEFAULT 4,
        sort INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS tables_(        -- столы
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id INTEGER,
        name TEXT NOT NULL,
        seats INTEGER DEFAULT 4,
        status TEXT DEFAULT 'free',            -- free | busy | bill
        sort INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS waiters(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        pin TEXT DEFAULT '0000',
        active INTEGER DEFAULT 1);

    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number INTEGER,
        table_id INTEGER,
        waiter_id INTEGER,
        guests INTEGER DEFAULT 1,
        status TEXT DEFAULT 'new',             -- new|cooking|ready|served|paid|cancel
        kitchen_status TEXT DEFAULT 'none',    -- none|sent|accepted|cooking|ready|done
        total REAL DEFAULT 0,
        service REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        paid REAL DEFAULT 0,
        payment TEXT DEFAULT '',
        note TEXT DEFAULT '',
        printed INTEGER DEFAULT 0,
        created TEXT,
        sent_at TEXT,
        closed_at TEXT);

    CREATE TABLE IF NOT EXISTS order_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        dish_id INTEGER,
        name TEXT NOT NULL,
        price REAL NOT NULL DEFAULT 0,
        qty REAL NOT NULL DEFAULT 1,
        station TEXT DEFAULT 'Кухня',
        note TEXT DEFAULT '',
        kitchen_status TEXT DEFAULT 'new',     -- new|sent|cooking|ready|served
        printed INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER, amount REAL, method TEXT,
        cashier TEXT DEFAULT '', created TEXT);

    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT);

    CREATE TABLE IF NOT EXISTS print_jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT, order_id INTEGER, printer TEXT,
        text TEXT, status TEXT DEFAULT 'new',
        error TEXT DEFAULT '', created TEXT, done_at TEXT);
    """)
    c.commit()

    def setting(k, v):
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))

    default_settings = [
        ("cafe_name", "Моё кафе"),
        ("address", ""),
        ("phone", ""),
        ("currency", "сум"),
        ("service_percent", "0"),
        ("password", "1234"),
        ("printer_mode", "none"),        # none | network | command | windows
        ("printer_ip", "192.168.1.50"),
        ("printer_port", "9100"),
        ("printer_command", "lp"),
        ("printer_windows", "XP-80C"),
        ("printer_copies", "1"),
        ("receipt_width", "42"),
        ("auto_print_kitchen", "1"),
        ("auto_print_receipt", "0"),
        ("waiter_confirm", "1"),
        ("tax_percent", "0"),
        ("footer_text", "Спасибо за визит!"),
    ]
    for k, v in default_settings:
        setting(k, v)
    c.commit()

    # если база пустая - заливаем демо-данные
    if c.execute("SELECT COUNT(*) n FROM categories").fetchone()["n"] == 0:
        cats = [("Горячие блюда", 1), ("Салаты и закуски", 2), ("Супы", 3),
                ("Десерты", 4), ("Напитки", 5)]
        for name, s in cats:
            c.execute("INSERT INTO categories(name,sort) VALUES(?,?)", (name, s))
        cid = {r["name"]: r["id"] for r in c.execute("SELECT id,name FROM categories")}
        dishes = [
            (cid["Горячие блюда"], "Плов", 45000, "Рис, баранина, морковь", "Кухня"),
            (cid["Горячие блюда"], "Шашлык из баранины", 60000, "2 шампура", "Мангал"),
            (cid["Горячие блюда"], "Лагман", 40000, "Домашняя лапша", "Кухня"),
            (cid["Горячие блюда"], "Куриное филе на гриле", 48000, "с овощами", "Кухня"),
            (cid["Салаты и закуски"], "Ачичук", 18000, "Помидор, лук, перец", "Холодный цех"),
            (cid["Салаты и закуски"], "Цезарь с курицей", 38000, "", "Холодный цех"),
            (cid["Салаты и закуски"], "Сырная тарелка", 52000, "", "Холодный цех"),
            (cid["Супы"], "Шурпа", 32000, "", "Кухня"),
            (cid["Супы"], "Крем-суп грибной", 28000, "", "Кухня"),
            (cid["Десерты"], "Чизкейк", 30000, "", "Кондитер"),
            (cid["Десерты"], "Мороженое (3 шарика)", 22000, "", "Кондитер"),
            (cid["Напитки"], "Чай с чайником", 12000, "", "Бар"),
            (cid["Напитки"], "Кофе американо", 18000, "", "Бар"),
            (cid["Напитки"], "Кола 0,5", 15000, "", "Бар"),
            (cid["Напитки"], "Свежевыжатый сок", 25000, "", "Бар"),
        ]
        for c_, n, p, d, st in dishes:
            c.execute("INSERT INTO dishes(category_id,name,price,description,station) VALUES(?,?,?,?,?)",
                      (c_, n, p, d, st))

        c.execute("INSERT INTO halls(name,sort) VALUES('Основной зал',1)")
        h1 = c.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        c.execute("INSERT INTO halls(name,sort) VALUES('Летняя терраса',2)")
        h2 = c.execute("SELECT last_insert_rowid() id").fetchone()["id"]

        rooms = [(h1, "Зал у окна", 0), (h1, "Зал у бара", 0),
                 (h1, "VIP-кабинка №1", 8), (h1, "VIP-кабинка №2", 12),
                 (h2, "Терраса общая", 0)]
        for h, n, s in rooms:
            c.execute("INSERT INTO rooms(hall_id,name,seats) VALUES(?,?,?)", (h, n, s))
        rows = c.execute("SELECT id,seats FROM rooms").fetchall()
        for r in rows:
            if r["seats"]:
                c.execute("INSERT INTO tables_(room_id,name,seats) VALUES(?,?,?)",
                          (r["id"], "Стол " + str(r["id"]) + "-1", r["seats"]))
            else:
                for k in range(1, 5):
                    c.execute("INSERT INTO tables_(room_id,name,seats) VALUES(?,?,?)",
                              (r["id"], "Стол " + str(k), 4))
        for n, pin in [("Азиз", "1111"), ("Дилшод", "2222"), ("Мадина", "3333")]:
            c.execute("INSERT INTO waiters(name,pin) VALUES(?,?)", (n, pin))

        c.execute("""INSERT INTO orders(number,table_id,waiter_id,guests,status,kitchen_status,
                     total,created,sent_at) VALUES(1,1,1,2,'new','sent',45000,?,?)""",
                  (now(), now()))
        oid = c.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        c.execute("""INSERT INTO order_items(order_id,dish_id,name,price,qty,station,kitchen_status)
                     VALUES(?,1,'Плов',45000,1,'Кухня','sent')""", (oid,))
        c.execute("UPDATE tables_ SET status='busy' WHERE id=1")
        c.commit()
    c.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def today():
    return date.today().isoformat()

def s_get(key, default=""):
    c = db()
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return r["value"] if r else default

def s_set(key, value):
    c = db()
    c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    c.commit(); c.close()

def s_all():
    c = db()
    d = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
    c.close()
    return d


# --------------------------------------------------------------------------
#  Печать
# --------------------------------------------------------------------------
def printer_mode():
    return s_get("printer_mode", "none")

def _ljust(s, w):
    s = str(s)
    return s if len(s) >= w else s + " " * (w - len(s))

def _rjust(s, w):
    s = str(s)
    return s if len(s) >= w else " " * (w - len(s)) + s

def _cut(s, w):
    s = str(s)
    return s if len(s) <= w else s[:w - 1] + "."

def _wrap(text, w):
    out, line = [], ""
    for word in str(text).split():
        if len(line) + len(word) + (1 if line else 0) <= w:
            line = (line + " " + word).strip()
        else:
            if line: out.append(line)
            line = word
    if line: out.append(line)
    return out or [""]

def build_receipt_text(order_id, kind="kitchen"):
    """kind: kitchen (тикет на кухню) | receipt (чек гостю) | prebill (счёт)"""
    c = db()
    o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o:
        c.close(); return "Заказ не найден"
    items = c.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    t = c.execute("SELECT t.name, r.name rn, h.name hn FROM tables_ t "
                  "LEFT JOIN rooms r ON r.id=t.room_id LEFT JOIN halls h ON h.id=r.hall_id "
                  "WHERE t.id=?", (o["table_id"],)).fetchone()
    w = c.execute("SELECT name FROM waiters WHERE id=?", (o["waiter_id"],)).fetchone()
    c.close()

    W = max(28, min(64, int(s_get("receipt_width", "42"))))
    cur = s_get("currency", "сум")
    L = []
    L.append("=" * W)
    if kind == "kitchen":
        L.append(_center("КУХНЯ / ЗАКАЗ №%s" % o["number"], W))
        L.append(_center(datetime.now().strftime("%d.%m.%Y  %H:%M"), W))
    else:
        L.append(_center(s_get("cafe_name", "Кафе"), W))
        if s_get("address"): L.append(_center(s_get("address"), W))
        if s_get("phone"):   L.append(_center("тел. " + s_get("phone"), W))
        L.append(_center(datetime.now().strftime("%d.%m.%Y  %H:%M"), W))
    L.append("=" * W)
    if kind == "kitchen":
        where = " ".join(x for x in [(t["hn"] if t else ""), (t["rn"] if t else ""),
                                     (t["name"] if t else "")] if x)
        L.append("Место : " + where)
        L.append("Официант: " + (w["name"] if w else "-"))
        L.append("Гостей: " + str(o["guests"]))
        if o["note"]:
            for ln in _wrap("Комментарий: " + o["note"], W): L.append(ln)
        L.append("-" * W)
        for it in items:
            if (it["station"] or "Кухня") not in ("", "Кухня") and kind == "kitchen":
                pass
            L.append(_cut(it["name"], W - 6))
            L.append("  %s x %s" % (_fmt_qty(it["qty"]), _fmt_qty(it["qty"])))
            if it["note"]:
                for ln in _wrap("  >> " + it["note"], W): L.append(ln)
        L.append("-" * W)
        L.append(_center("ГОТОВИТЬ", W))
    else:
        head = "ЧЕК" if kind == "receipt" else "СЧЁТ (предварительный)"
        L.append(_center(head + "  №" + str(o["number"]), W))
        where = " ".join(x for x in [(t["hn"] if t else ""), (t["rn"] if t else ""),
                                     (t["name"] if t else "")] if x)
        L.append("Зал/стол: " + where)
        L.append("Официант: " + (w["name"] if w else "-"))
        L.append("-" * W)
        L.append(_ljust("Наимен.", W - 20) + _rjust("Цена", 9) + _rjust("Кол-во", 6) + _rjust("Сумма", 10))
        for it in items:
            L.append(_ljust(_cut(it["name"], W - 25), W - 25) +
                     _rjust(_fmt_money(it["price"]), 9) +
                     _rjust(_fmt_qty(it["qty"]), 6) +
                     _rjust(_fmt_money(it["price"] * it["qty"]), 10))
        L.append("-" * W)
        L.append(_rjust("Итого:", W - 14) + _rjust(_fmt_money(o["total"]) + " " + cur, 14))
        if o["service"]:
            L.append(_rjust("Обслуживание:", W - 14) + _rjust(_fmt_money(o["service"]), 14))
        if o["discount"]:
            L.append(_rjust("Скидка:", W - 14) + _rjust("-" + _fmt_money(o["discount"]), 14))
        grand = (o["total"] or 0)
        L.append(_rjust("К ОПЛАТЕ:", W - 14) + _rjust(_fmt_money(grand) + " " + cur, 14))
        if kind == "receipt" and o["paid"]:
            L.append(_rjust("Оплачено (" + (o["payment"] or "") + "):", W - 14) +
                     _rjust(_fmt_money(o["paid"]), 14))
        L.append("-" * W)
        if s_get("footer_text"):
            for ln in _wrap(s_get("footer_text"), W): L.append(_center(ln, W))
    L.append("=" * W)
    L.append("")
    L.append("")
    L.append("")
    return "\n".join(L)

def _center(s, w):
    s = str(s)
    if len(s) >= w: return s[:w]
    pad = (w - len(s)) // 2
    return " " * pad + s

def _fmt_money(v):
    try:
        v = float(v)
    except Exception:
        return "0"
    if abs(v - round(v)) < 0.005:
        return "{:,.0f}".format(round(v)).replace(",", " ")
    return "{:,.2f}".format(v).replace(",", " ")

def _fmt_qty(v):
    try: v = float(v)
    except Exception: return "1"
    return str(int(v)) if abs(v - round(v)) < 0.005 else ("%.2f" % v)

def send_to_printer(text, printer=None, kind="ticket"):
    """Отправка текста на принтер. Возвращает (ok, сообщение)."""
    mode = printer_mode()
    ip = s_get("printer_ip"); port = int(s_get("printer_port", "9100") or 9100)
    if mode == "none":
        return False, "Принтер не настроен (Настройки → Принтер). Текст сохранён в журнале печати."
    try:
        if mode == "network":
            import socket
            s = socket.create_connection((ip, port), timeout=5)
            payload = b"\x1b@" + text.encode("cp866", errors="replace") + b"\n\n\n\x1dVA\x03"
            s.sendall(payload); s.close()
            return True, "Отправлено на сетевой принтер %s:%s" % (ip, port)
        elif mode == "windows":
            import subprocess, tempfile
            name = s_get("printer_windows")
            f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="cp866",
                                            errors="replace")
            f.write(text); f.close()
            subprocess.run(["print", "/D:" + name, f.name], shell=True, timeout=20)
            return True, "Отправлено на принтер Windows: " + name
        elif mode == "command":
            import subprocess, tempfile
            cmd = s_get("printer_command", "lp")
            f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
            f.write(text); f.close()
            p = subprocess.run("%s %s" % (cmd, f.name), shell=True, timeout=25,
                               capture_output=True, text=True)
            if p.returncode != 0:
                return False, "Команда вернула ошибку: " + (p.stderr or "").strip()[:200]
            return True, "Отправлено командой: " + cmd
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    return False, "Неизвестный режим печати"


def queue_print(kind, order_id, printer="kitchen"):
    text = build_receipt_text(order_id, kind)
    ok, msg = send_to_printer(text, printer, kind)
    c = db()
    c.execute("INSERT INTO print_jobs(kind,order_id,printer,text,status,error,created,done_at) "
              "VALUES(?,?,?,?,?,?,?,?)",
              (kind, order_id, s_get("printer_mode"), text,
               "done" if ok else "error", "" if ok else msg, now(),
               now() if ok else ""))
    c.commit(); c.close()
    return ok, msg


# --------------------------------------------------------------------------
#  Расчёт заказа
# --------------------------------------------------------------------------
def recalc(order_id):
    c = db()
    items = c.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    total = sum((i["price"] or 0) * (i["qty"] or 0) for i in items)
    svc_p = float(s_get("service_percent", "0") or 0)
    service = round(total * svc_p / 100.0, 2)
    o = c.execute("SELECT discount FROM orders WHERE id=?", (order_id,)).fetchone()
    disc = (o["discount"] if o else 0) or 0
    c.execute("UPDATE orders SET total=?, service=? WHERE id=?", (total + service - disc,
                                                                  service, order_id))
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    c.commit(); c.close()
    return dict(row)

def kitchen_state(order_id):
    c = db()
    rows = c.execute("SELECT kitchen_status FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    c.close()
    st = [r["kitchen_status"] for r in rows]
    if not st: return "none"
    if any(s == "new" for s in st):     return "new"
    if all(s == "served" for s in st):  return "done"
    if all(s in ("ready", "served") for s in st): return "ready"
    if any(s == "cooking" for s in st): return "cooking"
    if all(s == "sent" for s in st):    return "sent"
    return "cooking"


# --------------------------------------------------------------------------
#  HTML
# --------------------------------------------------------------------------
def esc(t):
    return (str(t) if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f2f4f7;color:#16233a}
a{color:inherit;text-decoration:none}
header{background:#16233a;color:#fff;padding:10px 14px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
header .logo{font-weight:700;font-size:18px}
header .sp{flex:1}
nav a{display:inline-block;padding:8px 12px;border-radius:8px;background:rgba(255,255,255,.12);margin:2px;font-size:14px}
nav a.on{background:#ffb703;color:#16233a;font-weight:600}
.wrap{padding:14px;max-width:1400px;margin:0 auto}
.card{background:#fff;border-radius:12px;padding:14px;box-shadow:0 1px 3px rgba(16,24,40,.08);margin-bottom:14px}
h2{margin:0 0 10px;font-size:18px}
h3{margin:14px 0 8px;font-size:15px;color:#475467}
.row{display:flex;gap:10px;flex-wrap:wrap}
.grow{flex:1}
button,.btn{cursor:pointer;border:0;border-radius:9px;padding:10px 14px;font-size:15px;background:#e4e7ec;color:#16233a;font-family:inherit}
button:hover,.btn:hover{filter:brightness(.96)}
.btn-p{background:#1d7a46;color:#fff}
.btn-b{background:#175cd3;color:#fff}
.btn-r{background:#d92d20;color:#fff}
.btn-y{background:#ffb703;color:#16233a}
.btn-lg{font-size:18px;padding:14px 18px;width:100%}
input,select,textarea{font-family:inherit;font-size:15px;padding:9px 10px;border:1px solid #d0d5dd;border-radius:8px;background:#fff;width:100%}
label{font-size:13px;color:#475467;display:block;margin:6px 0 3px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:8px;text-align:left;border-bottom:1px solid #eaecf0}
th{color:#475467;font-weight:600;font-size:13px}
.pill{display:inline-block;padding:3px 9px;border-radius:20px;font-size:12px;font-weight:600}
.p-new{background:#fee4e2;color:#b42318}
.p-sent{background:#fef0c7;color:#b54708}
.p-cooking{background:#d1e9ff;color:#175cd3}
.p-ready{background:#d1fadf;color:#05603a}
.p-served{background:#e4e7ec;color:#475467}
.p-paid{background:#d1fadf;color:#05603a}
.p-free{background:#e4e7ec;color:#475467}
.p-busy{background:#fef0c7;color:#b54708}
.p-bill{background:#d1e9ff;color:#175cd3}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.tile{background:#fff;border:2px solid #eaecf0;border-radius:12px;padding:12px;text-align:left;cursor:pointer;min-height:92px}
.tile b{display:block;font-size:16px;margin-bottom:6px}
.tile.free{border-color:#d0d5dd}
.tile.busy{border-color:#f79009;background:#fffcf5}
.tile.bill{border-color:#175cd3;background:#f5faff}
.kb{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.kb .kcard{background:#fff;border-left:6px solid #f79009;border-radius:10px;padding:10px}
.kb .kcard.cooking{border-color:#175cd3}
.kb .kcard.ready{border-color:#12b76a;background:#f6fef9}
.grid2{display:grid;grid-template-columns:1.15fr .85fr;gap:14px;align-items:start}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.cat{font-size:13px;color:#667085;text-transform:uppercase;letter-spacing:.04em;margin:12px 0 6px}
.dishes{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.dish{background:#fff;border:1px solid #eaecf0;border-radius:10px;padding:10px;cursor:pointer;text-align:left}
.dish:hover{border-color:#175cd3}
.dish .n{font-weight:600;font-size:14px}
.dish .p{color:#1d7a46;font-weight:600;margin-top:4px}
.dish.stop{opacity:.45;text-decoration:line-through}
.line{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px dashed #eaecf0}
.line .nm{flex:1}
.line .q{display:flex;align-items:center;gap:6px}
.line .q button{width:34px;height:34px;padding:0;font-size:18px;font-weight:700}
.sum{display:flex;justify-content:space-between;padding:6px 0;font-size:15px}
.sum.big{font-size:22px;font-weight:700;border-top:2px solid #16233a;margin-top:8px;padding-top:8px}
.sticky{position:sticky;top:12px}
.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:20px;background:#05603a;color:#fff;
 padding:12px 20px;border-radius:10px;font-size:15px;z-index:99;box-shadow:0 4px 14px rgba(0,0,0,.25)}
.toast.err{background:#b42318}
.login{max-width:380px;margin:60px auto}
.hint{font-size:13px;color:#667085}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;white-space:pre;background:#0b1220;color:#d6e2f5;padding:10px;border-radius:8px;overflow:auto;max-height:260px}
"""

def page(title, body, active="", extra_head=""):
    nav = [("waiter", "/waiter", "Заказ (официант)"), ("kitchen", "/kitchen", "Кухня"),
           ("cashier", "/cashier", "Касса"), ("manager", "/manager", "Меню и залы"),
           ("reports", "/reports", "Отчёты"), ("settings", "/settings", "Настройки")]
    links = "".join('<a href="%s"%s>%s</a>' % (u, ' class="on"' if k == active else "", esc(t))
                    for k, u, t in nav)
    return """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style>%s</head><body>
<header><span class="logo">%s</span><span class="sp"></span><nav>%s</nav></header>
<div class="wrap">%s</div>
<script>
function post(url,data){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify(data||{})}).then(function(r){return r.json()}).catch(function(e){return {ok:false,error:String(e)}});}
function toast(msg,bad){var d=document.createElement('div');d.className='toast'+(bad?' err':'');d.textContent=msg;
 document.body.appendChild(d);setTimeout(function(){d.remove()},3000);}
</script></body></html>""" % (esc(title), CSS, extra_head, esc(s_get("cafe_name", "Кафе")), links, body)


def page_login(msg="", back="/waiter", who="Официант"):
    body = """<div class="card login"><h2>%s</h2>
<form method="GET" action="%s">
<label>PIN-код официанта</label><input name="pin" type="password" inputmode="numeric" autofocus>
<label>или общий пароль</label><input name="password" type="password">
<p class="hint">PIN официантов задан в разделе «Меню и залы». Общий пароль по умолчанию 1234.</p>
<p style="color:#b42318">%s</p>
<button class="btn-p btn-lg" type="submit">Войти</button></form></div>""" % (
        esc(who), esc(back), esc(msg))
    return page("Вход", body)


# --------------------------------------------------------------------------
#  Обработчики
# --------------------------------------------------------------------------
class API:
    # ---------- auth ----------
    @staticmethod
    def check_auth(q):
        pin = (q.get("pin", [""])[0] or "").strip()
        pwd = (q.get("password", [""])[0] or "").strip()
        if pin:
            c = db()
            r = c.execute("SELECT * FROM waiters WHERE pin=? AND active=1", (pin,)).fetchone()
            c.close()
            if r: return dict(r)
        if pwd and pwd == s_get("password"):
            return {"id": 0, "name": "Администратор"}
        return None


def h_waiter(handler, q):
    waiter = API.check_auth(q)
    if not waiter:
        return handler.html(page_login("Неверный PIN или пароль", "/waiter"))
    return handler.html(render_waiter(waiter))


def h_waiter_order(handler, q):
    waiter = API.check_auth(q)
    tid = int(q.get("table", ["1"])[0])
    if not waiter:
        return handler.html(page_login("Неверный PIN или пароль",
                                       "/waiter/order?table=%d" % tid))
    return handler.html(render_order(waiter, tid))


def render_waiter(waiter):
    c = db()
    halls = c.execute("SELECT * FROM halls ORDER BY sort,id").fetchall()
    rooms = c.execute("SELECT * FROM rooms ORDER BY sort,id").fetchall()
    tables = c.execute("SELECT * FROM tables_ ORDER BY sort,id").fetchall()
    orders = c.execute("""SELECT t.id table_id,t.name tname,t.status tstatus,r.name rname,
                          o.id oid,o.number,o.total,o.status ostatus,o.kitchen_status
                          FROM tables_ t LEFT JOIN rooms r ON r.id=t.room_id
                          LEFT JOIN orders o ON o.table_id=t.id AND o.status NOT IN ('paid','cancel')
                          ORDER BY t.sort,t.id""").fetchall()
    c.close()
    omap = {o["table_id"]: o for o in orders}
    ks_label = {"none": "—", "sent": "Отправлен на кухню", "new": "Отправлен на кухню",
                "cooking": "Готовится", "ready": "ГОТОВ", "done": "Подан"}
    html = ['<h2>Столы и кабинки — %s</h2>' % esc(waiter["name"])]

    # сводка активных заказов
    active = [o for o in orders if o["oid"]]
    if active:
        html.append('<div class="card"><h3>Активные заказы</h3><div class="tiles">')
        for o in active:
            st = o["kitchen_status"] or "none"
            html.append('<a class="tile %s" href="/waiter/order?table=%d&pin=%s">'
                        '<b>%s · %s</b>Заказ №%s<br>%s %s<br>'
                        '<span class="pill p-%s">%s</span></a>' % (
                            o["tstatus"], o["table_id"], esc(waiter.get("pin", "")),
                            esc(o["rname"] or ""), esc(o["tname"]),
                            o["number"], _fmt_money(o["total"]), esc(s_get("currency")),
                            "ready" if st in ("ready", "done") else ("cooking" if st == "cooking" else "sent"),
                            esc(ks_label.get(st, "—"))))
        html.append('</div></div>')

    for h in halls:
        html.append('<div class="card"><h2>%s</h2>' % esc(h["name"]))
        for r in [x for x in rooms if x["hall_id"] == h["id"]]:
            html.append('<h3>%s%s</h3><div class="tiles">' % (
                esc(r["name"]), (" · мест: %d" % r["seats"]) if r["seats"] else ""))
            for t in [x for x in tables if x["room_id"] == r["id"]]:
                o = omap.get(t["id"])
                st = t["status"] if not o else ("bill" if o["ostatus"] == "bill" else "busy")
                label = "—" if not o else ("Заказ №%s · %s" % (o["number"], _fmt_money(o["total"])))
                html.append('<a class="tile %s" href="/waiter/order?table=%d&pin=%s">'
                            '<b>%s</b>%s<br><span class="hint">%d мест</span></a>' % (
                                st, t["id"], esc(waiter.get("pin", "")), esc(t["name"]),
                                esc(label), t["seats"]))
            html.append('</div>')
        html.append('</div>')
    return "".join(html)


def render_order(waiter, table_id):
    c = db()
    t = c.execute("SELECT t.*,r.name rname,h.name hname FROM tables_ t "
                  "LEFT JOIN rooms r ON r.id=t.room_id LEFT JOIN halls h ON h.id=r.hall_id "
                  "WHERE t.id=?", (table_id,)).fetchone()
    if not t:
        c.close(); return '<div class="card">Стол не найден</div>'
    cats = c.execute("SELECT * FROM categories WHERE active=1 ORDER BY sort,id").fetchall()
    dishes = c.execute("SELECT * FROM dishes WHERE active=1 ORDER BY sort,id").fetchall()
    c.close()

    body = ['<div class="grid2">']
    # ---- левая колонка: меню
    body.append('<div><div class="card"><h2>Меню — %s · %s / %s</h2>' % (
        esc(t["name"]), esc(t["hname"] or ""), esc(t["rname"] or "")))
    body.append('<div class="row"><input id="fnd" placeholder="Поиск блюда..." oninput="filt()"></div>')
    for cat in cats:
        ds = [d for d in dishes if d["category_id"] == cat["id"]]
        if not ds: continue
        body.append('<div class="cat" data-cat>%s</div><div class="dishes">' % esc(cat["name"]))
        for d in ds:
            cls = "dish stop" if d["stop"] else "dish"
            body.append('<button class="%s" data-name="%s" data-cat="%s" onclick="add(%d)">'
                        '<span class="n">%s</span><span class="p">%s %s</span>'
                        '<span class="hint">%s</span></button>' % (
                            cls, esc(d["name"].lower()), esc(cat["name"].lower()), d["id"],
                            esc(d["name"]), _fmt_money(d["price"]), esc(s_get("currency")),
                            esc(d["description"] or ("СТОП" if d["stop"] else ""))))
        body.append('</div>')
    body.append('</div></div>')

    # ---- правая колонка: заказ
    body.append('<div><div class="card sticky"><h2>Заказ</h2>')
    body.append('<div id="oblock"></div>')
    body.append('<div class="row" style="margin-top:10px">'
                '<button class="btn-b grow" onclick="sendKitchen()">Отправить на кухню</button>'
                '<button class="btn-y" onclick="printBill(\'prebill\')">Счёт</button></div>')
    body.append('<div class="row" style="margin-top:8px">'
                '<button class="btn-p grow" onclick="goPay()">Оплата</button>'
                '<button class="btn-r" onclick="cancelOrder()">Отмена</button></div>')
    body.append('<p class="hint" id="pinfo" style="margin-top:8px"></p>')
    body.append('</div></div></div>')

    js = """
<script>
var TABLE=%d, WID=%s, CUR=%s;
function fmt(v){var s=(Math.round(v*100)/100).toString().split('.');
 s[0]=s[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g,' ');return s.join('.');}
function add(id){post('/api/cart/add',{table:TABLE,dish_id:id,waiter_id:WID}).then(function(r){
 if(!r.ok){toast(r.error||'Не удалось',true);return} draw(r)});}
function setq(line_id,q){post('/api/cart/qty',{line_id:line_id,qty:q}).then(function(r){draw(r)});}
function setnote(line_id){var n=prompt('Комментарий к блюду:');if(n===null)return;
 post('/api/cart/note',{line_id:line_id,note:n}).then(function(r){draw(r)});}
function draw(r){
 if(!r)return; var o=r.order; if(!o){document.getElementById('oblock').innerHTML='<p class="hint">Ничего не выбрано</p>';return}
 var h='<div class="hint">Заказ №'+o.number+' · стол '+o.tname+' · гостей '+o.guests+
  ' · статус: '+o.status_label+'</div>';
 r.items.forEach(function(i){
  h+='<div class="line"><span class="nm">'+i.name+(i.note?'<br><span class="hint">'+i.note+'</span>':'')+
   '<br><span class="hint">'+fmt(i.price)+' × '+i.qty+' = '+fmt(i.price*i.qty)+'</span></span>'+
   '<span class="q"><button onclick="setq('+i.id+','+(i.qty-1)+')">−</button>'+
   '<b>'+(+i.qty)+'</b>'+
   '<button onclick="setq('+i.id+','+(i.qty+1)+')">+</button>'+
   '<button onclick="setnote('+i.id+')" style="width:34px;height:34px;padding:0">✎</button>'+
   '<button onclick="setq('+i.id+',0)" style="width:34px;height:34px;padding:0">✕</button></span></div>';});
 h+='<div class="sum"><span>Блюда</span><b>'+fmt(o.total)+' '+CUR+'</b></div>';
 if(o.service>0)h+='<div class="sum"><span>Обслуживание</span><b>'+fmt(o.service)+'</b></div>';
 h+='<div class="sum big"><span>Итого</span><span>'+fmt(o.grand)+' '+CUR+'</span></div>';
 document.getElementById('oblock').innerHTML=h;
 document.getElementById('pinfo').textContent='Кухня: '+(r.kitchen_label||'—');
}
function sendKitchen(){post('/api/order/send',{order_id:LAST,OID:LAST}).then(function(r){
 if(!r.ok){toast(r.error||'Ошибка',true);return}
 toast(r.print_ok?(r.print_msg):('На кухню. '+r.print_msg),!r.print_ok); draw(r);});}
function goPay(){location.href='/cashier?order='+LAST+'&pin='+WID;}
function printBill(kind){post('/api/order/print',{order_id:LAST,kind:kind}).then(function(r){
 toast(r.print_ok?r.print_msg:('Печать: '+r.print_msg),!r.print_ok);});}
function cancelOrder(){if(!confirm('Отменить заказ?'))return;
 post('/api/order/cancel',{order_id:LAST}).then(function(){toast('Заказ отменён');location.reload()});}
function filt(){var s=document.getElementById('fnd').value.toLowerCase();
 document.querySelectorAll('.dish').forEach(function(d){d.style.display=d.dataset.name.indexOf(s)>=0?'':'none'});
 document.querySelectorAll('[data-cat]').forEach(function(c){c.style.display=s?'none':''});}
var LAST=0;
post('/api/order/open',{table:TABLE,waiter_id:WID}).then(function(r){
 if(!r.ok){toast(r.error||'Не удалось открыть стол',true);return}
 LAST=r.order.id; r.order.tname=r.order.tname||r.tname; draw(r);});
</script>""" % (table_id, waiter["id"], json.dumps(s_get("currency", "сум")))
    return "".join(body) + js


def h_kitchen(handler, q):
    return handler.html(render_kitchen())


def render_kitchen():
    c = db()
    rows = c.execute("""SELECT o.*,t.name tname,r.name rname,h.name hname,w.name wname
                        FROM orders o LEFT JOIN tables_ t ON t.id=o.table_id
                        LEFT JOIN rooms r ON r.id=t.room_id LEFT JOIN halls h ON h.id=r.hall_id
                        LEFT JOIN waiters w ON w.id=o.waiter_id
                        WHERE o.status NOT IN ('paid','cancel') AND o.kitchen_status<>'none'
                        ORDER BY CASE o.kitchen_status WHEN 'sent' THEN 0 WHEN 'new' THEN 0
                                 WHEN 'cooking' THEN 1 WHEN 'ready' THEN 2 ELSE 3 END, o.id""").fetchall()
    out = ['<h2>Кухня</h2>', '<div class="row"><button class="btn-b" onclick="location.reload()">'
           'Обновить</button><span class="hint" style="align-self:center">'
           'Экран обновляется сам каждые 10 секунд</span></div>', '<div class="kb" id="kb">']
    for o in rows:
        st = o["kitchen_status"]
        cls = "cooking" if st == "cooking" else ("ready" if st in ("ready", "done") else "")
        out.append('<div class="kcard %s"><b>Заказ №%s</b> <span class="pill p-%s">%s</span><br>'
                   '<span class="hint">%s / %s / %s · %s</span><table>' % (
                       cls, o["number"], "ready" if st in ("ready", "done") else st,
                       {"sent": "НОВЫЙ", "new": "НОВЫЙ", "cooking": "ГОТОВИТСЯ",
                        "ready": "ГОТОВ", "done": "ПОДАН"}.get(st, st),
                       esc(o["hname"] or ""), esc(o["rname"] or ""), esc(o["tname"] or ""),
                       esc(o["wname"] or "-")))
        for it in c.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id",
                            (o["id"],)).fetchall():
            line = '%s <b>× %s</b>' % (esc(it["name"]), _fmt_qty(it["qty"]))
            if it["note"]: line += '<br><span class="hint">→ %s</span>' % esc(it["note"])
            extra = ""
            if it["kitchen_status"] in ("cooking", "ready", "served"):
                extra = ' <span class="pill p-%s">%s</span>' % (
                    it["kitchen_status"], {"cooking": "готовится", "ready": "готово",
                                           "served": "подано"}[it["kitchen_status"]])
            else:
                extra = (' <button style="padding:4px 8px;font-size:12px" '
                         'onclick="kitem(%d,\'cooking\')">Начать</button>'
                         ' <button class="btn-p" style="padding:4px 8px;font-size:12px" '
                         'onclick="kitem(%d,\'ready\')">Готово</button>')
            out.append('<tr><td>%s%s</td><td style="width:1%%">%s</td></tr>' % (
                line, extra, "" if extra.startswith(" <span") else ""))
            if extra.startswith(" <span"):
                out[-1] = '<tr><td>%s%s</td><td></td></tr>' % (line, extra)
            else:
                out[-1] = '<tr><td>%s%s</td><td style="white-space:nowrap">%s</td></tr>' % (
                    line, "", extra)
        out.append('</table>')
        out.append('<div class="row" style="margin-top:8px">'
                   '<button class="btn-y" onclick="korder(%d,\'cooking\')">Всё готовится</button>'
                   '<button class="btn-p" onclick="korder(%d,\'ready\')">Всё готово</button>'
                   '<button class="btn-b" onclick="printK(%d)">Печать</button></div>' % (
                       o["id"], o["id"], o["id"]))
        out.append('</div>')
    if not rows:
        out.append('<p class="hint">Новых заказов нет.</p>')
    out.append('</div>')
    c.close()
    js = """
<script>
setTimeout(function(){location.reload()},10000);
function kitem(id,st){post('/api/kitchen/item',{id:id,status:st}).then(function(){location.reload()});}
function korder(id,st){post('/api/kitchen/order',{order_id:id,status:st}).then(function(){location.reload()});}
function printK(id){post('/api/order/print',{order_id:id,kind:'kitchen'}).then(function(r){
 toast(r.print_ok?r.print_msg:('Печать: '+r.print_msg),!r.print_ok);});}
</script>"""
    return "".join(out) + js


def h_cashier(handler, q):
    c = db()
    orders = c.execute("""SELECT o.*,t.name tname,r.name rname,w.name wname
                          FROM orders o LEFT JOIN tables_ t ON t.id=o.table_id
                          LEFT JOIN rooms r ON r.id=t.room_id LEFT JOIN waiters w ON w.id=o.waiter_id
                          WHERE o.status NOT IN ('paid','cancel') ORDER BY o.id DESC""").fetchall()
    out = ['<h2>Касса</h2>', '<div class="grid2"><div><div class="card"><h3>Открытые заказы</h3>']
    if not orders:
        out.append('<p class="hint">Открытых заказов нет.</p>')
    for o in orders:
        items = c.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (o["id"],)).fetchall()
        out.append('<div class="card" style="box-shadow:none;border:1px solid #eaecf0">'
                   '<b>Заказ №%s</b> <span class="pill p-%s">%s</span><br>'
                   '<span class="hint">%s / %s · официант %s · %s</span>'
                   '<table>' % (o["number"], o["status"], o["status"],
                                esc(o["rname"] or ""), esc(o["tname"] or ""),
                                esc(o["wname"] or "-"), esc(o["created"])))
        for it in items:
            out.append('<tr><td>%s</td><td>%s × %s</td><td>%s</td></tr>' % (
                esc(it["name"]), _fmt_money(it["price"]), _fmt_qty(it["qty"]),
                _fmt_money(it["price"] * it["qty"])))
        out.append('</table>')
        out.append('<div class="sum"><span>Блюда</span><b>%s</b></div>' % _fmt_money(o["total"] - o["service"] + o["discount"]))
        if o["service"]: out.append('<div class="sum"><span>Обслуживание</span><b>%s</b></div>' % _fmt_money(o["service"]))
        if o["discount"]: out.append('<div class="sum"><span>Скидка</span><b>-%s</b></div>' % _fmt_money(o["discount"]))
        out.append('<div class="sum big"><span>К оплате</span><span>%s %s</span></div>' % (
            _fmt_money(o["total"]), esc(s_get("currency"))))
        out.append('<div class="row" style="margin-top:8px">'
                   '<input id="cash%s" placeholder="Внесено" inputmode="decimal" class="grow" '
                   'oninput="chg(%d)">'
                   '<select id="pay%s" style="width:150px"><option value="Наличные">Наличные</option>'
                   '<option value="Карта">Карта</option><option value="Перевод">Перевод</option>'
                   '<option value="Смешанно">Смешанно</option></select></div>' % (
                       o["id"], o["id"], o["id"]))
        out.append('<div class="hint" id="chg%s">Сдача: 0</div>' % o["id"])
        out.append('<div class="row" style="margin-top:8px">'
                   '<button class="btn-p grow" onclick="pay(%d)">Оплатить и закрыть</button>'
                   '<button class="btn-y" onclick="pr(%d,\'prebill\')">Счёт</button>'
                   '<button class="btn-b" onclick="pr(%d,\'receipt\')">Чек</button></div></div>' % (
                       o["id"], o["id"], o["id"]))
    out.append('</div></div>')
    # правая колонка - итоги дня
    tot = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total),0) s FROM orders "
                    "WHERE status='paid' AND date(closed_at)=?", (today(),)).fetchone()
    byp = c.execute("SELECT method, COUNT(*) n, COALESCE(SUM(amount),0) s FROM payments "
                    "WHERE date(created)=? GROUP BY method", (today(),)).fetchall()
    out.append('<div><div class="card"><h3>Итоги дня (%s)</h3>'
               '<div class="sum"><span>Закрытых заказов</span><b>%s</b></div>'
               '<div class="sum big"><span>Выручка</span><span>%s %s</span></div>' % (
                   today(), tot["n"], _fmt_money(tot["s"]), esc(s_get("currency"))))
    for b in byp:
        out.append('<div class="sum"><span>%s</span><b>%s</b></div>' % (
            esc(b["method"]), _fmt_money(b["s"])))
    out.append('</div></div></div></div>')
    c.close()
    js = """
<script>
var CUR=%s;
function chg(id){var o=window['ORD'+id];}
function pay(id){var el=document.getElementById('cash'+id);var v=parseFloat((el.value||'0').replace(/\\s/g,''));
 post('/api/order/pay',{order_id:id,amount:isNaN(v)?0:v,
  method:document.getElementById('pay'+id).value}).then(function(r){
  if(!r.ok){toast(r.error||'Ошибка',true);return}
  toast('Оплачено. Сдача: '+r.change+' '+CUR+(r.print_ok?'':' ('+r.print_msg+')'),!r.print_ok);
  setTimeout(function(){location.reload()},900);});}
function pr(id,kind){post('/api/order/print',{order_id:id,kind:kind}).then(function(r){
 toast(r.print_ok?r.print_msg:('Печать: '+r.print_msg),!r.print_ok);});}
</script>""" % json.dumps(s_get("currency", "сум"))
    return page("Касса", "".join(out), "cashier", extra_head="") .replace("</body>", js + "</body>")


def h_manager(handler, q):
    c = db()
    cats = c.execute("SELECT * FROM categories ORDER BY sort,id").fetchall()
    dishes = c.execute("SELECT * FROM dishes ORDER BY category_id,sort,id").fetchall()
    halls = c.execute("SELECT * FROM halls ORDER BY sort,id").fetchall()
    rooms = c.execute("SELECT * FROM rooms ORDER BY sort,id").fetchall()
    tables = c.execute("SELECT * FROM tables_ ORDER BY sort,id").fetchall()
    waiters = c.execute("SELECT * FROM waiters ORDER BY id").fetchall()
    c.close()
    out = ['<h2>Меню, залы, кабинки и столы</h2>']
    # --- Меню
    out.append('<div class="card"><h3>Категории меню</h3><table><tr><th>Название</th><th>Порядок</th><th>Вкл.</th><th></th></tr>')
    for k in cats:
        out.append('<tr><td><input value="%s" onchange="saveCat(%d,this.value,0,1)"></td>'
                   '<td style="width:90px"><input value="%s" onchange="saveCat(%d,null,this.value,1)"></td>'
                   '<td style="width:70px"><input type="checkbox" %s onchange="saveCat(%d,null,null,this.checked)"></td>'
                   '<td style="width:90px"><button class="btn-r" onclick="delCat(%d)">Удалить</button></td></tr>' % (
                       esc(k["name"]), k["id"], k["sort"], k["id"],
                       "checked" if k["active"] else "", k["id"], k["id"]))
    out.append('</table><div class="row" style="margin-top:10px">'
               '<input id="nc" placeholder="Новая категория" class="grow">'
               '<button class="btn-p" onclick="addCat()">Добавить категорию</button></div></div>')

    out.append('<div class="card"><h3>Блюда и цены</h3>')
    out.append('<div class="row"><input id="nf" placeholder="Название блюда" class="grow">'
               '<select id="nc2" style="width:200px">%s</select>'
               '<input id="np" placeholder="Цена" inputmode="decimal" style="width:120px">'
               '<input id="nst" placeholder="Цех (Кухня/Бар)" style="width:160px" value="Кухня">'
               '<button class="btn-p" onclick="addDish()">Добавить блюдо</button></div>' %
               "".join('<option value="%d">%s</option>' % (k["id"], esc(k["name"])) for k in cats))
    out.append('<table><tr><th>Блюдо</th><th>Категория</th><th>Цена</th><th>Цех</th>'
               '<th>Стоп</th><th>Вкл.</th><th></th></tr>')
    for d in dishes:
        out.append('<tr><td><input value="%s" onchange="saveDish(%d,\'name\',this.value)"></td>'
                   '<td style="width:180px"><select onchange="saveDish(%d,\'category_id\',this.value)">%s</select></td>'
                   '<td style="width:110px"><input value="%s" inputmode="decimal" onchange="saveDish(%d,\'price\',this.value)"></td>'
                   '<td style="width:130px"><input value="%s" onchange="saveDish(%d,\'station\',this.value)"></td>'
                   '<td style="width:70px"><input type="checkbox" %s onchange="saveDish(%d,\'stop\',this.checked?1:0)"></td>'
                   '<td style="width:70px"><input type="checkbox" %s onchange="saveDish(%d,\'active\',this.checked?1:0)"></td>'
                   '<td style="width:90px"><button class="btn-r" onclick="delDish(%d)">Удалить</button></td></tr>' % (
                       esc(d["name"]), d["id"], d["id"],
                       "".join('<option value="%d"%s>%s</option>' % (
                           k["id"], " selected" if k["id"] == d["category_id"] else "", esc(k["name"]))
                           for k in cats),
                       _fmt_money(d["price"]), d["id"], esc(d["station"]), d["id"],
                       "checked" if d["stop"] else "", d["id"],
                       "checked" if d["active"] else "", d["id"], d["id"]))
    out.append('</table></div>')

    # --- Залы, кабинки, столы
    out.append('<div class="card"><h3>Залы, кабинки и столы</h3>')
    out.append('<div class="row"><input id="nh" placeholder="Название зала" class="grow">'
               '<button class="btn-p" onclick="addHall()">Добавить зал</button></div>')
    for h in halls:
        out.append('<div style="margin-top:14px"><b>%s</b> '
                   '<button class="btn-r" style="padding:5px 10px;font-size:13px" onclick="delHall(%d)">Удалить зал</button>'
                   '<div class="row" style="margin-top:8px">'
                   '<input id="nr%d" placeholder="Название кабинки / зоны" class="grow">'
                   '<input id="ns%d" placeholder="Мест (0 = обычная зона)" style="width:210px">'
                   '<button class="btn-p" onclick="addRoom(%d)">Добавить кабинку</button></div></div>' % (
                       esc(h["name"]), h["id"], h["id"], h["id"], h["id"]))
        for r in [x for x in rooms if x["hall_id"] == h["id"]]:
            out.append('<div style="margin-left:18px;margin-top:12px;padding:10px;'
                       'border-left:4px solid #ffb703;background:#fffdf5"><b>%s</b>'
                       ' <span class="hint">%s</span> '
                       '<button class="btn-r" style="padding:4px 9px;font-size:12px" onclick="delRoom(%d)">Удалить</button>'
                       '<div class="row" style="margin-top:8px">'
                       '<input id="nt%d" placeholder="Название стола" class="grow">'
                       '<input id="nts%d" placeholder="Мест" style="width:100px">'
                       '<button class="btn-p" onclick="addTable(%d)">Добавить стол</button></div>'
                       '<table style="margin-top:8px"><tr><th>Стол</th><th>Мест</th><th>Статус</th><th></th></tr>' % (
                           esc(r["name"]), ("мест: %d" % r["seats"]) if r["seats"] else "обычная зона",
                           r["id"], r["id"], r["id"], r["id"]))
            for t in [x for x in tables if x["room_id"] == r["id"]]:
                out.append('<tr><td><input value="%s" onchange="saveTable(%d,\'name\',this.value)"></td>'
                           '<td style="width:100px"><input value="%d" inputmode="numeric" onchange="saveTable(%d,\'seats\',this.value)"></td>'
                           '<td style="width:130px"><select onchange="saveTable(%d,\'status\',this.value)">%s</select></td>'
                           '<td style="width:90px"><button class="btn-r" onclick="delTable(%d)">Удалить</button></td></tr>' % (
                               esc(t["name"]), t["id"], t["seats"], t["id"], t["id"],
                               "".join('<option value="%s"%s>%s</option>' % (
                                   v, " selected" if v == t["status"] else "", lbl)
                                   for v, lbl in [("free", "Свободен"), ("busy", "Занят"), ("bill", "Счёт")]),
                               t["id"]))
            out.append('</table></div>')
    out.append('</div>')

    # --- Официанты
    out.append('<div class="card"><h3>Официанты и PIN-коды</h3>'
               '<div class="row"><input id="nw" placeholder="Имя официанта" class="grow">'
               '<input id="npw" placeholder="PIN (4 цифры)" style="width:180px">'
               '<button class="btn-p" onclick="addWaiter()">Добавить</button></div>'
               '<table style="margin-top:10px"><tr><th>Имя</th><th>PIN</th><th>Вкл.</th><th></th></tr>')
    for w in waiters:
        out.append('<tr><td><input value="%s" onchange="saveWaiter(%d,\'name\',this.value)"></td>'
                   '<td style="width:140px"><input value="%s" onchange="saveWaiter(%d,\'pin\',this.value)"></td>'
                   '<td style="width:70px"><input type="checkbox" %s onchange="saveWaiter(%d,\'active\',this.checked?1:0)"></td>'
                   '<td style="width:90px"><button class="btn-r" onclick="delWaiter(%d)">Удалить</button></td></tr>' % (
                       esc(w["name"]), w["id"], esc(w["pin"]), w["id"],
                       "checked" if w["active"] else "", w["id"], w["id"]))
    out.append('</table></div>')
    js = """
<script>
function go(u,d){return post(u,d).then(function(r){if(!r.ok){toast(r.error||'Ошибка',true);return false}
 location.reload();return true});}
function addCat(){go('/api/category/add',{name:document.getElementById('nc').value});}
function saveCat(id,name,sort,active){go('/api/category/save',{id:id,name:name,sort:sort,active:active?1:0});}
function delCat(id){if(confirm('Удалить категорию и её блюда?'))go('/api/category/delete',{id:id});}
function addDish(){go('/api/dish/add',{name:document.getElementById('nf').value,
 category_id:document.getElementById('nc2').value,price:document.getElementById('np').value,
 station:document.getElementById('nst').value});}
function saveDish(id,k,v){var d={id:id};d[k]=v;go('/api/dish/save',d);}
function delDish(id){if(confirm('Удалить блюдо?'))go('/api/dish/delete',{id:id});}
function addHall(){go('/api/hall/add',{name:document.getElementById('nh').value});}
function delHall(id){if(confirm('Удалить зал со всеми кабинками?'))go('/api/hall/delete',{id:id});}
function addRoom(h){go('/api/room/add',{hall_id:h,name:document.getElementById('nr'+h).value,
 seats:document.getElementById('ns'+h).value});}
function delRoom(id){if(confirm('Удалить кабинку со столами?'))go('/api/room/delete',{id:id});}
function addTable(r){go('/api/table/add',{room_id:r,name:document.getElementById('nt'+r).value,
 seats:document.getElementById('nts'+r).value||4});}
function saveTable(id,k,v){var d={id:id};d[k]=v;go('/api/table/save',d);}
function delTable(id){if(confirm('Удалить стол?'))go('/api/table/delete',{id:id});}
function addWaiter(){go('/api/waiter/add',{name:document.getElementById('nw').value,
 pin:document.getElementById('npw').value});}
function saveWaiter(id,k,v){var d={id:id};d[k]=v;go('/api/waiter/save',d);}
function delWaiter(id){if(confirm('Удалить официанта?'))go('/api/waiter/delete',{id:id});}
</script>"""
    return page("Меню и залы", "".join(out), "manager").replace("</body>", js + "</body>")


def h_settings(handler, q):
    s = s_all()
    ok = q.get("ok", [""])[0]
    msg = q.get("msg", [""])[0]
    fields = [
        ("cafe_name", "Название заведения", "text"),
        ("address", "Адрес", "text"),
        ("phone", "Телефон", "text"),
        ("currency", "Валюта (сум, ₽, $, €)", "text"),
        ("footer_text", "Текст внизу чека", "text"),
        ("service_percent", "Процент обслуживания (%)", "text"),
        ("tax_percent", "Налог (%)", "text"),
        ("password", "Общий пароль администратора", "text"),
        ("receipt_width", "Ширина чека (символов): 32 / 42 / 48", "text"),
    ]
    out = ['<h2>Настройки</h2>']
    if msg:
        out.append('<div class="card" style="border-left:6px solid %s">%s</div>' % (
            "#12b76a" if ok == "1" else "#d92d20", esc(msg)))
    out.append('<div class="grid2"><div class="card"><h3>Заведение и чеки</h3><form method="POST" action="/settings/save">')
    for k, label, t in fields:
        out.append('<label>%s</label><input name="%s" value="%s">' % (esc(label), k, esc(s.get(k, ""))))
    out.append('<h3 style="margin-top:16px">Принтер</h3>')
    out.append('<label>Способ подключения</label><select name="printer_mode" id="pm" onchange="pmode()">%s</select>' % "".join(
        '<option value="%s"%s>%s</option>' % (v, " selected" if s.get("printer_mode") == v else "", lbl)
        for v, lbl in [("none", "Не настроен (без печати)"),
                       ("network", "Сетевой / Wi-Fi принтер (IP:9100) — рекомендуется"),
                       ("windows", "Принтер Windows (общая печать)"),
                       ("command", "Команда системы (Linux/Mac lp, lpr)")]))
    for k, label in [("printer_ip", "IP-адрес принтера"), ("printer_port", "Порт (обычно 9100)"),
                     ("printer_windows", "Имя принтера в Windows"),
                     ("printer_command", "Системная команда печати")]:
        out.append('<label>%s</label><input name="%s" value="%s">' % (esc(label), k, esc(s.get(k, ""))))
    out.append('<label>Автопечать тикета на кухню при отправке заказа</label>'
               '<select name="auto_print_kitchen">%s</select>' % "".join(
                   '<option value="%s"%s>%s</option>' % (v, " selected" if s.get("auto_print_kitchen") == v else "", lbl)
                   for v, lbl in [("1", "Да"), ("0", "Нет")]))
    out.append('<br><br><button class="btn-p btn-lg" type="submit">Сохранить настройки</button></form>')
    out.append('<form method="POST" action="/settings/testprint" style="margin-top:10px">'
               '<button class="btn-b btn-lg" type="submit">Проверить принтер (тестовая печать)</button></form>')
    out.append('<form method="POST" action="/settings/reset" style="margin-top:10px" '
               'onsubmit="return confirm(\'Сбросить ВСЕ данные (меню, залы, заказы)?\')">'
               '<button class="btn-r btn-lg" type="submit">Сбросить демонстрационные данные</button></form>')
    out.append('</div>')

    c = db()
    jobs = c.execute("SELECT * FROM print_jobs ORDER BY id DESC LIMIT 15").fetchall()
    c.close()
    out.append('<div><div class="card"><h3>Журнал печати (последние 15 заданий)</h3>')
    if not jobs:
        out.append('<p class="hint">Заданий пока нет.</p>')
    for j in jobs:
        out.append('<div style="border-bottom:1px solid #eaecf0;padding:8px 0">'
                   '<b>%s</b> · заказ %s · %s <span class="pill %s">%s</span><br>'
                   '<span class="hint">%s</span>%s</div>' % (
                       esc(j["kind"]), j["order_id"], esc(j["created"]),
                       "p-ready" if j["status"] == "done" else "p-new",
                       "отправлено" if j["status"] == "done" else "ошибка",
                       esc(j["printer"] or ""),
                       ('<div class="mono" style="margin-top:6px">%s</div>' % esc(j["text"])) if j["kind"] != "receipt" else ""))
    out.append('</div>')
    out.append('<div class="card"><h3>Как подключить принтер (по шагам)</h3>'
               '<p class="hint">1) Чековый принтер подключите к Wi-Fi или к сети кассы (Ethernet).<br>'
               '2) Узнайте его IP (на принтере распечатайте тест-страницу или посмотрите в роутере).<br>'
               '3) Выберите «Сетевой / Wi-Fi принтер», введите IP и порт 9100, сохраните.<br>'
               '4) Нажмите «Проверить принтер» — чек должен напечататься.<br>'
               '5) Для USB-принтера на Windows: установите драйвер, расшарьте принтер и укажите его имя.<br>'
               'Приложение печатает ESC/POS-совместимые чеки (Xprinter, Rongta, Epson, Gprinter и др.).</p>'
               '</div></div></div>')
    js = """
<script>
function pmode(){var m=document.getElementById('pm').value;
 document.querySelectorAll('input[name^=printer_]').forEach(function(i){i.disabled=false});
 if(m=='none'){document.querySelectorAll('input[name^=printer_]').forEach(function(i){i.disabled=true});}}
pmode();
</script>"""
    return page("Настройки", "".join(out), "settings").replace("</body>", js + "</body>")


def h_reports(handler, q):
    day = q.get("day", [today()])[0]
    c = db()
    tot = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total),0) s FROM orders "
                    "WHERE status='paid' AND date(closed_at)=?", (day,)).fetchone()
    byp = c.execute("SELECT method, COUNT(*) n, COALESCE(SUM(amount),0) s FROM payments "
                    "WHERE date(created)=? GROUP BY method", (day,)).fetchall()
    top = c.execute("""SELECT oi.name, SUM(oi.qty) q, SUM(oi.qty*oi.price) s
                       FROM order_items oi JOIN orders o ON o.id=oi.order_id
                       WHERE date(o.created)=? GROUP BY oi.name ORDER BY s DESC LIMIT 12""",
                    (day,)).fetchall()
    byw = c.execute("""SELECT COALESCE(w.name,'—') wname, COUNT(DISTINCT o.id) n, COALESCE(SUM(o.total),0) s
                       FROM orders o LEFT JOIN waiters w ON w.id=o.waiter_id
                       WHERE date(o.created)=? AND o.status NOT IN ('cancel') GROUP BY w.id""",
                    (day,)).fetchall()
    orders = c.execute("""SELECT o.*,t.name tname FROM orders o LEFT JOIN tables_ t ON t.id=o.table_id
                          WHERE date(o.created)=? ORDER BY o.id DESC""", (day,)).fetchall()
    c.close()
    out = ['<h2>Отчёты</h2><div class="card"><form method="GET" action="/reports" class="row">'
           '<input type="date" name="day" value="%s" style="width:200px">'
           '<button class="btn-b" type="submit">Показать</button></form></div>' % esc(day)]
    out.append('<div class="grid2"><div>')
    out.append('<div class="card"><h3>Итоги за %s</h3>'
               '<div class="sum"><span>Закрытых заказов</span><b>%s</b></div>'
               '<div class="sum big"><span>Выручка</span><span>%s %s</span></div></div>' % (
                   esc(day), tot["n"], _fmt_money(tot["s"]), esc(s_get("currency"))))
    out.append('<div class="card"><h3>Оплаты по способам</h3><table><tr><th>Способ</th><th>Заказов</th><th>Сумма</th></tr>')
    for b in byp:
        out.append('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            esc(b["method"]), b["n"], _fmt_money(b["s"])))
    out.append('</table></div>')
    out.append('<div class="card"><h3>По официантам</h3><table><tr><th>Официант</th><th>Заказов</th><th>Сумма</th></tr>')
    for w in byw:
        out.append('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            esc(w["wname"]), w["n"], _fmt_money(w["s"])))
    out.append('</table></div></div>')
    out.append('<div class="card"><h3>Хиты продаж</h3><table><tr><th>Блюдо</th><th>Кол-во</th><th>Сумма</th></tr>')
    for t in top:
        out.append('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            esc(t["name"]), _fmt_qty(t["q"]), _fmt_money(t["s"])))
    out.append('</table></div>')
    out.append('<div class="card"><h3>Все заказы за день</h3>'
               '<table><tr><th>№</th><th>Стол</th><th>Статус</th><th>Кухня</th><th>Сумма</th><th>Создан</th><th>Закрыт</th></tr>')
    for o in orders:
        out.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            o["number"], esc(o["tname"] or ""), esc(o["status"]), esc(o["kitchen_status"] or ""),
            _fmt_money(o["total"]), esc(o["created"] or ""), esc(o["closed_at"] or "")))
    out.append('</table></div></div>')
    return page("Отчёты", "".join(out), "reports")


# --------------------------------------------------------------------------
#  Действия (POST /api/...)
# --------------------------------------------------------------------------
def ensure_order(c, table_id, waiter_id=0):
    o = c.execute("SELECT * FROM orders WHERE table_id=? AND status NOT IN ('paid','cancel') "
                  "ORDER BY id DESC LIMIT 1", (table_id,)).fetchone()
    if o:
        return o["id"]
    n = c.execute("SELECT COALESCE(MAX(number),0)+1 n FROM orders").fetchone()["n"]
    c.execute("""INSERT INTO orders(number,table_id,waiter_id,status,kitchen_status,total,created)
                 VALUES(?,?,?,'new','none',0,?)""", (n, table_id, waiter_id, now()))
    oid = c.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    c.execute("UPDATE tables_ SET status='busy' WHERE id=?", (table_id,))
    c.commit()
    return oid

STATUS_LABEL = {"new": "принят", "bill": "счёт запрошен", "paid": "оплачен", "cancel": "отменён",
                "cooking": "готовится", "ready": "готов", "served": "подан"}
KS_LABEL = {"none": "не отправлен", "new": "отправлен", "sent": "отправлен", "accepted": "принят",
            "cooking": "готовится", "ready": "готов", "done": "подан"}

def order_payload(order_id):
    c = db()
    o = c.execute("""SELECT o.*,t.name tname,t.status tstatus,r.name rname,h.name hname
                     FROM orders o LEFT JOIN tables_ t ON t.id=o.table_id
                     LEFT JOIN rooms r ON r.id=t.room_id LEFT JOIN halls h ON h.id=r.hall_id
                     WHERE o.id=?""", (order_id,)).fetchone()
    if not o:
        c.close(); return None
    items = c.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    c.close()
    return {
        "order": {"id": o["id"], "number": o["number"], "table_id": o["table_id"],
                  "tname": o["tname"], "guests": o["guests"],
                  "status": o["status"], "status_label": STATUS_LABEL.get(o["status"], o["status"]),
                  "kitchen_status": o["kitchen_status"],
                  "total": o["total"] - o["service"] + o["discount"],
                  "service": o["service"], "discount": o["discount"], "grand": o["total"]},
        "items": [{"id": i["id"], "name": i["name"], "price": i["price"], "qty": i["qty"],
                   "note": i["note"], "kitchen_status": i["kitchen_status"]} for i in items],
        "kitchen_label": KS_LABEL.get(kitchen_state(order_id), "—"),
        "tname": o["tname"],
    }


def do_action(path, data):
    c = db()
    # --- корзина ---
    if path == "/api/order/open":
        oid = ensure_order(c, int(data.get("table", 0)), int(data.get("waiter_id", 0)))
        c.close()
        return {"ok": True, **(order_payload(oid) or {})}
    if path == "/api/cart/add":
        oid = ensure_order(c, int(data.get("table", 0)), int(data.get("waiter_id", 0)))
        d = c.execute("SELECT * FROM dishes WHERE id=?", (data.get("dish_id"),)).fetchone()
        if not d:
            c.close(); return {"ok": False, "error": "Блюдо не найдено"}
        if d["stop"]:
            c.close(); return {"ok": False, "error": "Блюдо в стоп-листе"}
        same = c.execute("""SELECT * FROM order_items WHERE order_id=? AND dish_id=?
                            AND COALESCE(note,'')='' AND kitchen_status='new'""",
                         (oid, d["id"])).fetchone()
        if same:
            c.execute("UPDATE order_items SET qty=qty+1 WHERE id=?", (same["id"],))
        else:
            c.execute("""INSERT INTO order_items(order_id,dish_id,name,price,qty,station,kitchen_status)
                         VALUES(?,?,?,?,1,?,'new')""", (oid, d["id"], d["name"], d["price"], d["station"]))
        recalc(oid); c.close()
        return {"ok": True, **(order_payload(oid) or {})}
    if path == "/api/cart/qty":
        lid = int(data.get("line_id", 0)); q = float(data.get("qty", 1))
        if q <= 0:
            c.execute("DELETE FROM order_items WHERE id=?", (lid,))
        else:
            c.execute("UPDATE order_items SET qty=? WHERE id=?", (q, lid))
        r = c.execute("SELECT order_id FROM order_items WHERE id=?", (lid,)).fetchone()
        oid = r["order_id"] if r else None
        if not oid:
            r2 = c.execute("SELECT order_id FROM orders ORDER BY id DESC LIMIT 1").fetchone()
            oid = r2["order_id"] if r2 else None
        if oid: recalc(oid)
        c.commit(); c.close()
        return {"ok": True, **(order_payload(oid) or {})}
    if path == "/api/cart/note":
        c.execute("UPDATE order_items SET note=? WHERE id=?", (data.get("note", ""), data.get("line_id")))
        r = c.execute("SELECT order_id FROM order_items WHERE id=?", (data.get("line_id"),)).fetchone()
        c.commit(); c.close()
        return {"ok": True, **(order_payload(r["order_id"]) or {})}

    # --- отправка на кухню ---
    if path == "/api/order/send":
        oid = int(data.get("order_id") or data.get("OID") or 0)
        items = c.execute("SELECT * FROM order_items WHERE order_id=?", (oid,)).fetchall()
        if not items:
            c.close(); return {"ok": False, "error": "В заказе нет блюд"}
        c.execute("""UPDATE order_items SET kitchen_status='sent' WHERE order_id=?
                     AND kitchen_status='new'""", (oid,))
        c.execute("UPDATE orders SET kitchen_status='sent', sent_at=?, status='cooking' WHERE id=?",
                  (now(), oid))
        c.execute("UPDATE order_items SET printed=1 WHERE order_id=?", (oid,))
        c.commit(); c.close()
        ok, msg = queue_print("kitchen", oid, "kitchen")
        return {"ok": True, "print_ok": ok, "print_msg": msg, **(order_payload(oid) or {})}

    if path == "/api/kitchen/item":
        c.execute("UPDATE order_items SET kitchen_status=? WHERE id=?",
                  (data.get("status", "cooking"), data.get("id")))
        r = c.execute("SELECT order_id FROM order_items WHERE id=?", (data.get("id"),)).fetchone()
        oid = r["order_id"] if r else 0
        c.execute("UPDATE orders SET kitchen_status=? WHERE id=?", (kitchen_state(oid), oid))
        c.commit(); c.close()
        return {"ok": True}
    if path == "/api/kitchen/order":
        st = data.get("status", "cooking")
        oid = int(data.get("order_id", 0))
        c.execute("UPDATE order_items SET kitchen_status=? WHERE order_id=? AND kitchen_status<>'served'",
                  (st, oid))
        c.execute("UPDATE orders SET kitchen_status=? WHERE id=?", (kitchen_state(oid), oid))
        c.commit(); c.close()
        return {"ok": True}

    # --- печать ---
    if path == "/api/order/print":
        oid = int(data.get("order_id", 0)); kind = data.get("kind", "receipt")
        c.close()
        ok, msg = queue_print(kind, oid, "kitchen" if kind == "kitchen" else "receipt")
        return {"ok": True, "print_ok": ok, "print_msg": msg}

    # --- оплата ---
    if path == "/api/order/pay":
        oid = int(data.get("order_id", 0))
        o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not o:
            c.close(); return {"ok": False, "error": "Заказ не найден"}
        amount = float(data.get("amount") or o["total"] or 0)
        method = data.get("method", "Наличные")
        change = round(amount - (o["total"] or 0), 2)
        c.execute("INSERT INTO payments(order_id,amount,method,created) VALUES(?,?,?,?)",
                  (oid, o["total"], method, now()))
        c.execute("""UPDATE orders SET status='paid', paid=?, payment=?, closed_at=? WHERE id=?""",
                  (amount, method, now(), oid))
        c.execute("UPDATE order_items SET kitchen_status='served' WHERE order_id=?", (oid,))
        c.execute("UPDATE tables_ SET status='free' WHERE id=?", (o["table_id"],))
        c.commit(); c.close()
        ok, msg = False, ""
        if s_get("auto_print_receipt", "0") == "1":
            ok, msg = queue_print("receipt", oid, "receipt")
        return {"ok": True, "change": _fmt_money(change), "print_ok": ok, "print_msg": msg}
    if path == "/api/order/cancel":
        oid = int(data.get("order_id", 0))
        o = c.execute("SELECT table_id FROM orders WHERE id=?", (oid,)).fetchone()
        c.execute("UPDATE orders SET status='cancel', closed_at=? WHERE id=?", (now(), oid))
        if o:
            c.execute("UPDATE tables_ SET status='free' WHERE id=?", (o["table_id"],))
        c.commit(); c.close()
        return {"ok": True}

    # --- меню ---
    if path == "/api/category/add":
        c.execute("INSERT INTO categories(name,sort) VALUES(?,?)",
                  (data.get("name") or "Новая категория",
                   c.execute("SELECT COALESCE(MAX(sort),0)+1 n FROM categories").fetchone()["n"]))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/category/save":
        i = int(data.get("id", 0))
        for k in ("name", "sort", "active"):
            if data.get(k) is not None:
                c.execute("UPDATE categories SET %s=? WHERE id=?" % k, (data[k], i))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/category/delete":
        i = int(data.get("id", 0))
        c.execute("DELETE FROM dishes WHERE category_id=?", (i,))
        c.execute("DELETE FROM categories WHERE id=?", (i,))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/dish/add":
        try: price = float(str(data.get("price", "0")).replace(",", ".").replace(" ", ""))
        except Exception: price = 0
        c.execute("""INSERT INTO dishes(category_id,name,price,station) VALUES(?,?,?,?)""",
                  (data.get("category_id"), data.get("name") or "Новое блюдо", price,
                   data.get("station") or "Кухня"))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/dish/save":
        i = int(data.get("id", 0))
        for k in ("name", "category_id", "price", "station", "stop", "active", "description"):
            if data.get(k) is not None:
                v = data[k]
                if k == "price":
                    try: v = float(str(v).replace(",", ".").replace(" ", ""))
                    except Exception: v = 0
                c.execute("UPDATE dishes SET %s=? WHERE id=?" % k, (v, i))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/dish/delete":
        c.execute("DELETE FROM dishes WHERE id=?", (data.get("id"),))
        c.commit(); c.close(); return {"ok": True}

    # --- залы / кабинки / столы ---
    if path == "/api/hall/add":
        c.execute("INSERT INTO halls(name,sort) VALUES(?,?)", (data.get("name") or "Новый зал",
                  c.execute("SELECT COALESCE(MAX(sort),0)+1 n FROM halls").fetchone()["n"]))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/hall/delete":
        i = int(data.get("id", 0))
        c.execute("DELETE FROM tables_ WHERE room_id IN (SELECT id FROM rooms WHERE hall_id=?)", (i,))
        c.execute("DELETE FROM rooms WHERE hall_id=?", (i,))
        c.execute("DELETE FROM halls WHERE id=?", (i,))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/room/add":
        try: seats = int(float(data.get("seats") or 0))
        except Exception: seats = 0
        c.execute("INSERT INTO rooms(hall_id,name,seats) VALUES(?,?,?)",
                  (data.get("hall_id"), data.get("name") or "Новая кабинка", seats))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/room/delete":
        i = int(data.get("id", 0))
        c.execute("DELETE FROM tables_ WHERE room_id=?", (i,))
        c.execute("DELETE FROM rooms WHERE id=?", (i,))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/table/add":
        try: seats = int(float(data.get("seats") or 4))
        except Exception: seats = 4
        c.execute("INSERT INTO tables_(room_id,name,seats) VALUES(?,?,?)",
                  (data.get("room_id"), data.get("name") or "Новый стол", seats))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/table/save":
        i = int(data.get("id", 0))
        for k in ("name", "seats", "status"):
            if data.get(k) is not None:
                c.execute("UPDATE tables_ SET %s=? WHERE id=?" % k, (data[k], i))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/table/delete":
        c.execute("DELETE FROM tables_ WHERE id=?", (data.get("id"),))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/table/addzone" or path == "/api/table/status":
        i = int(data.get("id", 0))
        if data.get("status"):
            c.execute("UPDATE tables_ SET status=? WHERE id=?", (data["status"], i))
        c.commit(); c.close(); return {"ok": True}

    # --- официанты ---
    if path == "/api/waiter/add":
        c.execute("INSERT INTO waiters(name,pin) VALUES(?,?)",
                  (data.get("name") or "Официант", data.get("pin") or "0000"))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/waiter/save":
        i = int(data.get("id", 0))
        for k in ("name", "pin", "active"):
            if data.get(k) is not None:
                c.execute("UPDATE waiters SET %s=? WHERE id=?" % k, (data[k], i))
        c.commit(); c.close(); return {"ok": True}
    if path == "/api/waiter/delete":
        c.execute("DELETE FROM waiters WHERE id=?", (data.get("id"),))
        c.commit(); c.close(); return {"ok": True}

    c.close()
    return {"ok": False, "error": "Неизвестное действие: " + path}


# --------------------------------------------------------------------------
#  HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "CafePOS/1.0"

    def log_message(self, fmt, *args):
        pass

    def html(self, body, code=200):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def json_out(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        try:
            if p in ("/", "/waiter"):        return h_waiter(self, q)
            if p == "/waiter/order":         return h_waiter_order(self, q)
            if p == "/kitchen":              return h_kitchen(self, q)
            if p == "/cashier":              return h_cashier(self, q)
            if p == "/manager":              return h_manager(self, q)
            if p == "/settings":             return h_settings(self, q)
            if p == "/reports":              return h_reports(self, q)
            if p == "/favicon.ico":
                self.send_response(204); self.end_headers(); return
            return self.html(page("Не найдено", '<div class="card"><h2>Страница не найдена</h2>'
                                  '<p><a href="/waiter">К заказам</a></p></div>'), 404)
        except Exception as e:
            import traceback; traceback.print_exc()
            return self.html(page("Ошибка", '<div class="card"><h2>Ошибка сервера</h2><pre>%s</pre>'
                                  '</div>' % esc(e)), 500)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/"):
            n = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(n).decode("utf-8") if n else "{}"
            try:
                data = json.loads(raw or "{}")
            except Exception:
                data = {}
            try:
                return self.json_out(do_action(u.path, data))
            except Exception as e:
                import traceback; traceback.print_exc()
                return self.json_out({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)

        n = int(self.headers.get("Content-Length", "0") or 0)
        form = parse_qs(self.rfile.read(n).decode("utf-8")) if n else {}
        flat = {k: v[0] for k, v in form.items()}

        if u.path == "/settings/save":
            for k, v in flat.items():
                if k:
                    s_set(k, v)
            return self.redirect("/settings?ok=1&msg=" + urllib.parse.quote("Настройки сохранены"))
        if u.path == "/settings/testprint":
            text = build_receipt_text(0, "receipt") if False else test_receipt()
            ok, msg = send_to_printer(text, "test", "test")
            c = db()
            c.execute("INSERT INTO print_jobs(kind,order_id,printer,text,status,error,created,done_at) "
                      "VALUES('test',0,?,?,?,?,?,?)",
                      (s_get("printer_mode"), text, "done" if ok else "error",
                       "" if ok else msg, now(), now() if ok else ""))
            c.commit(); c.close()
            return self.redirect("/settings?ok=%d&msg=%s" % (1 if ok else 0, urllib.parse.quote(msg)))
        if u.path == "/settings/reset":
            if os.path.exists(DB_PATH):
                os.remove(DB_PATH)
            for suf in ("-wal", "-shm"):
                if os.path.exists(DB_PATH + suf):
                    os.remove(DB_PATH + suf)
            init_db()
            return self.redirect("/settings?ok=1&msg=" + urllib.parse.quote("Данные сброшены"))
        return self.html(page("Не найдено", "<div class='card'>Не найдено</div>"), 404)

    def redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()


def test_receipt():
    W = max(28, min(64, int(s_get("receipt_width", "42"))))
    L = ["=" * W, _center(s_get("cafe_name", "Кафе"), W), _center("ПРОВЕРКА ПРИНТЕРА", W),
         "=" * W, "Дата: " + datetime.now().strftime("%d.%m.%Y %H:%M"),
         "Режим: " + s_get("printer_mode"),
         "Принтер: " + (s_get("printer_ip") if s_get("printer_mode") == "network" else s_get("printer_windows")),
         "-" * W, _center("Если вы видите этот текст —", W),
         _center("печать настроена верно.", W), "=" * W, "", "", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------
def local_ips():
    import socket
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ips.append(s.getsockname()[0]); s.close()
    except Exception:
        pass
    try:
        for i in socket.gethostbyname_ex(socket.gethostname())[2]:
            if i not in ips and not i.startswith("127."):
                ips.append(i)
    except Exception:
        pass
    return ips


def main():
    init_db()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    ips = local_ips()
    print("=" * 64)
    print("  %s — программа кафе запущена" % s_get("cafe_name", "Кафе"))
    print("=" * 64)
    print("  Касса / компьютер :  http://localhost:%d" % PORT)
    print("  Официант (телефон):  http://%s:%d/waiter" % (ips[0] if ips else "localhost", PORT))
    print("  Кухня             :  http://%s:%d/kitchen" % (ips[0] if ips else "localhost", PORT))
    print("  Касса (оплата)    :  http://%s:%d/cashier" % (ips[0] if ips else "localhost", PORT))
    print("  Меню и залы       :  http://%s:%d/manager" % (ips[0] if ips else "localhost", PORT))
    print("  Настройки/принтер :  http://%s:%d/settings" % (ips[0] if ips else "localhost", PORT))
    print("=" * 64)
    print("  Общий пароль: %s   (PIN официантов: 1111, 2222, 3333)" % s_get("password"))
    print("  Остановить: Ctrl+C")
    print("=" * 64)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()
