import calendar
import hmac
import json
import math
import os
import secrets
import tempfile
import threading
import uuid
from datetime import date
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

DATA_DIR = Path(os.environ.get("NJORD_DATA_DIR", Path(__file__).resolve().parents[2] / "instance"))
DATA_FILE = DATA_DIR / "data.json"
USERS_FILE = DATA_DIR / "users.json"
SECRET_FILE = DATA_DIR / ".secret_key"

CATEGORIES = {
    "Income": ["Salary", "Side income", "Benefits", "Other income"],
    "Expenses": [
        "Housing", "Utilities", "Food", "Transport", "Insurance", "Loans & debt",
        "Subscriptions", "Health", "Savings", "Leisure", "Other",
    ],
}
ALL_CATEGORIES = {c for group in CATEGORIES.values() for c in group}

_lock = threading.Lock()


def _write_json(path, payload):
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def init_storage():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        _write_json(USERS_FILE, {"admin": "admin"})
    if not DATA_FILE.exists():
        _write_json(DATA_FILE, {"currency": "NOK", "items": []})


def load_data():
    return _read_json(DATA_FILE, {"currency": "NOK", "items": []})


def _valid_import_number(value, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0 \
            and (maximum is None or value <= maximum)
    except OverflowError:
        return False


def validate_import_data(data):
    if not isinstance(data, dict):
        return "The JSON root must be an object."
    currency = data.get("currency")
    if not isinstance(currency, str) or not currency.strip() or len(currency) > 10:
        return "Currency must be a non-empty string of at most 10 characters."

    assets = data.get("assets", [])
    if not isinstance(assets, list):
        return "Assets must be a list."
    asset_ids = set()
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("id"), str) \
                or not asset["id"] or not isinstance(asset.get("name"), str) \
                or not asset["name"].strip() or asset["id"] in asset_ids:
            return "Each asset must have a unique ID and a non-empty name."
        asset_ids.add(asset["id"])

    items = data.get("items")
    if not isinstance(items, list):
        return "Items must be a list."
    item_ids = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) \
                or not item["id"] or item["id"] in item_ids:
            return "Each item must have a unique ID."
        item_ids.add(item["id"])
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            return "Each item must have a non-empty name."
        if item.get("type") not in ("income", "expense"):
            return "Each item's type must be income or expense."
        amount = item.get("amount")
        if not _valid_import_number(amount):
            return "Each item's amount must be a non-negative number."
        category = item.get("category", "")
        if not isinstance(category, str) or (category and category not in ALL_CATEGORIES):
            return "Each item's category must be a known category or empty."
        asset_id = item.get("asset_id")
        if asset_id is not None and (not isinstance(asset_id, str) or asset_id not in asset_ids):
            return "Each item's asset must refer to an existing asset."

    loans = data.get("loans", [])
    if not isinstance(loans, list):
        return "Loans must be a list."
    loan_ids = set()
    for loan in loans:
        if not isinstance(loan, dict) or not isinstance(loan.get("id"), str) \
                or not loan["id"] or loan["id"] in loan_ids:
            return "Each loan must have a unique ID."
        loan_ids.add(loan["id"])
        if not isinstance(loan.get("name"), str) or not loan["name"].strip():
            return "Each loan must have a non-empty name."
        for field in ("balance", "rate", "payment"):
            value = loan.get(field)
            maximum = 100 if field == "rate" else math.inf
            if not _valid_import_number(value, maximum):
                return f"Each loan's {field} must be a valid non-negative number."
        fee = loan.get("fee", 0)
        if not _valid_import_number(fee, loan["payment"]):
            return "Each loan's fee must be between 0 and its monthly payment."
        asset_id = loan.get("asset_id")
        if asset_id is not None and (not isinstance(asset_id, str) or asset_id not in asset_ids):
            return "Each loan's asset must refer to an existing asset."
    return None


def get_secret_key():
    env = os.environ.get("NJORD_SECRET_KEY")
    if env:
        return env
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
    return SECRET_FILE.read_text().strip()


def parse_item(form, asset_ids):
    name = form.get("name", "").strip()
    kind = form.get("type", "")
    category = form.get("category", "").strip()
    asset_id = form.get("asset_id", "").strip()
    try:
        amount = round(float(form.get("amount", "").replace(",", ".").replace(" ", "")), 2)
    except ValueError:
        return None, "Amount must be a number."
    if not name:
        return None, "Name is required."
    if kind not in ("income", "expense"):
        return None, "Type must be income or expense."
    if amount < 0:
        return None, "Amount can't be negative."
    if category and category not in ALL_CATEGORIES:
        return None, "Unknown category."
    if asset_id and asset_id not in asset_ids:
        return None, "Unknown asset."
    return {"name": name, "type": kind, "amount": amount, "category": category,
            "asset_id": asset_id or None}, None


def _number(form, field, label, minimum=0.0, maximum=None):
    raw = form.get(field, "").replace(",", ".").replace(" ", "").strip()
    if raw == "":
        raw = "0"
    try:
        value = float(raw)
    except ValueError:
        return None, f"{label} must be a number."
    if value < minimum or (maximum is not None and value > maximum):
        return None, f"{label} must be between {minimum:g} and {maximum:g}." if maximum is not None \
            else f"{label} can't be less than {minimum:g}."
    return round(value, 4), None


def parse_loan(form, asset_ids):
    name = form.get("name", "").strip()
    asset_id = form.get("asset_id", "").strip()
    if not name:
        return None, "Name is required."
    values = {}
    for field, label, kwargs in (
            ("balance", "Remaining balance", {}),
            ("rate", "Interest rate", {"maximum": 100.0}),
            ("payment", "Monthly payment", {}),
            ("fee", "Monthly fee", {})):
        values[field], error = _number(form, field, label, **kwargs)
        if error:
            return None, error
    if values["fee"] > values["payment"]:
        return None, "Monthly fee can't be larger than the payment."
    if asset_id and asset_id not in asset_ids:
        return None, "Unknown asset."
    return {"name": name, "asset_id": asset_id or None, **values}, None


def _add_months(today, months):
    index = today.month - 1 + months
    return today.year + index // 12, index % 12 + 1


def loan_schedule(loan, today=None):
    """Month-by-month payoff rows, or [] if the loan is paid or never ends."""
    today = today or date.today()
    balance, fee = loan["balance"], loan.get("fee", 0.0)
    monthly_rate = loan["rate"] / 100 / 12
    principal_part = loan["payment"] - fee
    if balance <= 0.005 or principal_part <= balance * monthly_rate + 1e-9:
        return []
    rows = []
    while balance > 0.005 and len(rows) < 1200:
        interest = balance * monthly_rate
        if principal_part >= balance + interest:
            principal = balance
            pay = balance + interest
        else:
            pay = principal_part
            principal = pay - interest
        balance = max(balance - principal, 0.0)
        year, month = _add_months(today, len(rows) + 1)
        rows.append({"n": len(rows) + 1, "year": year, "month": month,
                     "label": f"{calendar.month_name[month]} {year}",
                     "payment": pay + fee, "interest": interest, "fee": fee,
                     "principal": principal, "balance": balance})
    return rows


