"""
MenuEdge - AI Menu Optimizer
===========================
Two-step flow:
  1. /extract  — read menu image, return list of items
  2. /analyse  — take items + optional sales data, return full report + PDF
Auth:
  /signup, /login, /logout, /dashboard
"""

import os, json, base64, re, io, csv, sys, sqlite3, hashlib, secrets
from pathlib import Path
from datetime import datetime
import bcrypt
import stripe

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from flask import Flask, request, jsonify, send_file, render_template_string, redirect, url_for, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from anthropic import Anthropic
from report import generate_pdf

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)
REPORT_DIR = Path("reports"); REPORT_DIR.mkdir(exist_ok=True)
UPLOAD_DIR = Path("uploads"); UPLOAD_DIR.mkdir(exist_ok=True)
DB_PATH = Path("menuiq.db")

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

# ── Subscription plans ────────────────────────────────────────────────────────
PLANS = {
    "starter":        {"name": "Starter",  "price": "$29/mo",   "analyses": 10,
                       "price_id": os.environ.get("STRIPE_PRICE_ID_STARTER", "")},
    "starter-annual": {"name": "Starter",  "price": "$290/yr",  "analyses": 10,
                       "price_id": os.environ.get("STRIPE_PRICE_ID_STARTER_ANNUAL", "")},
    "pro":            {"name": "Pro",      "price": "$79/mo",   "analyses": None,
                       "price_id": os.environ.get("STRIPE_PRICE_ID_PRO", "")},
    "pro-annual":     {"name": "Pro",      "price": "$790/yr",  "analyses": None,
                       "price_id": os.environ.get("STRIPE_PRICE_ID_PRO_ANNUAL", "")},
    "premium":        {"name": "Premium",  "price": "$149/mo",  "analyses": None,
                       "price_id": os.environ.get("STRIPE_PRICE_ID_PREMIUM", "")},
    "premium-annual": {"name": "Premium",  "price": "$1,490/yr","analyses": None,
                       "price_id": os.environ.get("STRIPE_PRICE_ID_PREMIUM_ANNUAL", "")},
    "custom":         {"name": "Custom",   "price": "Custom",   "analyses": None,
                       "price_id": os.environ.get("STRIPE_PRICE_ID_CUSTOM", "")},
}

# ── DB setup ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            restaurant_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS dish_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            dish_name TEXT NOT NULL,
            photo_path TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            plan TEXT DEFAULT 'free',
            status TEXT DEFAULT 'inactive',
            analyses_this_month INTEGER DEFAULT 0,
            month_reset_at TEXT DEFAULT (strftime('%Y-%m-01', 'now')),
            current_period_end TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)

init_db()

# Add extra user profile columns if they don't exist yet (safe to run on existing DB)
def _migrate_db():
    with get_db() as conn:
        for col, typedef in [
            ("cuisine_type", "TEXT DEFAULT ''"),
            ("location",     "TEXT DEFAULT ''"),
            ("phone",        "TEXT DEFAULT ''"),
            ("is_admin",     "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # Column already exists

_migrate_db()

# hash_password is defined below — _seed_admin() is called after it

def _seed_admin():
    """Create or update the admin account from env vars on every startup."""
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@menuedge.com")
    admin_pw    = os.environ.get("ADMIN_PASSWORD", "menuedge-admin-2026")
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email=?", (admin_email,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET password_hash=?, is_admin=1 WHERE email=?",
                (hash_password(admin_pw), admin_email)
            )
            admin_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO users(email, password_hash, restaurant_name, is_admin) VALUES(?,?,?,1)",
                (admin_email, hash_password(admin_pw), "MenuEdge Admin")
            )
            admin_id = cur.lastrowid
        # Ensure subscription row exists (so admin doesn't hit FK issues)
        conn.execute("INSERT OR IGNORE INTO subscriptions(user_id, plan, status) VALUES(?,?,?)",
                     (admin_id, "pro", "active"))

def hash_password(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_password(pw, stored_hash):
    # Support legacy SHA-256 hashes during migration
    if len(stored_hash) == 64 and all(c in '0123456789abcdef' for c in stored_hash):
        return hashlib.sha256(pw.encode()).hexdigest() == stored_hash
    try:
        return bcrypt.checkpw(pw.encode(), stored_hash.encode())
    except Exception:
        return False

_seed_admin()

def current_user_id(): return session.get("user_id")
def is_admin(): return session.get("is_admin", False)
def login_required_redirect(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

# ── Subscription helpers ──────────────────────────────────────────────────────
def get_subscription(user_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None

def ensure_subscription_row(user_id):
    """Create a free-tier subscription row if one doesn't exist yet."""
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subscriptions(user_id) VALUES(?)", (user_id,)
        )

def is_subscribed(user_id):
    sub = get_subscription(user_id)
    return sub and sub["status"] == "active"

def can_analyse(user_id):
    """Returns (allowed: bool, reason: str)."""
    if session.get("is_admin"):
        return True, "ok"
    sub = get_subscription(user_id)
    if not sub or sub["status"] != "active":
        return False, "no_subscription"
    plan = sub.get("plan", "free")
    limit = PLANS.get(plan, {}).get("analyses")
    if limit is None:
        return True, "ok"  # unlimited
    # Reset monthly counter if we've crossed into a new month
    reset_month = (sub.get("month_reset_at") or "")[:7]
    current_month = datetime.utcnow().strftime("%Y-%m")
    if reset_month != current_month:
        with get_db() as conn:
            conn.execute(
                "UPDATE subscriptions SET analyses_this_month=0, month_reset_at=? WHERE user_id=?",
                (datetime.utcnow().strftime("%Y-%m-01"), user_id)
            )
        sub["analyses_this_month"] = 0
    if sub["analyses_this_month"] >= limit:
        return False, "limit_reached"
    return True, "ok"

def increment_analysis_count(user_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE subscriptions SET analyses_this_month = analyses_this_month + 1 WHERE user_id=?",
            (user_id,)
        )

# ── Load HTML files ───────────────────────────────────────────────────────────
def read_html(name):
    p = Path(name)
    return p.read_text(encoding="utf-8") if p.exists() else f"<h1>{name} not found</h1>"

# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route("/signup", methods=["GET","POST"])
@limiter.limit("10 per hour")
def signup_page():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        pw    = request.form.get("password","")
        rname = request.form.get("restaurant_name","").strip()
        if not email or not pw:
            return render_template_string(read_html("auth.html"), error="Email and password required", mode="signup")
        with get_db() as conn:
            existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                return render_template_string(read_html("auth.html"), error="Email already registered", mode="signup")
            cur = conn.execute(
                "INSERT INTO users(email,password_hash,restaurant_name) VALUES(?,?,?)",
                (email, hash_password(pw), rname)
            )
            new_id = cur.lastrowid
            session["user_id"] = new_id
            session["user_email"] = email
            session["restaurant_name"] = rname
            ensure_subscription_row(new_id)
            send_welcome_email(email, rname)
        return redirect(url_for("dashboard"))
    return render_template_string(read_html("auth.html"), error=None, mode="signup")

@app.route("/login", methods=["GET","POST"])
@limiter.limit("10 per minute")
def login_page():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        pw    = request.form.get("password","")
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or not verify_password(pw, user["password_hash"]):
            return render_template_string(read_html("auth.html"), error="Invalid email or password", mode="login")
        # Silently upgrade legacy SHA-256 hash to bcrypt on login
        if len(user["password_hash"]) == 64:
            with get_db() as conn:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (hash_password(pw), user["id"]))
        session["user_id"]        = user["id"]
        session["user_email"]     = user["email"]
        session["restaurant_name"]= user["restaurant_name"] or ""
        session["is_admin"]       = bool(user["is_admin"])
        return redirect(url_for("dashboard"))
    return render_template_string(read_html("auth.html"), error=None, mode="login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ── Password reset ────────────────────────────────────────────────────────────
def send_password_reset_email(to_email, token):
    import requests as _req
    resend_key = os.environ.get("RESEND_API_KEY", "")
    base_url   = os.environ.get("APP_URL", "http://localhost:5002").rstrip("/")
    reset_url  = f"{base_url}/reset-password/{token}"

    if not resend_key:
        print(f"  [!] RESEND_API_KEY not set — reset link: {reset_url}")
        return

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#0D0B09;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0D0B09;padding:48px 0">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#161412;border:1px solid rgba(245,237,216,0.10);border-radius:16px;overflow:hidden">
        <tr><td style="background:linear-gradient(135deg,#271007,#1a0e05);padding:32px 40px;border-bottom:1px solid rgba(245,237,216,0.07)">
          <p style="margin:0;font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:#E8B46A">MENUEDGE</p>
        </td></tr>
        <tr><td style="padding:40px">
          <h1 style="margin:0 0 12px;font-size:24px;font-weight:700;color:#F5EDD8;line-height:1.2">Reset your password</h1>
          <p style="margin:0 0 28px;font-size:15px;color:rgba(245,237,216,0.65);line-height:1.7">
            We received a request to reset your MenuEdge password. Click the button below — this link is valid for <strong style="color:#F5EDD8">1 hour</strong>.
          </p>
          <a href="{reset_url}" style="display:inline-block;padding:14px 28px;background:linear-gradient(135deg,#D4622A,#E8731A);color:#fff;font-size:15px;font-weight:700;text-decoration:none;border-radius:10px;letter-spacing:0.2px">
            Reset Password →
          </a>
          <p style="margin:28px 0 0;font-size:13px;color:rgba(245,237,216,0.40);line-height:1.6">
            If you didn't request this, you can safely ignore this email. Your password will not change.
          </p>
          <hr style="margin:28px 0;border:none;border-top:1px solid rgba(245,237,216,0.07)"/>
          <p style="margin:0;font-size:12px;color:rgba(245,237,216,0.30)">
            Or copy this link: <span style="color:rgba(245,237,216,0.50)">{reset_url}</span>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    try:
        _req.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
            json={
                "from": "MenuEdge <noreply@menuedge.com>",
                "to": [to_email],
                "subject": "Reset your MenuEdge password",
                "html": html_body,
            },
            timeout=10,
        )
    except Exception as e:
        print(f"  [!] Email send failed: {e}")


def send_welcome_email(to_email, restaurant_name):
    import requests as _req
    resend_key = os.environ.get("RESEND_API_KEY", "")
    if not resend_key:
        return
    name = restaurant_name or "there"
    base_url = os.environ.get("APP_URL", "http://localhost:5002").rstrip("/")
    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#0D0B09;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0D0B09;padding:48px 0">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#161412;border:1px solid rgba(245,237,216,0.10);border-radius:16px;overflow:hidden">
        <tr><td style="background:linear-gradient(135deg,#271007,#1a0e05);padding:32px 40px;border-bottom:1px solid rgba(245,237,216,0.07)">
          <p style="margin:0;font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:#E8B46A">MENUEDGE</p>
        </td></tr>
        <tr><td style="padding:40px">
          <h1 style="margin:0 0 12px;font-size:24px;font-weight:700;color:#F5EDD8;line-height:1.2">Welcome to MenuEdge, {name}!</h1>
          <p style="margin:0 0 20px;font-size:15px;color:rgba(245,237,216,0.65);line-height:1.7">
            Your account is ready. Upload your first menu photo and get an AI-powered analysis in under 60 seconds.
          </p>
          <a href="{base_url}/menu" style="display:inline-block;padding:14px 28px;background:linear-gradient(135deg,#D4622A,#E8731A);color:#fff;font-size:15px;font-weight:700;text-decoration:none;border-radius:10px">
            Analyse Your Menu →
          </a>
          <hr style="margin:28px 0;border:none;border-top:1px solid rgba(245,237,216,0.07)"/>
          <p style="margin:0;font-size:13px;color:rgba(245,237,216,0.35);line-height:1.6">
            Questions? Reply to this email — we read every one.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    try:
        _req.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
            json={
                "from": "MenuEdge <hello@menuedge.com>",
                "to": [to_email],
                "subject": "Welcome to MenuEdge — let's optimise your menu",
                "html": html_body,
            },
            timeout=10,
        )
    except Exception as e:
        print(f"  [!] Welcome email failed: {e}")


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def forgot_password():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        from datetime import timedelta
        email = request.form.get("email", "").strip().lower()
        if email:
            with get_db() as conn:
                user = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
                if user:
                    token = secrets.token_urlsafe(32)
                    expires_at = (datetime.utcnow() + timedelta(hours=1)).isoformat()
                    conn.execute(
                        "INSERT INTO password_resets(user_id, token, expires_at) VALUES(?,?,?)",
                        (user["id"], token, expires_at)
                    )
                    send_password_reset_email(email, token)
        # Always show the same page regardless of whether email exists (prevents enumeration)
        return render_template_string(read_html("auth.html"), error=None, mode="forgot_sent")
    return render_template_string(read_html("auth.html"), error=None, mode="forgot")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, user_id, expires_at, used FROM password_resets WHERE token=?",
            (token,)
        ).fetchone()

    if not row or row["used"]:
        return render_template_string(read_html("auth.html"),
            error="This reset link is invalid or has already been used.", mode="login")

    if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        return render_template_string(read_html("auth.html"),
            error="This reset link has expired. Please request a new one.", mode="forgot")

    if request.method == "POST":
        pw  = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if len(pw) < 6:
            return render_template_string(read_html("auth.html"),
                error="Password must be at least 6 characters.", mode="reset", token=token)
        if pw != pw2:
            return render_template_string(read_html("auth.html"),
                error="Passwords do not match.", mode="reset", token=token)
        with get_db() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (hash_password(pw), row["user_id"]))
            conn.execute("UPDATE password_resets SET used=1 WHERE token=?", (token,))
        return render_template_string(read_html("auth.html"), error=None, mode="reset_success")

    return render_template_string(read_html("auth.html"), error=None, mode="reset", token=token)

# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required_redirect
def dashboard():
    uid = current_user_id()
    with get_db() as conn:
        analyses = conn.execute(
            "SELECT id, report_json, created_at FROM analyses WHERE user_id=? ORDER BY created_at DESC",
            (uid,)
        ).fetchall()
        photos = conn.execute(
            "SELECT dish_name, photo_path FROM dish_photos WHERE user_id=?", (uid,)
        ).fetchall()

    # Build stats from all analyses
    # dish_history tracks across all analyses (for future cross-analysis features)
    dish_history = {}
    for a in analyses:
        try:
            rep = json.loads(a["report_json"])
            for item in rep.get("items", []):
                name = item.get("name","")
                if name not in dish_history:
                    dish_history[name] = []
                dish_history[name].append({
                    "classification": item.get("classification",""),
                    "price": item.get("original_price", 0),
                    "date": a["created_at"]
                })
        except Exception:
            pass

    # Counts and top dishes come from the LATEST analysis only (no double-counting)
    latest_items = []
    if analyses:
        try:
            latest_items = json.loads(analyses[0]["report_json"]).get("items", [])
        except Exception:
            pass

    counts = {"Star":0,"Plowhorse":0,"Puzzle":0,"Dog":0}
    for item in latest_items:
        c = item.get("classification","")
        if c in counts:
            counts[c] += 1

    stars   = [i for i in latest_items if i.get("classification")=="Star"]
    dogs    = [i for i in latest_items if i.get("classification")=="Dog"]
    puzzles = [i for i in latest_items if i.get("classification")=="Puzzle"]
    photo_map = {p["dish_name"]: p["photo_path"] for p in photos}

    stats = {
        "total_analyses": len(analyses),
        "total_items_analysed": len(latest_items),
        "counts": counts,
        "stars": stars[:6],
        "dogs": dogs[:6],
        "puzzles": puzzles[:6],
        "latest_report": json.loads(analyses[0]["report_json"]) if analyses else None,
        "photo_map": photo_map,
        "restaurant_name": session.get("restaurant_name","Your Restaurant"),
        "user_email": session.get("user_email",""),
    }

    sub = get_subscription(uid) or {}
    sub_plan   = sub.get("plan", "free")
    sub_status = sub.get("status", "inactive")
    has_pro    = session.get("is_admin") or (sub_status == "active" and sub_plan == "pro")
    return render_template_string(read_html("dashboard.html"), stats=stats, analyses=analyses,
                                  is_admin=session.get("is_admin", False), has_pro=has_pro)

_ALLOWED_IMAGE_SIGNATURES = {
    b'\xff\xd8\xff': '.jpg',
    b'\x89PNG':      '.png',
    b'GIF8':         '.gif',
    b'RIFF':         '.webp',  # WebP starts with RIFF
}

def _validate_image(file_bytes):
    for sig, ext in _ALLOWED_IMAGE_SIGNATURES.items():
        if file_bytes[:len(sig)] == sig:
            return ext
    return None

@app.route("/upload_dish_photo", methods=["POST"])
@login_required_redirect
def upload_dish_photo():
    uid = current_user_id()
    file = request.files.get("photo")
    dish_name = request.form.get("dish_name","").strip()
    if not file or not dish_name:
        return jsonify({"error": "Missing file or dish name"}), 400
    file_bytes = file.read()
    detected_ext = _validate_image(file_bytes)
    if not detected_ext:
        return jsonify({"error": "Invalid file type. Please upload a JPG, PNG, GIF, or WebP image."}), 400
    fname = f"dish_{uid}_{secrets.token_hex(6)}{detected_ext}"
    save_path = UPLOAD_DIR / fname
    save_path.write_bytes(file_bytes)
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO dish_photos(user_id,dish_name,photo_path) VALUES(?,?,?)",
                     (uid, dish_name, f"/uploads/{fname}"))
    return jsonify({"status":"ok","path":f"/uploads/{fname}"})

@app.route("/uploads/<path:fname>")
def serve_upload(fname):
    return send_file(str(UPLOAD_DIR / fname))

@app.route("/static/<path:fname>")
def serve_static(fname):
    return send_file(str(Path("static") / fname))

# ── Main app ──────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template_string(read_html("landing.html"))

@app.route("/home")
def home_public():
    return render_template_string(read_html("landing.html"), logged_in=bool(session.get("user_id")))

@app.route("/menu")
@login_required_redirect
def index():
    gmaps_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    return render_template_string(read_html("index.html"), gmaps_key=gmaps_key)

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    print(f"  [ERROR] {traceback.format_exc()}")
    return jsonify({"error": "An unexpected error occurred. Please try again."}), 500

@app.errorhandler(429)
def rate_limit_handler(e):
    return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429


def prep_image(file_bytes, filename):
    fname = filename.lower()
    if fname.endswith(".pdf"):
        return file_bytes, "application/pdf"
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(file_bytes))
        fmt = img.format or "PNG"
        if max(img.size) > 1600:
            img.thumbnail((1600, 1600), Image.LANCZOS)
        buf = io.BytesIO()
        if fmt == "PNG":
            img.save(buf, format="PNG")
            return buf.getvalue(), "image/png"
        else:
            img.save(buf, format="JPEG", quality=88)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        if fname.endswith(".png"):
            return file_bytes, "image/png"
        return file_bytes, "image/jpeg"


@app.route("/extract", methods=["POST"])
@login_required_redirect
@limiter.limit("30 per hour")
def extract():
    if not client.api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
    file = request.files.get("menu")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400
    file_bytes, media_type = prep_image(file.read(), file.filename)
    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    if media_type == "application/pdf":
        content_block = {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    else:
        content_block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    prompt = """Extract every menu item from this menu image.
Return a JSON array. Each object must have:
{
  "category": "section heading (Starters, Mains, Desserts, Drinks, etc.)",
  "name": "item name",
  "description": "item description or empty string",
  "price": price as a number
}
If price unclear, estimate from context.
Return ONLY the JSON array, no other text."""
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": [content_block, {"type": "text", "text": prompt}]}]
    )
    raw = re.sub(r"^```[a-z]*\n?", "", resp.content[0].text.strip()).rstrip("`").strip()
    try:
        items = json.loads(raw)
    except Exception:
        return jsonify({"error": "Could not read menu items. Try a clearer photo.", "raw": raw}), 422
    if not items:
        return jsonify({"error": "No items found. Try a clearer photo."}), 422
    return jsonify({"status": "ok", "items": items})


@app.route("/analyse", methods=["POST"])
@login_required_redirect
@limiter.limit("20 per hour")
def analyse():
    uid = current_user_id()
    allowed, reason = can_analyse(uid)
    if not allowed:
        if reason == "no_subscription":
            return jsonify({"error": "subscription_required",
                            "message": "An active subscription is required to run analyses."}), 402
        if reason == "limit_reached":
            sub = get_subscription(uid)
            plan = sub.get("plan", "free") if sub else "free"
            limit = PLANS.get(plan, {}).get("analyses", 0)
            return jsonify({"error": "limit_reached",
                            "message": f"You've used all {limit} analyses for this month. Upgrade to Pro for unlimited analyses."}), 402
    if not client.api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
    items_raw = request.form.get("items")
    period    = request.form.get("period", "3 months")
    location  = request.form.get("location", "").strip()
    current_month = datetime.now().strftime("%B")  # e.g. "March"
    if not items_raw:
        return jsonify({"error": "No items provided"}), 400
    try:
        items = json.loads(items_raw)
    except Exception:
        return jsonify({"error": "Invalid items data"}), 400

    has_sales = any(item.get("units_sold") not in (None, "", 0) for item in items)
    sales_file = request.files.get("sales_csv")
    if sales_file and sales_file.filename:
        filename = sales_file.filename.lower()
        sales_map = {}
        try:
            if filename.endswith(".csv"):
                content = sales_file.read().decode("utf-8-sig", errors="replace")
                reader = csv.DictReader(io.StringIO(content))
                for row in reader:
                    name = (row.get("Item") or row.get("item") or row.get("Name") or
                            row.get("name") or row.get("Product") or row.get("product") or "").strip()
                    qty  = (row.get("Qty") or row.get("qty") or row.get("Units") or
                            row.get("units") or row.get("Quantity") or row.get("quantity") or
                            row.get("Count") or row.get("count") or "0").strip()
                    if name:
                        try: sales_map[name.lower()] = int(float(qty))
                        except Exception: pass
            elif filename.endswith((".xlsx", ".xls")):
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(sales_file.read()), read_only=True)
                ws = wb.active
                rows = list(ws.iter_rows(values_only=True))
                if rows:
                    headers = [str(h).strip().lower() if h else "" for h in rows[0]]
                    name_col = next((i for i, h in enumerate(headers) if h in
                                     ["item","name","product","dish","menu item"]), 0)
                    qty_col  = next((i for i, h in enumerate(headers) if h in
                                     ["qty","quantity","units","count","sold","orders"]), 1)
                    for row in rows[1:]:
                        if row and len(row) > max(name_col, qty_col):
                            name = str(row[name_col] or "").strip()
                            qty  = row[qty_col]
                            if name:
                                try: sales_map[name.lower()] = int(float(qty or 0))
                                except Exception: pass
            for item in items:
                key = item["name"].lower()
                if key in sales_map:
                    item["units_sold"] = sales_map[key]
                else:
                    for k, v in sales_map.items():
                        if k in key or key in k:
                            item["units_sold"] = v
                            break
            has_sales = any(item.get("units_sold") for item in items)
        except Exception as e:
            print(f"  [sales csv] parse error: {e}")

    if has_sales:
        sales_context = f"""
IMPORTANT: Real sales data IS provided (period: {period}).
Use the units_sold field for EACH item to determine actual popularity.
- Calculate total units sold across all items
- Items above average sales = High Popularity; below = Low Popularity
- Use this REAL data for Star/Plowhorse/Puzzle/Dog classification
- Include units_sold and popularity_rank in your response for each item
"""
    else:
        sales_context = """
No sales data provided. Estimate popularity based on:
- Item type, price point, description quality
- Mark estimated_popularity as true in response
"""

    seasonal_context = ""
    if location:
        seasonal_context = f"""
SEASONAL CONTEXT:
- Restaurant location: {location}
- Current month: {current_month}
- Use the location to determine the current season (account for hemisphere — e.g. June = winter in South Africa, summer in USA).
- Factor in local seasonal produce, weather, and food culture for this location and cuisine.
- Include "seasonal_suggestions" in your JSON response.
"""
    else:
        seasonal_context = f"""
SEASONAL CONTEXT:
- No location provided. Current month: {current_month}.
- Use cuisine type and current month to make general seasonal suggestions.
- Include "seasonal_suggestions" in your JSON response.
"""

    prompt = f"""You are an expert restaurant consultant specialising in menu engineering.
{sales_context}
{seasonal_context}
Analyse this menu and return a detailed JSON report.

Menu items (with sales data if provided):
{json.dumps(items, indent=2)}

Period of sales data: {period}

Return this exact JSON structure:
{{
  "restaurant_summary": {{
    "total_items": <number>,
    "cuisine_type": "<e.g. Italian|Indian|American|Mexican|Japanese|Continental|Cafe|Fast Food|etc.>",
    "currency_symbol": "<$ or £ or € or ₹ — infer from prices on the menu>",
    "price_range": "<e.g. $4.50 - $22.00>",
    "avg_price": <number>,
    "estimated_avg_food_cost_pct": <number>,
    "overall_health": "<Excellent|Good|Fair|Poor>",
    "headline_insight": "<one powerful sentence about the biggest opportunity>",
    "data_quality": "<Real sales data|Estimated popularity>",
    "analysis_period": "{period}"
  }},
  "items": [
    {{
      "category": "<category>",
      "name": "<name>",
      "original_price": <number>,
      "original_description": "<description>",
      "units_sold": <number or null>,
      "popularity_rank": "<Top 25%|Above Average|Below Average|Bottom 25%>",
      "classification": "<Star|Plowhorse|Puzzle|Dog>",
      "classification_reason": "<1 sentence>",
      "recommended_price": <number>,
      "price_change": "<e.g. +£1.00 or No change>",
      "new_description": "<rewritten appetising 1-sentence description>",
      "action": "<Keep & Promote|Reprice|Rewrite Description|Reposition on Menu|Remove or Redesign>",
      "priority": "<High|Medium|Low>",
      "estimated_monthly_margin_uplift": "<e.g. +£120/month>"
    }}
  ],
  "top_wins": [
    {{
      "title": "<short title>",
      "detail": "<what to do and why — 2 sentences>",
      "estimated_monthly_uplift": "<e.g. +£800/month>"
    }}
  ],
  "layout_recommendations": ["<rec 1>","<rec 2>","<rec 3>"],
  "items_to_remove": ["<name>"],
  "items_to_promote": ["<name>"],
  "best_sellers": ["<name>"],
  "estimated_total_monthly_uplift": "<e.g. £1,200 - £2,400/month>",
  "seasonal_suggestions": {{
    "season": "<e.g. Summer, Monsoon, Winter, Spring — based on location + current month>",
    "location_context": "<1 sentence about the climate/season at this location right now>",
    "add_now": [
      {{
        "dish": "<dish name to add>",
        "reason": "<why it fits this season, cuisine, and location — 1 sentence>",
        "suggested_price": "<e.g. $12>"
      }}
    ],
    "rotate_out": [
      {{
        "dish": "<existing dish name>",
        "reason": "<why it's a poor fit for this season — 1 sentence>"
      }}
    ],
    "seasonal_opportunity": "<1-2 sentences on the biggest seasonal trend or ingredient this restaurant should capitalise on right now>"
  }}
}}

Return ONLY the JSON, no other text."""

    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}]
    )
    raw2 = re.sub(r"^```[a-z]*\n?", "", resp.content[0].text.strip()).rstrip("`").strip()
    try:
        report_data = json.loads(raw2)
    except Exception:
        return jsonify({"error": "Analysis failed. Please try again.", "raw": raw2}), 422

    # Save to DB and track usage
    if uid:
        with get_db() as conn:
            conn.execute("INSERT INTO analyses(user_id,report_json) VALUES(?,?)",
                         (uid, json.dumps(report_data)))
        increment_analysis_count(uid)

    if location:
        report_data.setdefault("restaurant_summary", {})["location"] = location

    pdf_path = REPORT_DIR / f"menuiq_report_user_{uid}.pdf"
    generate_pdf(report_data, str(pdf_path))
    return jsonify({"status": "ok", "report": report_data})


@app.route("/reverse-geocode", methods=["POST"])
@login_required_redirect
def reverse_geocode():
    import requests as _req
    data = request.get_json() or {}
    lat, lng = data.get("lat"), data.get("lng")
    if lat is None or lng is None:
        return jsonify({"error": "lat/lng required"}), 400

    # Try Google Maps Geocoding API first
    gmaps_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if gmaps_key:
        try:
            r = _req.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"latlng": f"{lat},{lng}", "key": gmaps_key},
                timeout=6
            )
            results = r.json().get("results", [])
            for res in results:
                for comp in res.get("address_components", []):
                    if "locality" in comp["types"]:
                        city = comp["long_name"]
                        country = next((c["long_name"] for c in res["address_components"] if "country" in c["types"]), "")
                        return jsonify({"location": f"{city}, {country}" if country else city})
            if results:
                return jsonify({"location": results[0]["formatted_address"]})
        except Exception:
            pass

    # Fallback: OpenStreetMap Nominatim (no API key needed)
    try:
        r = _req.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lng, "format": "json", "zoom": 10, "addressdetails": 1},
            headers={"User-Agent": "MenuEdge/1.0"},
            timeout=6
        )
        addr = r.json().get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county", "")
        country = addr.get("country", "")
        if city:
            return jsonify({"location": f"{city}, {country}" if country else city})
        display = r.json().get("display_name", "")
        if display:
            return jsonify({"location": ", ".join(display.split(",")[:2])})
    except Exception:
        pass

    return jsonify({"error": "Could not resolve location"}), 400


@app.route("/competitive", methods=["POST"])
@login_required_redirect
def competitive():
    if not client.api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    restaurant_name = data.get("restaurant_name", "")
    location        = data.get("location", "")
    cuisine         = data.get("cuisine", "")
    client_items    = data.get("items", [])
    if not location or not cuisine:
        return jsonify({"error": "Please provide location and cuisine type"}), 400
    gmaps_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not gmaps_key:
        return jsonify({"error": "GOOGLE_MAPS_API_KEY not set in .env file"}), 400
    from competitor import run_competitive_analysis
    lat = data.get("lat")
    lng = data.get("lng")
    result = run_competitive_analysis(restaurant_name, location, cuisine, client_items,
                                      lat=float(lat) if lat is not None else None,
                                      lng=float(lng) if lng is not None else None)
    return jsonify(result)


@app.route("/download")
@login_required_redirect
def download():
    uid = current_user_id()
    pdf_path = REPORT_DIR / f"menuiq_report_user_{uid}.pdf"
    if not pdf_path.exists():
        return "No report generated yet", 404
    return send_file(str(pdf_path), as_attachment=True, download_name="MenuEdge_Report.pdf")


@app.route("/report/download/<int:aid>")
@login_required_redirect
def report_download(aid):
    uid = current_user_id()
    with get_db() as conn:
        row = conn.execute(
            "SELECT report_json FROM analyses WHERE id=? AND user_id=?",
            (aid, uid)
        ).fetchone()
    if not row:
        return "Report not found", 404
    report_data = json.loads(row["report_json"])
    pdf_path = REPORT_DIR / f"menuiq_report_{aid}.pdf"
    generate_pdf(report_data, str(pdf_path))
    return send_file(str(pdf_path), as_attachment=True,
                     download_name=f"MenuEdge_Analysis_{aid}.pdf")


@app.route("/dish_insights", methods=["POST"])
@login_required_redirect
def dish_insights():
    if not client.api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    dish         = data.get("dish", {})
    cuisine_type = data.get("cuisine_type", "general")

    prompt = f"""You are an expert restaurant consultant. A dish is underperforming — classified as '{dish.get('classification', 'underperforming')}' on the menu engineering matrix.

Dish details:
- Name: {dish.get('name', '')}
- Category: {dish.get('category', '')}
- Price: {dish.get('price', '')}
- Description: {dish.get('description', '')}
- Classification: {dish.get('classification', '')}
- Units sold: {dish.get('units_sold', 'Unknown')}
- Cuisine type: {cuisine_type}

Diagnose WHY this dish is underperforming and give specific, actionable fixes. Return this exact JSON:
{{
  "root_causes": [
    {{
      "cause": "<specific root cause>",
      "explanation": "<1-2 sentence explanation>",
      "severity": "<High|Medium|Low>"
    }}
  ],
  "fixes": [
    {{
      "action": "<specific fix>",
      "detail": "<how to implement it in 1-2 sentences>",
      "expected_impact": "<what improvement to expect>",
      "effort": "<Easy|Medium|Hard>"
    }}
  ],
  "rewrite": {{
    "name": "<better dish name if the current name is hurting sales>",
    "description": "<rewritten appetising description that sells better>",
    "price": "<suggested price if repricing is recommended>"
  }},
  "benchmark": "<how similar dishes perform in {cuisine_type} cuisine — 1-2 sentences>",
  "verdict": "<one honest sentence — worth saving or cut it?>"
}}

Return ONLY valid JSON, no other text."""

    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = re.sub(r"^```[a-z]*\n?", "", resp.content[0].text.strip()).rstrip("`").strip()
    try:
        result = json.loads(raw)
    except Exception:
        return jsonify({"error": "Analysis failed. Please try again.", "raw": raw}), 422
    return jsonify({"status": "ok", "insights": result})


@app.route("/trend_suggestions", methods=["POST"])
@login_required_redirect
def trend_suggestions():
    if not client.api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    cuisine_type   = data.get("cuisine_type", "")
    existing_items = data.get("existing_items", [])

    if not cuisine_type:
        return jsonify({"error": "cuisine_type is required"}), 400

    existing_names = [i.get("name", "") for i in existing_items if i.get("name")]

    prompt = f"""You are a food trends consultant with deep knowledge of current restaurant industry trends worldwide.

Restaurant cuisine type: {cuisine_type}
Current menu items (already have these): {json.dumps(existing_names)}

Identify 7 trending dishes in {cuisine_type} cuisine that this restaurant does NOT currently offer and should seriously consider adding. Focus on dishes that are gaining real traction in the market right now.

Return this exact JSON:
{{
  "trend_summary": "<2-sentence overview of where {cuisine_type} cuisine is trending right now and what diners are seeking>",
  "suggestions": [
    {{
      "name": "<dish name>",
      "description": "<enticing 1-sentence description>",
      "why_trending": "<specific reason this dish is gaining traction — 1 sentence>",
      "price_range": "<e.g. $12-$18>",
      "target_demographic": "<who orders this>",
      "ease_of_execution": "<Easy|Medium|Hard>",
      "trend_strength": "<Hot|Rising|Emerging>"
    }}
  ],
  "quick_win": "<name of the single easiest dish to add with biggest impact>",
  "avoid": "<1 trend that looks appealing but is actually peaking and should be avoided now>"
}}

Return ONLY valid JSON, no other text."""

    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = re.sub(r"^```[a-z]*\n?", "", resp.content[0].text.strip()).rstrip("`").strip()
    try:
        result = json.loads(raw)
    except Exception:
        return jsonify({"error": "Trend analysis failed. Please try again.", "raw": raw}), 422
    return jsonify({"status": "ok", "trends": result})


@app.route("/seasonal", methods=["POST"])
@login_required_redirect
def seasonal():
    if not client.api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    location     = data.get("location", "").strip()
    cuisine_type = data.get("cuisine_type", "")
    items        = data.get("items", [])
    current_month = datetime.now().strftime("%B")

    if not location:
        return jsonify({"error": "Please provide a restaurant location"}), 400

    item_names = [i.get("name","") for i in items if i.get("name")]

    prompt = f"""You are a culinary consultant specialising in seasonal menu strategy.

Restaurant details:
- Location: {location}
- Cuisine type: {cuisine_type or "not specified"}
- Current month: {current_month}
- Current menu items: {json.dumps(item_names)}

Task: Generate seasonal menu suggestions tailored to this exact location and cuisine.

Important:
- Determine the correct season for {location} in {current_month} (account for hemisphere — June is winter in South Africa, summer in USA/India).
- For "add_now", suggest 3-4 dishes that use peak-season local ingredients or suit the current weather.
- For "rotate_out", identify 2-3 existing dishes that are a poor seasonal fit right now.
- Make suggestions specific to the cuisine type and local food culture, not generic.

Return this exact JSON structure:
{{
  "season": "<season name and months, e.g. Monsoon (June–September)>",
  "location_context": "<1 sentence about the climate/food culture at this location right now>",
  "add_now": [
    {{
      "dish": "<dish name>",
      "reason": "<why it fits this season, location and cuisine — 1 sentence>",
      "suggested_price": "<e.g. $12 or ₹350>"
    }}
  ],
  "rotate_out": [
    {{
      "dish": "<name of existing menu item to rotate out>",
      "reason": "<why it's a poor fit for this season — 1 sentence>"
    }}
  ],
  "seasonal_opportunity": "<1-2 sentences on the single biggest seasonal trend or ingredient this restaurant should capitalise on right now>"
}}

Return ONLY valid JSON, no other text."""

    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = re.sub(r"^```[a-z]*\n?", "", resp.content[0].text.strip()).rstrip("`").strip()
    try:
        result = json.loads(raw)
    except Exception:
        return jsonify({"error": "Failed to generate seasonal suggestions. Please try again."}), 422
    return jsonify({"status": "ok", "seasonal_suggestions": result})


# ── Stripe payments ───────────────────────────────────────────────────────────
@app.route("/subscribe/<plan>")
@login_required_redirect
def subscribe(plan):
    if plan not in PLANS:
        return "Invalid plan", 400
    price_id = PLANS[plan]["price_id"]
    if not price_id:
        return "Stripe price not configured. Set STRIPE_PRICE_ID in .env.", 500
    uid   = current_user_id()
    email = session.get("user_email", "")
    base_url = os.environ.get("APP_URL", "http://localhost:5002").rstrip("/")
    ensure_subscription_row(uid)
    sub = get_subscription(uid)
    customer_id = sub.get("stripe_customer_id") if sub else None
    try:
        if not customer_id:
            customer = stripe.Customer.create(email=email, metadata={"user_id": uid})
            customer_id = customer.id
            with get_db() as conn:
                conn.execute("UPDATE subscriptions SET stripe_customer_id=? WHERE user_id=?",
                             (customer_id, uid))
        checkout = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=f"{base_url}/billing?success=1",
            cancel_url=f"{base_url}/billing?cancelled=1",
            metadata={"user_id": str(uid), "plan": plan},
        )
        return redirect(checkout.url, code=303)
    except stripe.error.StripeError as e:
        print(f"  [Stripe] {e}")
        return "Payment setup failed. Please try again.", 500


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig     = request.headers.get("Stripe-Signature", "")
    secret  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except (stripe.error.SignatureVerificationError, ValueError):
        return "", 400

    obj = event["data"]["object"]

    if event["type"] == "checkout.session.completed":
        user_id = obj.get("metadata", {}).get("user_id")
        plan    = obj.get("metadata", {}).get("plan")
        sub_id  = obj.get("subscription")
        if user_id and plan and sub_id:
            stripe_sub = stripe.Subscription.retrieve(sub_id)
            period_end = datetime.utcfromtimestamp(
                stripe_sub["current_period_end"]).isoformat()
            with get_db() as conn:
                conn.execute("""
                    UPDATE subscriptions
                    SET plan=?, status='active', stripe_subscription_id=?,
                        current_period_end=?, analyses_this_month=0,
                        month_reset_at=?, updated_at=datetime('now')
                    WHERE user_id=?
                """, (plan, sub_id, period_end,
                      datetime.utcnow().strftime("%Y-%m-01"), int(user_id)))

    elif event["type"] in ("customer.subscription.deleted", "customer.subscription.paused"):
        sub_id = obj.get("id")
        with get_db() as conn:
            conn.execute(
                "UPDATE subscriptions SET status='inactive', updated_at=datetime('now') "
                "WHERE stripe_subscription_id=?", (sub_id,)
            )

    elif event["type"] == "invoice.payment_failed":
        sub_id = obj.get("subscription")
        with get_db() as conn:
            conn.execute(
                "UPDATE subscriptions SET status='past_due', updated_at=datetime('now') "
                "WHERE stripe_subscription_id=?", (sub_id,)
            )

    elif event["type"] == "customer.subscription.updated":
        sub_id   = obj.get("id")
        new_plan = None
        for item in obj.get("items", {}).get("data", []):
            price_id = item.get("price", {}).get("id", "")
            for pname, pdata in PLANS.items():
                if pdata["price_id"] == price_id:
                    new_plan = pname
        status = "active" if obj.get("status") == "active" else "inactive"
        period_end = datetime.utcfromtimestamp(
            obj["current_period_end"]).isoformat() if obj.get("current_period_end") else None
        with get_db() as conn:
            if new_plan:
                conn.execute(
                    "UPDATE subscriptions SET plan=?, status=?, current_period_end=?, "
                    "updated_at=datetime('now') WHERE stripe_subscription_id=?",
                    (new_plan, status, period_end, sub_id)
                )
            else:
                conn.execute(
                    "UPDATE subscriptions SET status=?, current_period_end=?, "
                    "updated_at=datetime('now') WHERE stripe_subscription_id=?",
                    (status, period_end, sub_id)
                )

    return "", 200


