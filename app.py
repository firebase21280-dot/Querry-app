"""
Query report app (Flask + SQLite)

Run:
    pip install flask
    python app.py
Open:  http://127.0.0.1:5000        (user)
       http://127.0.0.1:5000/owner  (owner)

Set your own secrets before going live:
    export OWNER_PASSWORD="your-strong-password"
    export SECRET_KEY="any-long-random-string"
"""
import os, sqlite3, uuid
from functools import wraps
from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, send_from_directory, abort, flash)
from jinja2 import DictLoader
from werkzeug.utils import secure_filename

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "uploads")
DB_FILE = os.path.join(BASE, "data.db")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "admin123")
ALLOWED = {"png", "jpg", "jpeg", "webp"}

os.makedirs(UPLOADS, exist_ok=True)
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-please")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB


# ---------------- database ----------------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    c = sqlite3.connect(DB_FILE)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS options(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, contact TEXT,
        opt_name TEXT, price REAL, shot TEXT,
        status TEXT DEFAULT 'pending',
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    """)
    c.commit()
    c.close()


def get_qr():
    r = db().execute("SELECT value FROM settings WHERE key='qr'").fetchone()
    return r["value"] if r else None


def save_image(file):
    """Save an uploaded image safely and return the stored filename (or None)."""
    if not file or not file.filename:
        return None
    ext = secure_filename(file.filename).rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED:
        return None
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOADS, name))
    return name


def owner_only(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get("owner"):
            return redirect(url_for("owner_login"))
        return f(*a, **k)
    return wrap


# ---------------- user routes ----------------
@app.route("/")
def index():
    req = None
    if session.get("req"):
        req = db().execute("SELECT * FROM requests WHERE id=?", (session["req"],)).fetchone()
    options = db().execute("SELECT * FROM options ORDER BY id").fetchall()
    return render_template("index.html", req=req, options=options, qr=get_qr())


@app.route("/submit", methods=["POST"])
def submit():
    name = request.form.get("name", "").strip()
    contact = request.form.get("contact", "").strip()
    opt = db().execute("SELECT * FROM options WHERE id=?", (request.form.get("option"),)).fetchone()
    shot = save_image(request.files.get("screenshot"))

    if not (name and contact and opt):
        flash("Fill in your name, contact and choose an option.")
    elif not shot:
        flash("Upload the payment screenshot (png, jpg, jpeg or webp).")
    else:
        cur = db().execute(
            "INSERT INTO requests(name,contact,opt_name,price,shot) VALUES(?,?,?,?,?)",
            (name, contact, opt["name"], opt["price"], shot))
        db().commit()
        session["req"] = cur.lastrowid
    return redirect(url_for("index"))


@app.route("/new", methods=["POST"])
def new():
    session.pop("req", None)
    return redirect(url_for("index"))


@app.route("/uploads/<name>")
def uploads(name):
    # QR image is public; payment screenshots are visible to the owner only
    if not name.startswith("qr_") and not session.get("owner"):
        abort(403)
    return send_from_directory(UPLOADS, name)


# ---------------- owner routes ----------------
@app.route("/owner/login", methods=["GET", "POST"])
def owner_login():
    if request.method == "POST":
        if request.form.get("password") == OWNER_PASSWORD:
            session["owner"] = True
            return redirect(url_for("owner"))
        flash("Wrong password.")
    return render_template("login.html")


@app.route("/owner/logout")
def owner_logout():
    session.pop("owner", None)
    return redirect(url_for("owner_login"))


@app.route("/owner")
@owner_only
def owner():
    return render_template(
        "owner.html",
        options=db().execute("SELECT * FROM options ORDER BY id").fetchall(),
        reqs=db().execute("SELECT * FROM requests ORDER BY id DESC").fetchall(),
        qr=get_qr())


@app.route("/owner/option/add", methods=["POST"])
@owner_only
def add_option():
    name = request.form.get("name", "").strip()
    try:
        price = float(request.form.get("price", ""))
    except ValueError:
        price = None
    if name and price is not None and price >= 0:
        db().execute("INSERT INTO options(name,price) VALUES(?,?)", (name, price))
        db().commit()
    else:
        flash("Enter a valid option name and amount.")
    return redirect(url_for("owner"))


@app.route("/owner/option/delete/<int:oid>", methods=["POST"])
@owner_only
def delete_option(oid):
    db().execute("DELETE FROM options WHERE id=?", (oid,))
    db().commit()
    return redirect(url_for("owner"))


@app.route("/owner/qr", methods=["POST"])
@owner_only
def upload_qr():
    name = save_image(request.files.get("qr"))
    if name:
        name = "qr_" + name
        os.rename(os.path.join(UPLOADS, name[3:]), os.path.join(UPLOADS, name))
        db().execute("INSERT OR REPLACE INTO settings(key,value) VALUES('qr',?)", (name,))
        db().commit()
    else:
        flash("Upload a valid QR image.")
    return redirect(url_for("owner"))


@app.route("/owner/confirm/<int:rid>", methods=["POST"])
@owner_only
def confirm(rid):
    db().execute("UPDATE requests SET status='confirmed' WHERE id=?", (rid,))
    db().commit()
    return redirect(url_for("owner"))


# ---------------- templates ----------------
app.jinja_loader = DictLoader({
"base.html": """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Query Desk</title>
<style>
:root{--ink:#10231f;--mut:#5d716b;--acc:#0e9f78;--acc2:#0a7a5c;--line:#e0ebe7;--soft:#f3f8f6;--gold:#f2b84b}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;font-family:'Segoe UI',system-ui,-apple-system,Roboto,sans-serif;color:var(--ink);line-height:1.55;
background:radial-gradient(900px 500px at 85% -10%,#15a37c55,transparent 60%),radial-gradient(700px 500px at -10% 110%,#f2b84b22,transparent 60%),linear-gradient(160deg,#041a16,#0a3a30 55%,#0d4a3d);background-attachment:fixed}
header{display:flex;justify-content:space-between;align-items:center;padding:16px 22px;max-width:760px;margin:0 auto}
.brand{display:flex;align-items:center;gap:10px;color:#fff;font-weight:700;font-size:19px;letter-spacing:.2px;text-decoration:none}
.logo{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,var(--acc),#6ee7b7);display:grid;place-items:center;color:#04201b}
nav a{color:#cfe9e1;text-decoration:none;font-size:14px;padding:7px 14px;border-radius:
