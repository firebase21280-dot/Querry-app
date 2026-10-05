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
nav a{color:#cfe9e1;text-decoration:none;font-size:14px;padding:7px 14px;border-radius:99px;margin-left:4px}
nav a:hover,nav a.on{background:#ffffff1f;color:#fff}
main{max-width:560px;margin:0 auto;padding:6px 18px 40px}
.hero{color:#fff;padding:8px 4px 18px}.hero h2{font-size:27px;margin:0 0 6px;line-height:1.2}.hero p{margin:0;color:#b8d8ce;font-size:15px}
.card{background:#fffffff7;border-radius:18px;padding:20px;margin-bottom:16px;box-shadow:0 18px 40px -18px #000a,0 2px 0 #fff inset}
h3{font-size:17px;margin:0 0 12px;display:flex;align-items:center;gap:8px}
label.f{display:block;font-size:13px;font-weight:600;color:var(--mut);margin:12px 0 5px}
input[type=text],input[type=password],input[type=number],input[type=file]{width:100%;padding:12px 14px;border:1.5px solid var(--line);border-radius:11px;font:inherit;background:var(--soft)}
input:focus-visible{outline:3px solid #0e9f7855;border-color:var(--acc)}
.btn{display:block;width:100%;text-align:center;text-decoration:none;background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;border:0;padding:13px 16px;border-radius:12px;font:inherit;font-weight:600;cursor:pointer;margin-top:14px;box-shadow:0 8px 18px -8px #0a7a5c}
.btn:hover{filter:brightness(1.07)}.btn:focus-visible{outline:3px solid var(--gold);outline-offset:2px}
.btn.sec{background:#fff;color:var(--ink);border:1.5px solid var(--line);box-shadow:none}
.btn.del{display:inline;width:auto;background:none;color:#c2362b;box-shadow:none;margin:0;padding:4px 8px;font-size:13px}
.steps{display:flex;gap:6px;margin-bottom:18px}
.st{flex:1;text-align:center;font-size:12px;color:#9cc2b6}
.st i{display:grid;place-items:center;width:30px;height:30px;margin:0 auto 4px;border-radius:50%;background:#ffffff1a;font-style:normal;font-weight:700;color:#fff}
.st.on{color:#fff}.st.on i{background:var(--gold);color:#3a2a00}
.opts{display:grid;gap:10px}
.opt{position:relative;display:block;cursor:pointer}
.opt input{position:absolute;opacity:0}
.opt-in{display:flex;justify-content:space-between;align-items:center;padding:14px;border:1.5px solid var(--line);border-radius:13px;background:var(--soft);transition:.15s}
.opt-in em{font-style:normal;font-weight:700;color:var(--acc2)}
.opt:hover .opt-in{border-color:#9fd8c6}
.opt input:checked+.opt-in{border-color:var(--acc);background:#e4f6ef;box-shadow:0 0 0 3px #0e9f7830}
.opt input:focus-visible+.opt-in{outline:3px solid var(--gold)}
.qr{text-align:center}.qr img.q{width:230px;max-width:100%;border-radius:14px;border:1px solid var(--line);padding:10px;background:#fff}
.amt{font-size:28px;font-weight:800;margin:10px 0 2px;color:var(--acc2)}
.hint{font-size:13px;color:var(--mut)}
.prev{max-width:100%;max-height:200px;border-radius:10px;margin-top:10px;border:1px solid var(--line)}
.status{text-align:center;padding:10px 4px}
.ico{width:68px;height:68px;border-radius:50%;display:grid;place-items:center;margin:0 auto 12px;font-size:32px}
.ico.ok{background:#d9f5ea;color:var(--acc2)}.ico.wait{background:#fff0d1;color:#a86400}
.status h3{justify-content:center;font-size:20px}
.sum{background:var(--soft);border-radius:12px;padding:12px;margin-top:14px;font-size:14px;text-align:left}
.sum div{display:flex;justify-content:space-between;padding:3px 0}
.flash{background:#fde7e5;color:#8c1d18;padding:12px 14px;border-radius:12px;margin-bottom:14px;font-size:14px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px}
.stat{background:#ffffff14;border:1px solid #ffffff22;border-radius:14px;padding:14px;color:#fff;text-align:center}
.stat b{display:block;font-size:26px}.stat span{font-size:12px;color:#b8d8ce}
.row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--line);gap:8px}
.row:last-child{border:0}
.req{padding:14px 0;border-bottom:1px solid var(--line)}.req:last-child{border:0}
.req img{max-width:100%;max-height:230px;border-radius:10px;margin-top:8px;border:1px solid var(--line)}
.tag{font-size:12px;font-weight:600;padding:3px 10px;border-radius:99px;background:#fff0d1;color:#8a5200}
.tag.confirmed{background:#d9f5ea;color:#066247}
.hide{display:none}small{color:var(--mut)}
footer{text-align:center;color:#8fb8ab;font-size:12px;padding-bottom:24px}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>
<header>
<a class="brand" href="{{ url_for('index') }}"><span class="logo"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg></span>Query Desk</a>
<nav><a href="{{ url_for('index') }}" class="{{ 'on' if request.endpoint == 'index' }}">Report</a><a href="{{ url_for('owner') }}" class="{{ 'on' if request.endpoint in ('owner','owner_login') }}">Owner</a></nav>
</header>
<main>{% for m in get_flashed_messages() %}<div class="flash">{{ m }}</div>{% endfor %}{% block body %}{% endblock %}</main>
<footer>Secure payments · Reviewed by the owner</footer>
</body></html>""",

"index.html": """{% extends 'base.html' %}{% block body %}
<div class="hero"><h2>Report your query</h2><p>Choose an issue, pay, upload proof and we will take action within 5 days.</p></div>
<div class="steps">
<div class="st on" id="s1"><i>1</i>Choose</div>
<div class="st {{ 'on' if req }}" id="s2"><i>2</i>Pay</div>
<div class="st {{ 'on' if req }}" id="s3"><i>3</i>Upload</div>
</div>
{% if req %}
<div class="card status">
{% if req.status == 'confirmed' %}
  <div class="ico ok">&#10003;</div><h3>Your query has been reported</h3>
  <p>Action will be taken within <b>5 days</b>.</p>
{% else %}
  <div class="ico wait">&#9203;</div><h3>Payment under review</h3>
  <p>We received your screenshot. The owner will confirm it shortly.</p>
{% endif %}
  <div class="sum"><div><span>Issue</span><b>{{ req.opt_name }}</b></div><div><span>Amount</span><b>&#8377;{{ '%g' % req.price }}</b></div><div><span>Name</span><b>{{ req.name }}</b></div></div>
  <a class="btn sec" href="{{ url_for('index') }}">Check status</a>
  <form method="post" action="{{ url_for('new') }}"><button class="btn sec">Report another query</button></form>
</div>
{% else %}
<form method="post" action="{{ url_for('submit') }}" enctype="multipart/form-data">
<div class="card"><h3>Your details</h3>
  <label class="f" for="name">Full name</label><input type="text" id="name" name="name" required>
  <label class="f" for="contact">Mobile or email</label><input type="text" id="contact" name="contact" required>
  <label class="f" style="margin-top:18px">Select your issue</label>
  <div class="opts">
  {% for o in options %}<label class="opt"><input type="radio" name="option" value="{{ o.id }}" data-price="{{ '%g' % o.price }}" required><span class="opt-in"><b>{{ o.name }}</b><em>&#8377;{{ '%g' % o.price }}</em></span></label>
  {% else %}<small>The owner has not added any options yet.</small>{% endfor %}
  </div>
</div>
<div id="pay" class="card qr hide"><h3 style="justify-content:center">Scan and pay</h3>
  {% if qr %}<img class="q" src="{{ url_for('uploads', name=qr) }}" alt="Payment QR code">{% else %}<small>Owner has not uploaded a QR code yet.</small>{% endif %}
  <div class="amt" id="amt"></div><div class="hint">Pay with any UPI app, then come back here.</div>
  <button class="btn" type="button" id="paid">I have paid</button></div>
<div id="shot" class="card hide"><h3>Upload payment screenshot</h3>
  <input type="file" name="screenshot" id="file" accept="image/*" required>
  <img id="prev" class="prev hide" alt="Screenshot preview">
  <button class="btn">Submit screenshot</button></div>
</form>
<script>
const $=id=>document.getElementById(id);
document.querySelectorAll('input[name=option]').forEach(r=>r.onchange=()=>{
  $('amt').textContent='Pay \u20b9'+r.dataset.price;
  $('pay').classList.remove('hide');$('shot').classList.add('hide');
  $('s2').classList.add('on');$('s3').classList.remove('on');
  $('pay').scrollIntoView({behavior:'smooth'});});
$('paid').onclick=()=>{$('shot').classList.remove('hide');$('s3').classList.add('on');$('shot').scrollIntoView({behavior:'smooth'});};
$('file').onchange=e=>{const f=e.target.files[0];if(!f)return;const r=new FileReader();
  r.onload=()=>{$('prev').src=r.result;$('prev').classList.remove('hide');};r.readAsDataURL(f);};
</script>
{% endif %}{% endblock %}""",

"login.html": """{% extends 'base.html' %}{% block body %}
<div class="hero"><h2>Owner login</h2><p>Sign in to manage options and confirm payments.</p></div>
<form method="post" class="card"><label class="f" for="pw">Password</label>
<input id="pw" type="password" name="password" required><button class="btn">Log in</button></form>{% endblock %}""",

"owner.html": """{% extends 'base.html' %}{% block body %}
<div class="hero"><h2>Owner dashboard</h2><p>Manage options, your QR code and payment requests.</p></div>
<div class="stats">
<div class="stat"><b>{{ reqs|length }}</b><span>Total</span></div>
<div class="stat"><b>{{ reqs|selectattr('status','equalto','pending')|list|length }}</b><span>Pending</span></div>
<div class="stat"><b>{{ reqs|selectattr('status','equalto','confirmed')|list|length }}</b><span>Confirmed</span></div>
</div>

<div class="card"><h3>Options</h3>
<form method="post" action="{{ url_for('add_option') }}">
<label class="f" for="n">Option name</label><input type="text" id="n" name="name" required>
<label class="f" for="p">Amount (&#8377;)</label><input id="p" name="price" type="number" step="any" min="0" required>
<button class="btn">Add option</button></form>
<div style="margin-top:12px">
{% for o in options %}<div class="row"><span>{{ o.name }} &mdash; <b>&#8377;{{ '%g' % o.price }}</b></span>
<form method="post" action="{{ url_for('delete_option', oid=o.id) }}"><button class="btn del">Delete</button></form></div>
{% else %}<small>No options yet. Add your first one above.</small>{% endfor %}</div></div>

<div class="card qr"><h3 style="justify-content:center">Payment QR code</h3>
{% if qr %}<img class="q" src="{{ url_for('uploads', name=qr) }}" alt="Current QR" style="width:170px;margin-bottom:10px">{% endif %}
<form method="post" action="{{ url_for('upload_qr') }}" enctype="multipart/form-data">
<input type="file" name="qr" accept="image/*" required><button class="btn">Save QR</button></form></div>

<div class="card"><h3>Payment requests</h3>
{% for r in reqs %}<div class="req">
<div class="row" style="border:0;padding:0"><b>{{ r.name }}</b><span class="tag {{ r.status }}">{{ r.status|capitalize }}</span></div>
<small>{{ r.contact }} &middot; {{ r.opt_name }} &middot; &#8377;{{ '%g' % r.price }} &middot; {{ r.created }}</small><br>
<a href="{{ url_for('uploads', name=r.shot) }}" target="_blank"><img src="{{ url_for('uploads', name=r.shot) }}" alt="Payment screenshot"></a>
{% if r.status == 'pending' %}<form method="post" action="{{ url_for('confirm', rid=r.id) }}"><button class="btn">Confirm payment</button></form>{% endif %}
</div>{% else %}<small>No requests yet.</small>{% endfor %}</div>
<a class="btn sec" href="{{ url_for('owner_logout') }}">Log out</a>
{% endblock %}""",
})

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
    