def loan_calc(loan, today=None):
    if loan["balance"] <= 0.005:
        return {"status": "paid"}
    monthly_rate = loan["rate"] / 100 / 12
    rows = loan_schedule(loan, today)
    if not rows or rows[-1]["balance"] > 0.005:
        return {"status": "never",
                "min_payment": loan["balance"] * monthly_rate + loan.get("fee", 0.0)}
    months = len(rows)
    return {"status": "ok", "months": months,
            "interest": sum(r["interest"] for r in rows),
            "total": sum(r["payment"] for r in rows),
            "last_payment": rows[-1]["payment"],
            "payoff": rows[-1]["label"],
            "years": months // 12, "rem_months": months % 12}


def loan_expenses(loans):
    return [{"name": l["name"], "type": "expense", "amount": l["payment"],
             "category": "Loans & debt", "asset_id": l.get("asset_id"), "loan": True}
            for l in loans if l["balance"] > 0.005]


def _key_fn(mode, assets):
    if mode == "asset":
        names = {a["id"]: a["name"] for a in assets}
        return lambda i: names.get(i.get("asset_id"), "No asset")
    return lambda i: i.get("category") or "No category"


PIE_COLORS = ["#1f6fb2", "#e07a1f", "#2e9e5b", "#b3261e", "#7b5ea7", "#c9a227",
              "#2aa5a5", "#d6578a", "#6c7a89", "#8bb34a", "#4b6cb7", "#a0522d"]


def pie_slices(rows, mode, assets, cx=100, cy=100, r=90):
    key = _key_fn(mode, assets)
    totals = {}
    for i in rows:
        if i["amount"] > 0:
            totals[key(i)] = totals.get(key(i), 0) + i["amount"]
    grand = sum(totals.values())
    ordered = sorted(totals.items(), key=lambda kv: (kv[0].startswith("No "), -kv[1]))
    slices, angle, color_idx = [], -math.pi / 2, 0
    for label, total in ordered:
        if label.startswith("No "):
            color = "#b8c0c8"
        else:
            color = PIE_COLORS[color_idx % len(PIE_COLORS)]
            color_idx += 1
        frac = total / grand
        end = angle + frac * 2 * math.pi
        if frac > 0.9999:
            path = None
        else:
            x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
            x2, y2 = cx + r * math.cos(end), cy + r * math.sin(end)
            large = 1 if frac > 0.5 else 0
            path = f"M{cx},{cy} L{x1:.2f},{y1:.2f} A{r},{r} 0 {large} 1 {x2:.2f},{y2:.2f} Z"
        slices.append({"label": label, "total": total, "pct": frac * 100,
                       "color": color, "path": path})
        angle = end
    return slices


def group_items(rows, mode, assets):
    if mode not in ("asset", "category"):
        return [{"label": None, "rows": rows, "total": sum(i["amount"] for i in rows)}]
    key = _key_fn(mode, assets)
    groups = {}
    for i in rows:
        groups.setdefault(key(i), []).append(i)
    return [{"label": label, "rows": rs, "total": sum(i["amount"] for i in rs)}
            for label, rs in sorted(groups.items(), key=lambda kv: (kv[0].startswith("No "), kv[0]))]


def create_app():
    init_storage()
    app = Flask(__name__)
    app.secret_key = get_secret_key()
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user"):
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    @app.template_filter("money")
    def money(value):
        return f"{value:,.2f}".replace(",", " ")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            users = _read_json(USERS_FILE, {})
            expected = users.get(username)
            if expected is not None and hmac.compare_digest(
                    str(expected).encode(), password.encode()):
                session["user"] = username
                return redirect(url_for("overview"))
            flash("Wrong username or password.")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def overview():
        data = load_data()
        assets = data.get("assets", [])
        mode = request.args.get("group", "none")
        if mode not in ("none", "asset", "category"):
            mode = "none"
        items = data["items"]
        loans = data.get("loans", [])
        income = [i for i in items if i["type"] == "income"]
        expenses = [i for i in items if i["type"] == "expense"] + loan_expenses(loans)
        total_in = sum(i["amount"] for i in income)
        total_out = sum(i["amount"] for i in expenses)
        return render_template(
            "overview.html", currency=data["currency"], mode=mode,
            income=group_items(income, mode, assets),
            expenses=group_items(expenses, mode, assets),
            asset_names={a["id"]: a["name"] for a in assets},
            loans=[(l, loan_calc(l)) for l in loans],
            pie_asset=pie_slices(expenses, "asset", assets),
            pie_category=pie_slices(expenses, "category", assets),
            total_in=total_in, total_out=total_out, left=total_in - total_out)

    @app.get("/options")
    @login_required
    def options():
        data = load_data()
        return render_template("options.html", currency=data["currency"],
                               assets=data.get("assets", []))

    @app.get("/options/data")
    @login_required
    def download_data():
        return send_file(DATA_FILE, as_attachment=True, download_name="data.json",
                         mimetype="application/json")

    @app.post("/options/data")
    @login_required
    def upload_data():
        request.max_content_length = 2 * 1024 * 1024 + 64 * 1024
        uploaded = request.files.get("data_file")
        if uploaded is None or not uploaded.filename:
            flash("Choose a data.json file to upload.")
            return redirect(url_for("options"))
        raw = uploaded.stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            flash("The file is too large. Maximum size is 2 MB.")
            return redirect(url_for("options"))
        try:
            imported = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            flash("The uploaded file must contain valid UTF-8 JSON.")
            return redirect(url_for("options"))
        error = validate_import_data(imported)
        if error:
            flash(f"Invalid data.json: {error}")
            return redirect(url_for("options"))
        with _lock:
            _write_json(DATA_FILE, imported)
        flash("Data imported successfully.")
        return redirect(url_for("options"))

    @app.post("/options")
    @login_required
    def save_options():
        currency = request.form.get("currency", "").strip()
        if not currency or len(currency) > 10:
            flash("Currency must be 1-10 characters.")
            return redirect(url_for("options"))
        with _lock:
            data = load_data()
            data["currency"] = currency
            _write_json(DATA_FILE, data)
        return redirect(url_for("options"))

    @app.post("/assets")
    @login_required
    def add_asset():
        name = request.form.get("name", "").strip()
        if not name or len(name) > 50:
            flash("Asset name must be 1-50 characters.")
            return redirect(url_for("options"))
        with _lock:
            data = load_data()
            data.setdefault("assets", []).append({"id": uuid.uuid4().hex, "name": name})
            _write_json(DATA_FILE, data)
        return redirect(url_for("options"))

    @app.post("/assets/<asset_id>")
    @login_required
    def rename_asset(asset_id):
        name = request.form.get("name", "").strip()
        if not name or len(name) > 50:
            flash("Asset name must be 1-50 characters.")
            return redirect(url_for("options"))
        with _lock:
            data = load_data()
            for asset in data.get("assets", []):
                if asset["id"] == asset_id:
                    asset["name"] = name
                    break
            else:
                abort(404)
            _write_json(DATA_FILE, data)
        return redirect(url_for("options"))

    @app.post("/assets/<asset_id>/delete")
    @login_required
    def delete_asset(asset_id):
        with _lock:
            data = load_data()
            data["assets"] = [a for a in data.get("assets", []) if a["id"] != asset_id]
            for item in data["items"] + data.get("loans", []):
                if item.get("asset_id") == asset_id:
                    item["asset_id"] = None
            _write_json(DATA_FILE, data)
        return redirect(url_for("options"))

    @app.get("/loans")
    @login_required
    def loans():
        data = load_data()
        rows = [(l, loan_calc(l)) for l in data.get("loans", [])]
        return render_template("loans.html", loans=rows, currency=data["currency"],
                               assets=data.get("assets", []))

    @app.post("/loans")
    @login_required
    def add_loan():
        with _lock:
            data = load_data()
            assets = data.get("assets", [])
            loan, error = parse_loan(request.form, {a["id"] for a in assets})
            if error:
                return jsonify(ok=False, error=error), 400
            loan["id"] = uuid.uuid4().hex
            data.setdefault("loans", []).append(loan)
            _write_json(DATA_FILE, data)
        html = render_template("_loan_row_fragment.html", l=loan, c=loan_calc(loan),
                               assets=assets, currency=data["currency"])
        return jsonify(ok=True, html=html)

    @app.get("/payoff")
    @login_required
    def payoff():
        data = load_data()
        today = date.today()
        loans = [l for l in data.get("loans", []) if l["balance"] > 0.005]
        schedules, skipped = [], []
        for l in loans:
            rows = loan_schedule(l, today)
            if rows and rows[-1]["balance"] <= 0.005:
                schedules.append((l, rows))
            else:
                skipped.append(l["name"])
        length = max((len(r) for _, r in schedules), default=0)
        months = []
        for n in range(length):
            year, month = _add_months(today, n + 1)
            cells = [r[n] if n < len(r) else None for _, r in schedules]
            months.append({
                "label": f"{calendar.month_name[month]} {year}", "year": year,
                "balances": [c["balance"] if c else 0.0 for c in cells],
                "payment": sum(c["payment"] for c in cells if c),
                "interest": sum(c["interest"] for c in cells if c),
                "balance": sum(c["balance"] for c in cells if c)})
        years = {}
        for m_ in months:
            y = years.setdefault(m_["year"], {"year": m_["year"], "rows": [],
                                              "paid": 0.0, "interest": 0.0})
            y["rows"].append(m_)
            y["paid"] += m_["payment"]
            y["interest"] += m_["interest"]
        for y in years.values():
            y["end"] = y["rows"][-1]
        return render_template(
            "payoff.html", loans=[l for l, _ in schedules], years=list(years.values()),
            start_balance=sum(l["balance"] for l, _ in schedules),
            total_interest=sum(m_["interest"] for m_ in months),
            total_paid=sum(m_["payment"] for m_ in months),
            months=len(months), skipped=skipped, currency=data["currency"],
            last=months[-1]["label"] if months else None)

    @app.get("/loans/<loan_id>")
    @login_required
    def loan_detail(loan_id):
        data = load_data()
        loan = next((l for l in data.get("loans", []) if l["id"] == loan_id), None)
        if loan is None:
            abort(404)
        rows = loan_schedule(loan)
        years = {}
        for r in rows:
            y = years.setdefault(r["year"], {"year": r["year"], "rows": [], "interest": 0.0,
                                             "paid": 0.0})
            y["rows"].append(r)
            y["interest"] += r["interest"]
            y["paid"] += r["payment"]
        for y in years.values():
            y["end_balance"] = y["rows"][-1]["balance"]
        asset = next((a["name"] for a in data.get("assets", [])
                      if a["id"] == loan.get("asset_id")), None)
        return render_template("loan_detail.html", loan=loan, calc=loan_calc(loan),
                               years=list(years.values()), asset=asset,
                               currency=data["currency"])

    @app.post("/loans/<loan_id>")
    @login_required
    def update_loan(loan_id):
        with _lock:
            data = load_data()
            assets = data.get("assets", [])
            loan, error = parse_loan(request.form, {a["id"] for a in assets})
            if error:
                return jsonify(ok=False, error=error), 400
            for existing in data.get("loans", []):
                if existing["id"] == loan_id:
                    existing.update(loan)
                    loan = existing
                    break
            else:
                abort(404)
            _write_json(DATA_FILE, data)
        html = render_template("_loan_result_fragment.html", c=loan_calc(loan),
                               currency=data["currency"])
        return jsonify(ok=True, result=html)

    @app.post("/loans/<loan_id>/delete")
    @login_required
    def delete_loan(loan_id):
        with _lock:
            data = load_data()
            data["loans"] = [l for l in data.get("loans", []) if l["id"] != loan_id]
            _write_json(DATA_FILE, data)
        return jsonify(ok=True)

    @app.get("/items")
    @login_required
    def items():
        data = load_data()
        return render_template("items.html", items=data["items"],
                               currency=data["currency"], categories=CATEGORIES,
                               assets=data.get("assets", []))

    @app.post("/items")
    @login_required
    def add_item():
        wants_json = request.headers.get("X-Requested-With") == "fetch"
        with _lock:
            data = load_data()
            assets = data.get("assets", [])
            item, error = parse_item(request.form, {a["id"] for a in assets})
            if error:
                if wants_json:
                    return jsonify(ok=False, error=error), 400
                flash(error)
                return redirect(url_for("items"))
            item["id"] = uuid.uuid4().hex
            data["items"].append(item)
            _write_json(DATA_FILE, data)
        if wants_json:
            html = render_template("_row_fragment.html", i=item,
                                   categories=CATEGORIES, assets=assets)
            return jsonify(ok=True, kind=item["type"], html=html)
        return redirect(url_for("items"))

    @app.post("/items/<item_id>")
    @login_required
    def update_item(item_id):
        wants_json = request.headers.get("X-Requested-With") == "fetch"
        with _lock:
            data = load_data()
            item, error = parse_item(request.form, {a["id"] for a in data.get("assets", [])})
            if error:
                if wants_json:
                    return jsonify(ok=False, error=error), 400
                flash(error)
                return redirect(url_for("items"))
            for existing in data["items"]:
                if existing["id"] == item_id:
                    existing.update(item)
                    break
            else:
                abort(404)
            _write_json(DATA_FILE, data)
        if wants_json:
            return jsonify(ok=True)
        return redirect(url_for("items"))

    @app.post("/items/<item_id>/delete")
    @login_required
    def delete_item(item_id):
        with _lock:
            data = load_data()
            data["items"] = [i for i in data["items"] if i["id"] != item_id]
            _write_json(DATA_FILE, data)
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(ok=True)
        return redirect(url_for("items"))

    return app


if __name__ == "__main__":
    from waitress import serve

    serve(create_app(), host="0.0.0.0", port=8000)