@app.route("/billing")
@login_required_redirect
def billing():
    uid = current_user_id()
    sub = get_subscription(uid) or {}
    plan      = sub.get("plan", "free")
    status    = sub.get("status", "inactive")
    used      = sub.get("analyses_this_month", 0)
    limit     = PLANS.get(plan, {}).get("analyses")
    period_end = sub.get("current_period_end", "")
    success   = request.args.get("success")
    cancelled = request.args.get("cancelled")

    portal_url = None
    stripe_customer_id = sub.get("stripe_customer_id")
    if stripe_customer_id and stripe.api_key:
        try:
            base_url = os.environ.get("APP_URL", "http://localhost:5002").rstrip("/")
            portal = stripe.billing_portal.Session.create(
                customer=stripe_customer_id,
                return_url=f"{base_url}/billing",
            )
            portal_url = portal.url
        except Exception:
            pass

    billing_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>MenuEdge — Billing</title>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"/>
  <style>
    *,*::before,*::after{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:'Plus Jakarta Sans',sans-serif;background:#0D0B09;color:#F5EDD8;min-height:100vh;padding:48px 24px}}
    .wrap{{max-width:640px;margin:0 auto}}
    .brand{{font-family:'Fraunces',serif;font-size:18px;font-weight:700;letter-spacing:3px;color:#F5EDD8;margin-bottom:40px;display:block;text-decoration:none}}
    h1{{font-family:'Fraunces',serif;font-size:32px;font-weight:700;margin-bottom:8px}}
    .sub{{color:rgba(245,237,216,0.55);font-size:15px;margin-bottom:36px}}
    .card{{background:#161412;border:1px solid rgba(245,237,216,0.09);border-radius:16px;padding:28px 32px;margin-bottom:20px}}
    .label{{font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:rgba(245,237,216,0.4);margin-bottom:6px}}
    .value{{font-size:22px;font-weight:700;color:#F5EDD8}}
    .badge{{display:inline-block;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;letter-spacing:.5px}}
    .badge-active{{background:rgba(26,122,60,0.2);color:#4ade80;border:1px solid rgba(74,222,128,0.3)}}
    .badge-inactive{{background:rgba(212,98,42,0.15);color:#fb923c;border:1px solid rgba(251,146,60,0.3)}}
    .badge-pastdue{{background:rgba(220,38,38,0.15);color:#f87171;border:1px solid rgba(248,113,113,0.3)}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}}
    .btn{{display:inline-block;padding:12px 24px;border-radius:10px;font-size:14px;font-weight:700;text-decoration:none;cursor:pointer;border:none;font-family:inherit}}
    .btn-primary{{background:linear-gradient(135deg,#D4622A,#E8731A);color:#fff;box-shadow:0 4px 20px rgba(212,98,42,0.3)}}
    .btn-outline{{background:transparent;border:1px solid rgba(245,237,216,0.2);color:#F5EDD8}}
    .btn-sm{{padding:8px 18px;font-size:13px}}
    .plans{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}}
    .plan-badge{{display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.5px;margin-bottom:8px;background:rgba(232,180,106,0.15);color:#E8B46A;border:1px solid rgba(232,180,106,0.3)}}
    .toggle-wrap{{display:flex;align-items:center;gap:12px;margin-bottom:24px}}
    .toggle-label{{font-size:13px;font-weight:600;color:rgba(245,237,216,0.6)}}
    .toggle-label.active{{color:#F5EDD8}}
    .toggle{{position:relative;width:44px;height:24px;cursor:pointer}}
    .toggle input{{opacity:0;width:0;height:0}}
    .toggle-slider{{position:absolute;inset:0;background:rgba(245,237,216,0.15);border-radius:99px;transition:.2s}}
    .toggle-slider::before{{content:'';position:absolute;width:18px;height:18px;left:3px;bottom:3px;background:#F5EDD8;border-radius:50%;transition:.2s}}
    .toggle input:checked+.toggle-slider{{background:#D4622A}}
    .toggle input:checked+.toggle-slider::before{{transform:translateX(20px)}}
    .save-badge{{background:rgba(74,222,128,0.15);color:#4ade80;border:1px solid rgba(74,222,128,0.3);font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px}}
    .founding-banner{{background:linear-gradient(135deg,rgba(212,98,42,0.15),rgba(232,180,106,0.1));border:1px solid rgba(232,180,106,0.3);border-radius:12px;padding:14px 18px;margin-bottom:24px;display:flex;align-items:center;justify-content:space-between;gap:12px}}
    .founding-text{{font-size:13px;color:#E8B46A;font-weight:600}}
    .founding-sub{{font-size:12px;color:rgba(245,237,216,0.5);margin-top:2px}}
    .plan-card{{background:#161412;border:1px solid rgba(245,237,216,0.09);border-radius:14px;padding:24px;transition:border-color .2s}}
    .plan-card:hover{{border-color:rgba(232,180,106,0.3)}}
    .plan-name{{font-family:'Fraunces',serif;font-size:20px;font-weight:700;margin-bottom:4px}}
    .plan-price{{font-size:28px;font-weight:700;color:#E8B46A;margin-bottom:8px}}
    .plan-detail{{font-size:13px;color:rgba(245,237,216,0.5);margin-bottom:16px}}
    .alert{{padding:12px 16px;border-radius:10px;font-size:14px;margin-bottom:20px}}
    .alert-success{{background:rgba(26,122,60,0.15);border:1px solid rgba(74,222,128,0.2);color:#4ade80}}
    .alert-info{{background:rgba(232,180,106,0.1);border:1px solid rgba(232,180,106,0.2);color:#E8B46A}}
    .progress-bar{{background:rgba(255,255,255,0.07);border-radius:99px;height:6px;margin-top:8px;overflow:hidden}}
    .progress-fill{{height:100%;background:linear-gradient(90deg,#D4622A,#E8B46A);border-radius:99px;transition:width .4s}}
    a.back{{color:rgba(245,237,216,0.4);font-size:13px;text-decoration:none;margin-bottom:32px;display:inline-block}}
    a.back:hover{{color:#F5EDD8}}
  </style>
</head>
<body>
<div class="wrap">
  <a href="/dashboard" class="brand">MENUEDGE</a>
  <a href="/dashboard" class="back">← Back to Dashboard</a>
  <h1>Billing & Subscription</h1>
  <p class="sub">Manage your plan and payment details.</p>

  {"<div class='alert alert-success'>✓ Subscription activated — you're all set!</div>" if success else ""}
  {"<div class='alert alert-info'>Checkout cancelled. Your plan was not changed.</div>" if cancelled else ""}

  {"<!-- Current plan card -->" if status == "active" else ""}
  {"<div class='card'>" if status == "active" else ""}
  {"<div class='label'>Current Plan</div>" if status == "active" else ""}
  {"<div style='display:flex;align-items:center;gap:12px;margin-bottom:20px'><span class='value'>" + PLANS.get(plan, {}).get('name', plan.title()) + "</span><span class='badge badge-active'>Active</span></div>" if status == "active" else ""}
  {"<div class='label'>Dishes Optimised This Month</div>" if status == "active" else ""}
  {"<div class='value' style='font-size:18px'>" + str(used) + (" / " + str(limit) if limit else " / Unlimited") + "</div>" if status == "active" else ""}
  {"<div class='progress-bar'><div class='progress-fill' style='width:" + (str(round(used/limit*100)) if limit and limit > 0 else "0") + "%'></div></div>" if status == "active" and limit else ""}
  {"<div style='margin-top:20px;display:flex;gap:10px'>" if status == "active" else ""}
  {"<a href='" + portal_url + "' class='btn btn-outline btn-sm'>Manage Billing →</a>" if portal_url and status == "active" else ""}
  {"</div>" if status == "active" else ""}
  {"</div>" if status == "active" else ""}

  {"<!-- Past due -->" if status == "past_due" else ""}
  {"<div class='card' style='border-color:rgba(248,113,113,0.3)'><div class='label'>Subscription Status</div><div style='display:flex;align-items:center;gap:12px;margin-top:6px'><span class='value' style='font-size:18px'>Payment Failed</span><span class='badge badge-pastdue'>Past Due</span></div><p style='color:rgba(245,237,216,0.5);font-size:14px;margin-top:12px'>Your last payment failed. Please update your payment method.</p>" + ("<a href='" + portal_url + "' class='btn btn-primary btn-sm' style='margin-top:16px;display:inline-block'>Update Payment →</a>" if portal_url else "") + "</div>" if status == "past_due" else ""}

  <!-- Founding member banner -->
  <div class="founding-banner">
    <div>
      <div class="founding-text">🎯 Founding Member Offer — Lock in 33% off, forever</div>
      <div class="founding-sub">First 50 customers get permanent discounted pricing. Spots remaining: 47 / 50</div>
    </div>
  </div>

  <!-- Plans -->
  {"<h2 style='font-family:Fraunces,serif;font-size:22px;margin-bottom:16px'>" + ("Upgrade your plan" if status == "active" else "Choose a plan") + "</h2>" }

  <!-- Billing toggle -->
  <div class="toggle-wrap">
    <span class="toggle-label active" id="lbl-mo">Monthly</span>
    <label class="toggle">
      <input type="checkbox" id="billing-toggle" onchange="switchBilling(this.checked)"/>
      <span class="toggle-slider"></span>
    </label>
    <span class="toggle-label" id="lbl-yr">Annual</span>
    <span class="save-badge" id="save-badge" style="display:none">Save 2 months</span>
  </div>

  <div class="plans">
    <div class="plan-card">
      <div class="plan-name">Starter</div>
      <div class="plan-price" id="price-starter">$29<span style="font-size:15px;color:rgba(245,237,216,0.5)">/mo</span></div>
      <div class="plan-detail">Optimise 10 dishes per month — perfect for a single restaurant</div>
      <a id="btn-starter" href="/subscribe/starter" class="btn btn-{"outline" if plan == "starter" and status == "active" else "primary"} btn-sm">{"Current Plan" if plan == "starter" and status == "active" else "Get Started"}</a>
    </div>
    <div class="plan-card" style="border-color:rgba(232,180,106,0.25)">
      <div class="plan-name">Pro</div>
      <div class="plan-price" id="price-pro">$79<span style="font-size:15px;color:rgba(245,237,216,0.5)">/mo</span></div>
      <div class="plan-detail">Optimise unlimited dishes — for growing restaurants &amp; consultants</div>
      <a id="btn-pro" href="/subscribe/pro" class="btn btn-{"outline" if plan == "pro" and status == "active" else "primary"} btn-sm">{"Current Plan" if plan == "pro" and status == "active" else "Upgrade to Pro"}</a>
    </div>
    <div class="plan-card" style="border-color:rgba(180,120,220,0.25)">
      <div class="plan-badge">POPULAR</div>
      <div class="plan-name" style="color:#C084FC">Premium</div>
      <div class="plan-price" id="price-premium" style="color:#C084FC">$149<span style="font-size:15px;color:rgba(245,237,216,0.5)">/mo</span></div>
      <div class="plan-detail">Optimise unlimited dishes + white-label reports + priority support</div>
      <a id="btn-premium" href="/subscribe/premium" class="btn btn-{"outline" if plan == "premium" and status == "active" else "primary"} btn-sm" style="{"background:linear-gradient(135deg,#7C3AED,#C084FC)" if not (plan == "premium" and status == "active") else ""}">{"Current Plan" if plan == "premium" and status == "active" else "Go Premium"}</a>
    </div>
    <div class="plan-card" style="border-color:rgba(245,237,216,0.15)">
      <div class="plan-name">Enterprise</div>
      <div class="plan-price" style="font-size:22px;margin-top:4px">Let's talk</div>
      <div class="plan-detail">Custom pricing, dedicated support, custom integrations &amp; SLA</div>
      <a href="mailto:hello@menuedge.com" class="btn btn-outline btn-sm">Contact Us →</a>
    </div>
  </div>
  <script>
  const monthly = {{
    starter: '$29<span style="font-size:15px;color:rgba(245,237,216,0.5)">/mo</span>',
    pro:     '$79<span style="font-size:15px;color:rgba(245,237,216,0.5)">/mo</span>',
    premium: '$149<span style="font-size:15px;color:rgba(245,237,216,0.5)">/mo</span>'
  }};
  const annual = {{
    starter: '$24<span style="font-size:15px;color:rgba(245,237,216,0.5)">/mo</span><span style="font-size:11px;display:block;margin-top:2px;color:rgba(245,237,216,0.4)">$290 billed annually</span>',
    pro:     '$65<span style="font-size:15px;color:rgba(245,237,216,0.5)">/mo</span><span style="font-size:11px;display:block;margin-top:2px;color:rgba(245,237,216,0.4)">$790 billed annually</span>',
    premium: '$124<span style="font-size:15px;color:rgba(245,237,216,0.5)">/mo</span><span style="font-size:11px;display:block;margin-top:2px;color:rgba(245,237,216,0.4)">$1,490 billed annually</span>'
  }};
  const links = {{monthly:{{starter:'/subscribe/starter',pro:'/subscribe/pro',premium:'/subscribe/premium'}},annual:{{starter:'/subscribe/starter-annual',pro:'/subscribe/pro-annual',premium:'/subscribe/premium-annual'}}}};
  function switchBilling(isAnnual) {{
    ['starter','pro','premium'].forEach(p => {{
      document.getElementById('price-'+p).innerHTML = isAnnual ? annual[p] : monthly[p];
      const btn = document.getElementById('btn-'+p);
      if(btn) btn.href = isAnnual ? links.annual[p] : links.monthly[p];
    }});
    document.getElementById('lbl-mo').classList.toggle('active', !isAnnual);
    document.getElementById('lbl-yr').classList.toggle('active', isAnnual);
    document.getElementById('save-badge').style.display = isAnnual ? 'inline-block' : 'none';
  }}
  </script>
</div>
</body>
</html>"""
    return billing_html


# ── Profile ───────────────────────────────────────────────────────────────────
@app.route("/profile", methods=["GET", "POST"])
@login_required_redirect
def profile():
    uid = current_user_id()
    saved = False
    pw_error = None
    pw_saved = False

    with get_db() as conn:
        user = dict(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
        total_analyses = conn.execute(
            "SELECT COUNT(*) FROM analyses WHERE user_id=?", (uid,)
        ).fetchone()[0]

    sub = get_subscription(uid) or {}

    if request.method == "POST":
        action = request.form.get("action", "profile")

        if action == "profile":
            rname    = request.form.get("restaurant_name", "").strip()
            cuisine  = request.form.get("cuisine_type", "").strip()
            location = request.form.get("location", "").strip()
            phone    = request.form.get("phone", "").strip()
            with get_db() as conn:
                conn.execute(
                    "UPDATE users SET restaurant_name=?, cuisine_type=?, location=?, phone=? WHERE id=?",
                    (rname, cuisine, location, phone, uid)
                )
            session["restaurant_name"] = rname
            user.update(restaurant_name=rname, cuisine_type=cuisine,
                        location=location, phone=phone)
            saved = True

        elif action == "password":
            current_pw  = request.form.get("current_password", "")
            new_pw      = request.form.get("new_password", "")
            confirm_pw  = request.form.get("confirm_password", "")
            if not verify_password(current_pw, user["password_hash"]):
                pw_error = "Current password is incorrect."
            elif len(new_pw) < 6:
                pw_error = "New password must be at least 6 characters."
            elif new_pw != confirm_pw:
                pw_error = "New passwords do not match."
            else:
                with get_db() as conn:
                    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                                 (hash_password(new_pw), uid))
                pw_saved = True

    plan_name   = PLANS.get(sub.get("plan",""), {}).get("name", "No plan")
    plan_status = sub.get("status", "inactive")
    used        = sub.get("analyses_this_month", 0)
    plan_limit  = PLANS.get(sub.get("plan",""), {}).get("analyses")
    member_since = user.get("created_at", "")[:10]

    return render_template_string(read_html("profile.html"),
        user=user,
        total_analyses=total_analyses,
        plan_name=plan_name,
        plan_status=plan_status,
        used=used,
        plan_limit=plan_limit,
        member_since=member_since,
        saved=saved,
        pw_error=pw_error,
        pw_saved=pw_saved,
    )


# ── Delete account ────────────────────────────────────────────────────────────
@app.route("/delete-account", methods=["POST"])
@login_required_redirect
def delete_account():
    uid = current_user_id()

    # Cancel Stripe subscription if active
    sub = get_subscription(uid)
    if sub and sub.get("stripe_subscription_id") and stripe.api_key:
        try:
            stripe.Subscription.cancel(sub["stripe_subscription_id"])
        except Exception as e:
            print(f"  [Stripe] cancel on delete failed: {e}")

    # Delete all user data
    with get_db() as conn:
        conn.execute("DELETE FROM analyses      WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM dish_photos   WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM password_resets WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM subscriptions WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users         WHERE id=?",      (uid,))

    # Remove user-scoped PDF and uploaded photos from disk (best effort)
    try:
        pdf = REPORT_DIR / f"menuiq_report_user_{uid}.pdf"
        if pdf.exists():
            pdf.unlink()
    except Exception:
        pass

    session.clear()
    return redirect(url_for("account_deleted"))


@app.route("/account-deleted")
def account_deleted():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>MenuEdge — Account Deleted</title>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@600;700&family=Plus+Jakarta+Sans:wght@400;600&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{font-family:'Plus Jakarta Sans',sans-serif;background:#0D0B09;color:#F5EDD8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
    .box{text-align:center;max-width:420px}
    .icon{width:64px;height:64px;border-radius:20px;background:rgba(255,255,255,0.05);border:1px solid rgba(245,237,216,0.1);display:flex;align-items:center;justify-content:center;margin:0 auto 28px;color:rgba(245,237,216,0.4)}
    h1{font-family:'Fraunces',serif;font-size:30px;font-weight:700;margin-bottom:12px}
    p{font-size:15px;color:rgba(245,237,216,0.55);line-height:1.7;margin-bottom:32px}
    a{display:inline-block;padding:12px 28px;background:rgba(255,255,255,0.06);border:1px solid rgba(245,237,216,0.12);border-radius:10px;color:#F5EDD8;text-decoration:none;font-size:14px;font-weight:600;transition:all .2s}
    a:hover{background:rgba(255,255,255,0.1)}
  </style>
</head>
<body>
  <div class="box">
    <div class="icon">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
    </div>
    <h1>Account deleted</h1>
    <p>Your account and all associated data have been permanently removed from MenuEdge. We're sorry to see you go.</p>
    <a href="/">Back to home</a>
  </div>
</body>
</html>"""


# ── Legal pages ───────────────────────────────────────────────────────────────
def _legal_page(title, content_html):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>MenuEdge — {title}</title>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@600;700&family=Plus+Jakarta+Sans:wght@400;500;600&display=swap" rel="stylesheet"/>
  <style>
    *,*::before,*::after{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:'Plus Jakarta Sans',sans-serif;background:#0D0B09;color:#F5EDD8;padding:60px 24px;line-height:1.8}}
    .wrap{{max-width:720px;margin:0 auto}}
    .brand{{font-family:'Fraunces',serif;font-size:18px;font-weight:700;letter-spacing:3px;color:#F5EDD8;text-decoration:none;display:block;margin-bottom:48px}}
    h1{{font-family:'Fraunces',serif;font-size:36px;font-weight:700;margin-bottom:8px}}
    .updated{{font-size:13px;color:rgba(245,237,216,0.4);margin-bottom:40px}}
    h2{{font-family:'Fraunces',serif;font-size:20px;font-weight:600;margin:32px 0 10px;color:#E8B46A}}
    p{{font-size:15px;color:rgba(245,237,216,0.7);margin-bottom:14px}}
    ul{{padding-left:20px;margin-bottom:14px}}
    li{{font-size:15px;color:rgba(245,237,216,0.7);margin-bottom:6px}}
    a{{color:#E8B46A}}
    .back{{font-size:13px;color:rgba(245,237,216,0.4);text-decoration:none;display:inline-block;margin-bottom:32px}}
    .back:hover{{color:#F5EDD8}}
  </style>
</head>
<body>
<div class="wrap">
  <a href="/" class="brand">MENUEDGE</a>
  <a href="/" class="back">← Back to home</a>
  <h1>{title}</h1>
  <p class="updated">Last updated: April 2026</p>
  {content_html}
</div>
</body>
</html>"""


@app.route("/terms")
def terms():
    content = """
    <h2>1. Acceptance of Terms</h2>
    <p>By creating an account or using MenuEdge ("Service"), you agree to be bound by these Terms of Service. If you do not agree, do not use the Service.</p>

    <h2>2. Description of Service</h2>
    <p>MenuEdge is an AI-powered menu analysis platform that helps restaurant operators optimise their menus using menu engineering principles, pricing analysis, and trend data.</p>

    <h2>3. Subscriptions and Payments</h2>
    <p>MenuEdge offers paid subscription plans billed monthly. Payments are processed securely by Stripe. By subscribing, you authorise recurring charges to your payment method. You may cancel at any time via the Billing page; your access continues until the end of the current billing period.</p>
    <p>All prices are in USD. We reserve the right to change pricing with 30 days' notice.</p>

    <h2>4. Acceptable Use</h2>
    <p>You agree not to misuse the Service, including but not limited to: uploading malicious files, attempting to reverse-engineer our AI systems, or using the Service to infringe third-party rights.</p>

    <h2>5. Intellectual Property</h2>
    <p>You retain ownership of any menu data you upload. MenuEdge retains ownership of the Service, its code, and generated report formats. AI-generated analysis content is provided for your business use.</p>

    <h2>6. Disclaimer of Warranties</h2>
    <p>The Service is provided "as is." MenuEdge does not guarantee specific revenue outcomes from following AI recommendations. All analysis is advisory in nature.</p>

    <h2>7. Limitation of Liability</h2>
    <p>MenuEdge's liability is limited to the amount you paid in the 3 months preceding any claim. We are not liable for indirect, incidental, or consequential damages.</p>

    <h2>8. Termination</h2>
    <p>We may terminate accounts that violate these terms. You may cancel your account at any time from the Billing page.</p>

    <h2>9. Governing Law</h2>
    <p>These terms are governed by the laws of the applicable jurisdiction. Any disputes shall be resolved through binding arbitration.</p>

    <h2>10. Contact</h2>
    <p>Questions about these terms? Email us at <a href="mailto:legal@menuedge.com">legal@menuedge.com</a>.</p>
    """
    return _legal_page("Terms of Service", content)


@app.route("/privacy")
def privacy():
    content = """
    <h2>1. Information We Collect</h2>
    <ul>
      <li><strong>Account data:</strong> email address, restaurant name, hashed password</li>
      <li><strong>Menu data:</strong> menu images and photos you upload for analysis</li>
      <li><strong>Usage data:</strong> analysis history, report data stored in your account</li>
      <li><strong>Payment data:</strong> processed by Stripe — we never store full card numbers</li>
    </ul>

    <h2>2. How We Use Your Data</h2>
    <ul>
      <li>To provide and improve the MenuEdge analysis service</li>
      <li>To send transactional emails (receipts, password resets, product updates)</li>
      <li>To enforce subscription limits and prevent abuse</li>
    </ul>

    <h2>3. Data Sharing</h2>
    <p>We do not sell your data. We share data only with:</p>
    <ul>
      <li><strong>Anthropic:</strong> menu images are sent to Claude AI for analysis (subject to Anthropic's privacy policy)</li>
      <li><strong>Stripe:</strong> payment processing</li>
      <li><strong>Resend:</strong> transactional email delivery</li>
    </ul>

    <h2>4. Data Retention</h2>
    <p>Your account data and analysis history are retained while your account is active. You may request deletion at any time by emailing <a href="mailto:privacy@menuedge.com">privacy@menuedge.com</a>. Uploaded images are deleted from our servers after processing.</p>

    <h2>5. Security</h2>
    <p>Passwords are hashed using bcrypt. Data is transmitted over TLS. API keys are stored as environment variables and never exposed client-side.</p>

    <h2>6. Cookies</h2>
    <p>We use a single session cookie to keep you logged in. No third-party tracking cookies are used.</p>

    <h2>7. Your Rights</h2>
    <p>You have the right to access, correct, or delete your personal data. Contact <a href="mailto:privacy@menuedge.com">privacy@menuedge.com</a> to exercise these rights.</p>

    <h2>8. Changes</h2>
    <p>We will notify you of material changes to this policy by email or in-app notice.</p>

    <h2>9. Contact</h2>
    <p>Privacy questions: <a href="mailto:privacy@menuedge.com">privacy@menuedge.com</a></p>
    """
    return _legal_page("Privacy Policy", content)


if __name__ == "__main__":
    print("=" * 50)
    print("  MenuEdge - AI Menu Optimizer")
    print("=" * 50)
    print("  Open: http://localhost:5001")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  [!] Set ANTHROPIC_API_KEY in .env")
    print("=" * 50)
    app.run(debug=False, port=5002, threaded=True)
