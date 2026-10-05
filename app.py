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
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Report a Query</title>
<style>
:root{--ink:#14312b;--mut:#5f7770;--acc:#0b7a62;--line:#d3e0db}
*{box-sizing:border-box}body{margin:0;font-family:Georgia,serif;background:#eef3f1;color:var(--ink);line-height:1.5}
header{display:flex;justify-content:space-between;padding:14px 18px;background:var(--ink);color:#fff}
header a{color:#fff;font-size:14px;margin-left:12px}header h1{font-size:18px;margin:0}
main{max-width:520px;margin:0 auto;padding:18px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
h2{font-size:17px;margin:0 0 10px}label{display:block;font-size:14px;color:var(--mut);margin:10px 0 4px}
input,select{width:100%;padding:10px;border:1px solid var(--line);border-radius:6px;font:inherit}
.btn{background:var(--acc);color:#fff;border:0;padding:11px 16px;border-radius:6px;font:inherit;cursor:pointer;margin-top:12px;width:100%}
.btn.sec{background:#fff;color:var(--ink);border:1px solid var(--line)}
.btn.del{background:none;color:#b3261e;border:0;width:auto;margin:0;padding:0}
.btn:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #f5b942;outline-offset:2px}
.qr{text-align:center}.qr img{max-width:230px;width:100%;border:1px solid var(--line);border-radius:8px;padding:8px}
.amt{font-size:22px;font-weight:bold;margin:8px 0}
.msg{padding:14px;border-radius:8px}.ok{background:#e1f4ee;border:1px solid var(--acc);color:#06503f}
.wait{background:#fff3df;border:1px solid #b86a00;color:#6b3d00}.flash{background:#fde7e5;color:#8c1d18;padding:10px;border-radius:6px;margin-bottom:12px}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--line)}
.req{padding:10px 0;border-bottom:1px solid var(--line)}.req img{max-width:100%;max-height:220px;border:1px solid var(--line);border-radius:6px;margin-top:6px}
.tag{font-size:12px;padding:2px 8px;border-radius:99px;background:#fff3df}.tag.confirmed{background:#e1f4ee}
.hide{display:none}small{color:var(--mut)}
</style></head><body>
<header><h1>Report a Query</h1><nav><a href="{{ url_for('index') }}">User</a><a href="{{ url_for('owner') }}">Owner</a></nav></header>
<main>{% for m in get_flashed_messages() %}<div class="flash">{{ m }}</div>{% endfor %}{% block body %}{% endblock %}</main>
</body></html>""",

"index.html": """{% extends 'base.html' %}{% block body %}
{% if req %}
<div class="card"><h2>Your request</h2>
{% if req.status == 'confirmed' %}
  <div class="msg ok"><b>Your query has been reported</b> and action will be taken within 5 days.<br><small>Issue: {{ req.opt_name }}</small></div>
{% else %}
  <div class="msg wait">Screenshot received for <b>{{ req.opt_name }}</b>. Waiting for the owner to confirm your payment.</div>
{% endif %}
<a href="{{ url_for('index') }}"><button class="btn sec" type="button">Check status</button></a>
<form method="post" action="{{ url_for('new') }}"><button class="btn sec">Report another query</button></form></div>
{% else %}
<form method="post" action="{{ url_for('submit') }}" enctype="multipart/form-data">
<div class="card"><h2>1. Choose your issue</h2>
  <label for="name">Your name</label><input id="name" name="name" required>
  <label for="contact">Mobile / email</label><input id="contact" name="contact" required>
  <label for="opt">Option</label>
  <select id="opt" name="option" required>
    <option value="">— Select an option —</option>
    {% for o in options %}<option value="{{ o.id }}" data-price="{{ '%g' % o.price }}">{{ o.name }} — ₹{{ '%g' % o.price }}</option>{% endfor %}
  </select>
  {% if not options %}<small>The owner has not added any options yet.</small>{% endif %}
</div>
<div id="pay" class="card qr hide"><h2>2. Scan and pay</h2>
  {% if qr %}<img src="{{ url_for('uploads', name=qr) }}" alt="Payment QR code">{% else %}<small>Owner has not uploaded a QR code yet.</small>{% endif %}
  <div class="amt" id="amt"></div>
  <button class="btn" type="button" id="paid">I have paid</button></div>
<div id="shot" class="card hide"><h2>3. Upload payment screenshot</h2>
  <input type="file" name="screenshot" accept="image/*">
  <button class="btn">Submit screenshot</button></div>
</form>
<script>
const opt=document.getElementById('opt');
opt.onchange=()=>{const o=opt.selectedOptions[0];
  document.getElementById('pay').classList.toggle('hide',!opt.value);
  document.getElementById('shot').classList.add('hide');
  if(opt.value)document.getElementById('amt').textContent='Pay ₹'+o.dataset.price;};
document.getElementById('paid').onclick=()=>{
  const s=document.getElementById('shot');s.classList.remove('hide');s.scrollIntoView({behavior:'smooth'});};
</script>
{% endif %}{% endblock %}""",

"login.html": """{% extends 'base.html' %}{% block body %}
<form method="post" class="card"><h2>Owner login</h2>
<label for="pw">Password</label><input id="pw" type="password" name="password" required>
<button class="btn">Log in</button></form>{% endblock %}""",

"owner.html": """{% extends 'base.html' %}{% block body %}
<div class="card"><h2>Add option</h2>
<form method="post" action="{{ url_for('add_option') }}">
<label for="n">Option name</label><input id="n" name="name" required>
<label for="p">Amount (₹)</label><input id="p" name="price" type="number" step="any" min="0" required>
<button class="btn">Add option</button></form>
{% for o in options %}<div class="row"><span>{{ o.name }} — ₹{{ '%g' % o.price }}</span>
<form method="post" action="{{ url_for('delete_option', oid=o.id) }}"><button class="btn del">Delete</button></form></div>
{% else %}<small>No options yet.</small>{% endfor %}</div>

<div class="card"><h2>Payment QR code</h2>
{% if qr %}<img src="{{ url_for('uploads', name=qr) }}" style="max-width:160px" alt="Current QR">{% endif %}
<form method="post" action="{{ url_for('upload_qr') }}" enctype="multipart/form-data">
<input type="file" name="qr" accept="image/*" required><button class="btn">Save QR</button></form></div>

<div class="card"><h2>Payment requests</h2>
{% for r in reqs %}<div class="req">
<div class="row" style="border:0;padding:0"><b>{{ r.name }}</b><span class="tag {{ r.status }}">{{ r.status }}</span></div>
<small>{{ r.contact }} · {{ r.opt_name }} · ₹{{ '%g' % r.price }} · {{ r.created }}</small><br>
<img src="{{ url_for('uploads', name=r.shot) }}" alt="Payment screenshot">
{% if r.status == 'pending' %}<form method="post" action="{{ url_for('confirm', rid=r.id) }}"><button class="btn">Confirm payment</button></form>{% endif %}
</div>{% else %}<small>No requests yet.</small>{% endfor %}</div>
<a href="{{ url_for('owner_logout') }}"><button class="btn sec" type="button">Log out</button></a>
{% endblock %}""",
})

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
