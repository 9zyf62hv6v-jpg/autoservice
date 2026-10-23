import os
import sys
import csv
import io
from datetime import datetime, timezone, date, timedelta
from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort, jsonify,
    Response, session
)
from werkzeug.utils import secure_filename
from flask import send_from_directory
from config import Config
from models import (
    db, Client, Car, Master, Post, Appointment, Supplier, Part, StockMovement,
    Order, OrderWork, OrderPart, Settings, SMSLog,
    ORDER_STATUSES, MOVEMENT_TYPES, MOVEMENT_COLORS,
    APPOINTMENT_STATUSES, APPOINTMENT_COLORS,
)
from sms_gateway import get_provider


if getattr(sys, "frozen", False):
    _base = sys._MEIPASS
    _template_folder = os.path.join(_base, "templates")
    _static_folder = os.path.join(_base, "static")
else:
    _template_folder = "templates"
    _static_folder = "static"

app = Flask(__name__, template_folder=_template_folder, static_folder=_static_folder)
app.config.from_object(Config)
db.init_app(app)


# ---------- Папка для загруженных файлов (логотип) ----------

def _get_uploads_dir():
    """Папка uploads — рядом с БД, чтобы сохранялась между запусками .exe."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.abspath(os.path.dirname(__file__))
    d = os.path.join(base, "uploads")
    os.makedirs(d, exist_ok=True)
    return d


UPLOADS_DIR = _get_uploads_dir()


@app.route("/uploads/<path:filename>")
def uploads_file(filename):
    return send_from_directory(UPLOADS_DIR, filename)


# ---------- Контекст ----------

@app.context_processor
def inject_globals():
    return {
        "ORDER_STATUSES": ORDER_STATUSES,
        "MOVEMENT_TYPES": MOVEMENT_TYPES,
        "MOVEMENT_COLORS": MOVEMENT_COLORS,
        "APPOINTMENT_STATUSES": APPOINTMENT_STATUSES,
        "APPOINTMENT_COLORS": APPOINTMENT_COLORS,
        "STATUS_COLORS": {
            "new": "secondary",
            "in_progress": "primary",
            "waiting_parts": "warning",
            "done": "info",
            "closed": "success",
            "canceled": "danger",
        },
    }


# ---------- Авто-миграция ----------

def _auto_migrate():
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    tables = set(insp.get_table_names())

    if "order_works" in tables:
        cols = {c["name"] for c in insp.get_columns("order_works")}
        with db.engine.begin() as conn:
            if "master_id" not in cols:
                conn.execute(text("ALTER TABLE order_works ADD COLUMN master_id INTEGER"))
            if "master" not in cols:
                conn.execute(text("ALTER TABLE order_works ADD COLUMN master VARCHAR(120)"))

    if "order_parts" in tables:
        cols = {c["name"] for c in insp.get_columns("order_parts")}
        with db.engine.begin() as conn:
            if "part_id" not in cols:
                conn.execute(text("ALTER TABLE order_parts ADD COLUMN part_id INTEGER"))
            if "written_off" not in cols:
                conn.execute(text("ALTER TABLE order_parts ADD COLUMN written_off BOOLEAN DEFAULT 0"))

    if "settings" in tables:
        cols = {c["name"] for c in insp.get_columns("settings")}
        with db.engine.begin() as conn:
            if "logo_filename" not in cols:
                conn.execute(text("ALTER TABLE settings ADD COLUMN logo_filename VARCHAR(200)"))
            if "warranty_days" not in cols:
                conn.execute(text("ALTER TABLE settings ADD COLUMN warranty_days INTEGER DEFAULT 14"))
            # SMS-поля
            sms_fields = [
                ("sms_provider", "VARCHAR(30) DEFAULT 'smsru'"),
                ("sms_api_id", "VARCHAR(100)"),
                ("sms_email", "VARCHAR(120)"),
                ("sms_api_key", "VARCHAR(200)"),
                ("sms_login", "VARCHAR(100)"),
                ("sms_password", "VARCHAR(200)"),
                ("sms_sender", "VARCHAR(50)"),
                ("sms_enabled", "BOOLEAN DEFAULT 0"),
            ]
            for fname, ftype in sms_fields:
                if fname not in cols:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {fname} {ftype}"))


# ---------- Дашборд ----------

@app.route("/")
def index():
    open_statuses = ("new", "in_progress", "waiting_parts", "done")
    closed = Order.query.filter_by(status="closed").all()
    all_parts = Part.query.all()

    today = date.today()
    start_today = datetime.combine(today, datetime.min.time())
    end_today = datetime.combine(today, datetime.max.time())
    today_appointments = Appointment.query.filter(
        Appointment.start_at >= start_today,
        Appointment.start_at <= end_today,
    ).count()

    stats = {
        "clients": Client.query.count(),
        "cars": Car.query.count(),
        "open_orders": Order.query.filter(Order.status.in_(open_statuses)).count(),
        "revenue": sum(o.total for o in closed),
        "stock_value": sum(((p.stock or 0) * (p.purchase_price or 0)) for p in all_parts),
        "low_stock": sum(1 for p in all_parts if p.is_low),
        "masters": Master.query.filter_by(is_active=True).count(),
        "today_appointments": today_appointments,
    }
    recent_orders = Order.query.order_by(Order.id.desc()).limit(5).all()
    low_stock_parts = sorted([p for p in all_parts if p.is_low], key=lambda x: x.name)[:5]

    return render_template(
        "index.html",
        stats=stats,
        recent_orders=recent_orders,
        low_stock_parts=low_stock_parts,
    )


# ---------- Клиенты ----------

@app.route("/clients")
def clients_list():
    q = request.args.get("q", "").strip()
    query = Client.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Client.full_name.ilike(like),
            Client.phone.ilike(like),
            Client.email.ilike(like),
        ))
    clients = query.order_by(Client.full_name).all()
    return render_template("clients/list.html", clients=clients, q=q)


@app.route("/clients/new", methods=["GET", "POST"])
def client_new():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        comment = request.form.get("comment", "").strip()

        errors = []
        if not full_name:
            errors.append("Укажите ФИО клиента.")
        if not phone:
            errors.append("Укажите телефон.")
        elif Client.query.filter_by(phone=phone).first():
            errors.append("Клиент с таким телефоном уже существует.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("clients/form.html", client=None, form=request.form)

        client = Client(full_name=full_name, phone=phone, email=email, comment=comment)
        db.session.add(client)
        db.session.commit()
        flash("Клиент добавлен.", "success")
        return redirect(url_for("client_detail", client_id=client.id))

    return render_template("clients/form.html", client=None, form={})


@app.route("/clients/<int:client_id>")
def client_detail(client_id):
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)
    return render_template("clients/detail.html", client=client)


@app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
def client_edit(client_id):
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        comment = request.form.get("comment", "").strip()

        errors = []
        if not full_name:
            errors.append("Укажите ФИО клиента.")
        if not phone:
            errors.append("Укажите телефон.")
        else:
            existing = Client.query.filter_by(phone=phone).first()
            if existing and existing.id != client.id:
                errors.append("Клиент с таким телефоном уже существует.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("clients/form.html", client=client, form=request.form)

        client.full_name = full_name
        client.phone = phone
        client.email = email
        client.comment = comment
        db.session.commit()
        flash("Данные клиента обновлены.", "success")
        return redirect(url_for("client_detail", client_id=client.id))

    return render_template("clients/form.html", client=client, form={})


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
def client_delete(client_id):
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)
    db.session.delete(client)
    db.session.commit()
    flash("Клиент удалён вместе с автомобилями и заказами.", "success")
    return redirect(url_for("clients_list"))


# ---------- Автомобили ----------

@app.route("/cars")
def cars_list():
    q = request.args.get("q", "").strip()
    query = Car.query.join(Client)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Car.brand.ilike(like),
            Car.model.ilike(like),
            Car.plate.ilike(like),
            Car.vin.ilike(like),
            Client.full_name.ilike(like),
        ))
    cars = query.order_by(Car.id.desc()).all()
    return render_template("cars/list.html", cars=cars, q=q)


@app.route("/clients/<int:client_id>/cars/new", methods=["GET", "POST"])
def car_new(client_id):
    client = db.session.get(Client, client_id)
    if client is None:
        abort(404)

    if request.method == "POST":
        data, errors = _read_car_form(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("cars/form.html", client=client, car=None, form=request.form)
        car = Car(client_id=client.id, **data)
        db.session.add(car)
        db.session.commit()
        flash("Автомобиль добавлен.", "success")
        return redirect(url_for("client_detail", client_id=client.id))
    return render_template("cars/form.html", client=client, car=None, form={})


@app.route("/cars/<int:car_id>/edit", methods=["GET", "POST"])
def car_edit(car_id):
    car = db.session.get(Car, car_id)
    if car is None:
        abort(404)

    if request.method == "POST":
        data, errors = _read_car_form(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("cars/form.html", client=car.client, car=car, form=request.form)
        for key, value in data.items():
            setattr(car, key, value)
        db.session.commit()
        flash("Данные автомобиля обновлены.", "success")
        return redirect(url_for("client_detail", client_id=car.client_id))
    return render_template("cars/form.html", client=car.client, car=car, form={})


@app.route("/cars/<int:car_id>/delete", methods=["POST"])
def car_delete(car_id):
    car = db.session.get(Car, car_id)
    if car is None:
        abort(404)
    client_id = car.client_id
    db.session.delete(car)
    db.session.commit()
    flash("Автомобиль удалён.", "success")
    return redirect(url_for("client_detail", client_id=client_id))


# ---------- API ----------

@app.route("/api/clients/<int:client_id>/cars")
def api_client_cars(client_id):
    client = db.session.get(Client, client_id)
    if client is None:
        return jsonify({"cars": []}), 404
    return jsonify({"cars": [
        {"id": c.id, "title": c.title(), "plate": c.plate or "", "mileage": c.mileage}
        for c in client.cars
    ]})


@app.route("/api/parts/search")
def api_parts_search():
    q = request.args.get("q", "").strip()
    query = Part.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Part.sku.ilike(like), Part.name.ilike(like), Part.brand.ilike(like)))
    parts = query.order_by(Part.name).limit(500).all()
    return jsonify({"parts": [
        {"id": p.id, "sku": p.sku or "", "name": p.name, "brand": p.brand or "",
         "title": p.title, "price": p.price or 0, "stock": p.stock or 0, "unit": p.unit or "шт"}
        for p in parts
    ]})


# ---------- Заказ-наряды ----------

@app.route("/orders")
def orders_list():
    status = request.args.get("status", "").strip()
    query = Order.query
    if status and status in ORDER_STATUSES:
        query = query.filter_by(status=status)
    orders = query.order_by(Order.id.desc()).all()
    return render_template("orders/list.html", orders=orders, current_status=status)


@app.route("/orders/new", methods=["GET", "POST"])
def order_new():
    if request.method == "POST":
        client_id = request.form.get("client_id", type=int)
        car_id = request.form.get("car_id", type=int)
        intake_mileage = request.form.get("intake_mileage", "").strip()
        complaint = request.form.get("complaint", "").strip()
        notes = request.form.get("notes", "").strip()

        errors = []
        client = db.session.get(Client, client_id) if client_id else None
        car = db.session.get(Car, car_id) if car_id else None
        if client is None: errors.append("Выберите клиента.")
        if car is None: errors.append("Выберите автомобиль.")
        if client and car and car.client_id != client.id:
            errors.append("Автомобиль не принадлежит выбранному клиенту.")

        mileage = None
        if intake_mileage:
            try:
                mileage = int(intake_mileage)
                if mileage < 0: errors.append("Пробег не может быть отрицательным.")
            except ValueError:
                errors.append("Пробег должен быть числом.")

        if errors:
            for e in errors: flash(e, "danger")
            return render_template("orders/form.html", order=None,
                clients=Client.query.order_by(Client.full_name).all(), form=request.form)

        order = Order(client_id=client.id, car_id=car.id, status="new",
                      intake_mileage=mileage, complaint=complaint or None, notes=notes or None)
        db.session.add(order)
        db.session.commit()
        flash(f"Заказ-наряд №{order.id} создан.", "success")
        return redirect(url_for("order_detail", order_id=order.id))

    clients = Client.query.order_by(Client.full_name).all()
    form = {}
    preselect_client_id = request.args.get("client_id", type=int)
    if preselect_client_id:
        form = {"client_id": str(preselect_client_id)}
    return render_template("orders/form.html", order=None, clients=clients, form=form)


@app.route("/orders/<int:order_id>")
def order_detail(order_id):
    order = db.session.get(Order, order_id)
    if order is None:
        abort(404)
    masters = Master.query.filter_by(is_active=True).order_by(Master.full_name).all()
    return render_template("orders/detail.html", order=order, masters=masters)


@app.route("/orders/<int:order_id>/edit", methods=["GET", "POST"])
def order_edit(order_id):
    order = db.session.get(Order, order_id)
    if order is None:
        abort(404)

    if request.method == "POST":
        client_id = request.form.get("client_id", type=int)
        car_id = request.form.get("car_id", type=int)
        status = request.form.get("status", "").strip()
        intake_mileage = request.form.get("intake_mileage", "").strip()
        complaint = request.form.get("complaint", "").strip()
        notes = request.form.get("notes", "").strip()

        errors = []
        client = db.session.get(Client, client_id) if client_id else None
        car = db.session.get(Car, car_id) if car_id else None
        if client is None: errors.append("Выберите клиента.")
        if car is None: errors.append("Выберите автомобиль.")
        if client and car and car.client_id != client.id:
            errors.append("Автомобиль не принадлежит выбранному клиенту.")
        if status not in ORDER_STATUSES:
            errors.append("Некорректный статус.")

        mileage = None
        if intake_mileage:
            try:
                mileage = int(intake_mileage)
                if mileage < 0: errors.append("Пробег не может быть отрицательным.")
            except ValueError:
                errors.append("Пробег должен быть числом.")

        if errors:
            for e in errors: flash(e, "danger")
            return render_template("orders/form.html", order=order,
                clients=Client.query.order_by(Client.full_name).all(), form=request.form)

        order.client_id = client.id
        order.car_id = car.id
        order.status = status
        order.intake_mileage = mileage
        order.complaint = complaint or None
        order.notes = notes or None
        db.session.commit()
        flash("Заказ-наряд обновлён.", "success")
        return redirect(url_for("order_detail", order_id=order.id))

    clients = Client.query.order_by(Client.full_name).all()
    return render_template("orders/form.html", order=order, clients=clients, form={})


@app.route("/orders/<int:order_id>/status", methods=["POST"])
def order_status(order_id):
    order = db.session.get(Order, order_id)
    if order is None: abort(404)
    status = request.form.get("status", "").strip()
    if status not in ORDER_STATUSES:
        flash("Некорректный статус.", "danger")
    else:
        order.status = status
        if status == "closed" and order.closed_at is None:
            order.closed_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("Статус обновлён.", "success")
    return redirect(url_for("order_detail", order_id=order.id))


@app.route("/orders/<int:order_id>/delete", methods=["POST"])
def order_delete(order_id):
    order = db.session.get(Order, order_id)
    if order is None: abort(404)
    for op in order.parts:
        if op.written_off and op.part_id:
            stock_part = db.session.get(Part, op.part_id)
            if stock_part:
                stock_part.stock = (stock_part.stock or 0) + (op.quantity or 0)
                db.session.add(StockMovement(part_id=stock_part.id, movement_type="in",
                    quantity=op.quantity or 0, price=0, order_id=None,
                    comment=f"Возврат при удалении заказа #{order_id}"))
    db.session.delete(order)
    db.session.commit()
    flash("Заказ-наряд удалён. Списанные запчасти возвращены на склад.", "success")
    return redirect(url_for("orders_list"))


# ---------- Работы ----------

@app.route("/orders/<int:order_id>/works/add", methods=["POST"])
def order_work_add(order_id):
    order = db.session.get(Order, order_id)
    if order is None: abort(404)
    name = request.form.get("name", "").strip()
    hours = _to_float(request.form.get("hours"))
    price = _to_float(request.form.get("price"))
    master_id = request.form.get("master_id", type=int)
    master = db.session.get(Master, master_id) if master_id else None
    if not name:
        flash("Укажите название работы.", "danger")
        return redirect(url_for("order_detail", order_id=order.id))
    db.session.add(OrderWork(order_id=order.id, name=name, hours=hours or 0, price=price or 0,
                             master=master.full_name if master else None,
                             master_id=master.id if master else None))
    db.session.commit()
    flash("Работа добавлена.", "success")
    return redirect(url_for("order_detail", order_id=order.id))


@app.route("/orders/<int:order_id>/works/<int:work_id>/edit", methods=["POST"])
def order_work_edit(order_id, work_id):
    work = db.session.get(OrderWork, work_id)
    if work is None or work.order_id != order_id: abort(404)
    name = request.form.get("name", "").strip()
    hours = _to_float(request.form.get("hours"))
    price = _to_float(request.form.get("price"))
    master_id = request.form.get("master_id", type=int)
    master = db.session.get(Master, master_id) if master_id else None
    if not name:
        flash("Укажите название работы.", "danger")
        return redirect(url_for("order_detail", order_id=order_id))
    work.name = name
    work.hours = hours or 0
    work.price = price or 0
    work.master_id = master.id if master else None
    work.master = master.full_name if master else None
    db.session.commit()
    flash("Работа обновлена.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/orders/<int:order_id>/works/<int:work_id>/delete", methods=["POST"])
def order_work_delete(order_id, work_id):
    work = db.session.get(OrderWork, work_id)
    if work is None or work.order_id != order_id: abort(404)
    db.session.delete(work)
    db.session.commit()
    flash("Работа удалена.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


# ---------- Запчасти в заказе ----------

@app.route("/orders/<int:order_id>/parts/add", methods=["POST"])
def order_part_add(order_id):
    order = db.session.get(Order, order_id)
    if order is None: abort(404)
    name = request.form.get("name", "").strip()
    quantity = _to_float(request.form.get("quantity"))
    price = _to_float(request.form.get("price"))
    part_id = request.form.get("part_id", type=int)
    stock_part = db.session.get(Part, part_id) if part_id else None
    if part_id and stock_part is None: part_id = None
    if stock_part:
        if not name: name = stock_part.title
        if price is None: price = stock_part.price or 0
    if not name:
        flash("Укажите наименование запчасти.", "danger")
        return redirect(url_for("order_detail", order_id=order.id))
    db.session.add(OrderPart(order_id=order.id, name=name,
                             quantity=quantity if quantity is not None else 1,
                             price=price or 0, part_id=part_id, written_off=False))
    db.session.commit()
    flash("Запчасть добавлена.", "success")
    return redirect(url_for("order_detail", order_id=order.id))


@app.route("/orders/<int:order_id>/parts/<int:part_id>/edit", methods=["POST"])
def order_part_edit(order_id, part_id):
    part = db.session.get(OrderPart, part_id)
    if part is None or part.order_id != order_id: abort(404)
    name = request.form.get("name", "").strip()
    quantity = _to_float(request.form.get("quantity"))
    price = _to_float(request.form.get("price"))
    if not name:
        flash("Укажите наименование запчасти.", "danger")
        return redirect(url_for("order_detail", order_id=order_id))
    part.name = name
    part.quantity = quantity if quantity is not None else 1
    part.price = price or 0
    db.session.commit()
    flash("Запчасть обновлена.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/orders/<int:order_id>/parts/<int:part_id>/delete", methods=["POST"])
def order_part_delete(order_id, part_id):
    part = db.session.get(OrderPart, part_id)
    if part is None or part.order_id != order_id: abort(404)
    if part.written_off and part.part_id:
        stock_part = db.session.get(Part, part.part_id)
        if stock_part:
            stock_part.stock = (stock_part.stock or 0) + (part.quantity or 0)
            db.session.add(StockMovement(part_id=stock_part.id, movement_type="in",
                quantity=part.quantity or 0, price=0, order_id=None,
                comment=f"Возврат по заказу #{order_id} (удаление позиции)"))
    db.session.delete(part)
    db.session.commit()
    flash("Запчасть удалена.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/orders/<int:order_id>/write-off", methods=["POST"])
def order_write_off(order_id):
    order = db.session.get(Order, order_id)
    if order is None: abort(404)
    count = 0
    errors = []
    for op in order.parts:
        if op.written_off or not op.part_id: continue
        stock_part = db.session.get(Part, op.part_id)
        if stock_part is None: continue
        if (stock_part.stock or 0) < (op.quantity or 0):
            errors.append(f"{stock_part.name}: в наличии {stock_part.stock or 0} {stock_part.unit or 'шт'}, требуется {op.quantity or 0}")
            continue
        stock_part.stock = (stock_part.stock or 0) - (op.quantity or 0)
        db.session.add(StockMovement(part_id=stock_part.id, movement_type="out",
            quantity=op.quantity or 0, price=op.price or 0, order_id=order.id,
            comment=f"Списание по заказу #{order.id}"))
        op.written_off = True
        count += 1
    if errors:
        db.session.rollback()
        for e in errors: flash(e, "warning")
        flash("Списание не выполнено — исправьте количество или пополните склад.", "danger")
        return redirect(url_for("order_detail", order_id=order.id))
    if count:
        db.session.commit()
        flash(f"Списано позиций: {count}.", "success")
    else:
        flash("Нет позиций со склада для списания.", "info")
    return redirect(url_for("order_detail", order_id=order.id))


# ---------- Склад ----------

@app.route("/warehouse")
def warehouse_root():
    return redirect(url_for("parts_list"))


@app.route("/warehouse/parts")
def parts_list():
    q = request.args.get("q", "").strip()
    low = request.args.get("low", "").strip()
    query = Part.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Part.sku.ilike(like), Part.name.ilike(like), Part.brand.ilike(like)))
    parts = query.order_by(Part.name).all()
    if low == "1":
        parts = [p for p in parts if p.is_low]
    return render_template("warehouse/parts_list.html", parts=parts, q=q, low=low)


@app.route("/warehouse/parts/new", methods=["GET", "POST"])
def part_new():
    suppliers = Supplier.query.order_by(Supplier.name).all()
    if request.method == "POST":
        data, errors = _read_part_form(request.form)
        init_stock = _to_float(request.form.get("stock")) or 0
        if errors:
            for e in errors: flash(e, "danger")
            return render_template("warehouse/part_form.html", part=None, suppliers=suppliers, form=request.form)
        part = Part(**data)
        part.stock = 0
        db.session.add(part)
        db.session.commit()
        if init_stock > 0:
            part.stock = init_stock
            db.session.add(StockMovement(part_id=part.id, movement_type="in",
                quantity=init_stock, price=part.purchase_price or 0, comment="Начальный остаток"))
            db.session.commit()
        flash("Запчасть добавлена.", "success")
        return redirect(url_for("part_detail", part_id=part.id))
    return render_template("warehouse/part_form.html", part=None, suppliers=suppliers, form={})


@app.route("/warehouse/parts/<int:part_id>")
def part_detail(part_id):
    part = db.session.get(Part, part_id)
    if part is None: abort(404)
    return render_template("warehouse/part_detail.html", part=part)


@app.route("/warehouse/parts/<int:part_id>/edit", methods=["GET", "POST"])
def part_edit(part_id):
    part = db.session.get(Part, part_id)
    if part is None: abort(404)
    suppliers = Supplier.query.order_by(Supplier.name).all()
    if request.method == "POST":
        data, errors = _read_part_form(request.form)
        if errors:
            for e in errors: flash(e, "danger")
            return render_template("warehouse/part_form.html", part=part, suppliers=suppliers, form=request.form)
        for key, value in data.items():
            setattr(part, key, value)
        db.session.commit()
        flash("Запчасть обновлена.", "success")
        return redirect(url_for("part_detail", part_id=part.id))
    return render_template("warehouse/part_form.html", part=part, suppliers=suppliers, form={})


@app.route("/warehouse/parts/<int:part_id>/delete", methods=["POST"])
def part_delete(part_id):
    part = db.session.get(Part, part_id)
    if part is None: abort(404)
    db.session.delete(part)
    db.session.commit()
    flash("Запчасть удалена.", "success")
    return redirect(url_for("parts_list"))


@app.route("/warehouse/parts/<int:part_id>/movement", methods=["POST"])
def part_movement(part_id):
    part = db.session.get(Part, part_id)
    if part is None: abort(404)
    mtype = request.form.get("movement_type", "").strip()
    quantity = _to_float(request.form.get("quantity"))
    price = _to_float(request.form.get("price"))
    comment = request.form.get("comment", "").strip()
    if mtype not in MOVEMENT_TYPES:
        flash("Некорректный тип движения.", "danger")
        return redirect(url_for("part_detail", part_id=part.id))
    if quantity is None or quantity <= 0:
        flash("Количество должно быть положительным числом.", "danger")
        return redirect(url_for("part_detail", part_id=part.id))
    if mtype == "in":
        part.stock = (part.stock or 0) + quantity
        if price and price > 0: part.purchase_price = price
    elif mtype == "out":
        if (part.stock or 0) < quantity:
            flash(f"Недостаточно на складе: остаток {part.stock or 0}, списывается {quantity}.", "danger")
            return redirect(url_for("part_detail", part_id=part.id))
        part.stock = (part.stock or 0) - quantity
    elif mtype == "adjust":
        part.stock = quantity
    db.session.add(StockMovement(part_id=part.id, movement_type=mtype,
                                 quantity=quantity, price=price or 0, comment=comment or None))
    db.session.commit()
    flash("Движение добавлено.", "success")
    return redirect(url_for("part_detail", part_id=part.id))


# ---------- Поставщики ----------

@app.route("/warehouse/suppliers")
def suppliers_list():
    suppliers = Supplier.query.order_by(Supplier.name).all()
    return render_template("warehouse/suppliers_list.html", suppliers=suppliers)


@app.route("/warehouse/suppliers/new", methods=["GET", "POST"])
def supplier_new():
    if request.method == "POST":
        data, errors = _read_supplier_form(request.form)
        if errors:
            for e in errors: flash(e, "danger")
            return render_template("warehouse/supplier_form.html", supplier=None, form=request.form)
        supplier = Supplier(**data)
        db.session.add(supplier)
        db.session.commit()
        flash("Поставщик добавлен.", "success")
        return redirect(url_for("suppliers_list"))
    return render_template("warehouse/supplier_form.html", supplier=None, form={})


@app.route("/warehouse/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
def supplier_edit(supplier_id):
    supplier = db.session.get(Supplier, supplier_id)
    if supplier is None: abort(404)
    if request.method == "POST":
        data, errors = _read_supplier_form(request.form)
        if errors:
            for e in errors: flash(e, "danger")
            return render_template("warehouse/supplier_form.html", supplier=supplier, form=request.form)
        for key, value in data.items():
            setattr(supplier, key, value)
        db.session.commit()
        flash("Поставщик обновлён.", "success")
        return redirect(url_for("suppliers_list"))
    return render_template("warehouse/supplier_form.html", supplier=supplier, form={})


@app.route("/warehouse/suppliers/<int:supplier_id>/delete", methods=["POST"])
def supplier_delete(supplier_id):
    supplier = db.session.get(Supplier, supplier_id)
    if supplier is None: abort(404)
    for p in supplier.parts:
        p.supplier_id = None
    db.session.delete(supplier)
    db.session.commit()
    flash("Поставщик удалён.", "success")
    return redirect(url_for("suppliers_list"))


# ---------- Мастера ----------

@app.route("/masters")
def masters_list():
    masters = Master.query.order_by(Master.full_name).all()
    return render_template("masters/list.html", masters=masters)


@app.route("/masters/new", methods=["GET", "POST"])
def master_new():
    if request.method == "POST":
        data, errors = _read_master_form(request.form)
        if errors:
            for e in errors: flash(e, "danger")
            return render_template("masters/form.html", master=None, form=request.form)
        master = Master(**data)
        db.session.add(master)
        db.session.commit()
        flash("Мастер добавлен.", "success")
        return redirect(url_for("master_detail", master_id=master.id))
    return render_template("masters/form.html", master=None, form={})


@app.route("/masters/<int:master_id>")
def master_detail(master_id):
    master = db.session.get(Master, master_id)
    if master is None: abort(404)
    works = OrderWork.query.filter_by(master_id=master.id).order_by(OrderWork.id.desc()).limit(50).all()
    all_works = OrderWork.query.filter_by(master_id=master.id).all()
    totals = {
        "works_count": len(all_works),
        "works_sum": sum((w.price or 0) for w in all_works),
        "hours_sum": sum((w.hours or 0) for w in all_works),
    }
    return render_template("masters/detail.html", master=master, works=works, totals=totals)


@app.route("/masters/<int:master_id>/edit", methods=["GET", "POST"])
def master_edit(master_id):
    master = db.session.get(Master, master_id)
    if master is None: abort(404)
    if request.method == "POST":
        data, errors = _read_master_form(request.form)
        if errors:
            for e in errors: flash(e, "danger")
            return render_template("masters/form.html", master=master, form=request.form)
        for key, value in data.items():
            setattr(master, key, value)
        db.session.commit()
        flash("Мастер обновлён.", "success")
        return redirect(url_for("master_detail", master_id=master.id))
    return render_template("masters/form.html", master=master, form={})


@app.route("/masters/<int:master_id>/delete", methods=["POST"])
def master_delete(master_id):
    master = db.session.get(Master, master_id)
    if master is None: abort(404)
    for w in OrderWork.query.filter_by(master_id=master.id).all():
        w.master_id = None
    db.session.delete(master)
    db.session.commit()
    flash("Мастер удалён.", "success")
    return redirect(url_for("masters_list"))


@app.route("/masters/<int:master_id>/salary")
def master_salary(master_id):
    master = db.session.get(Master, master_id)
    if master is None: abort(404)
    date_from, date_to = _read_period(request.args)
    works = _works_for_master_period(master.id, date_from, date_to)
    totals = _calc_salary(master, works)
    return render_template("masters/salary.html", master=master, works=works, totals=totals,
                           date_from=date_from.isoformat(), date_to=date_to.isoformat())


@app.route("/salary")
def salary_report():
    date_from, date_to = _read_period(request.args)
    masters = Master.query.order_by(Master.full_name).all()
    rows = []
    for m in masters:
        works = _works_for_master_period(m.id, date_from, date_to)
        totals = _calc_salary(m, works)
        rows.append({"master": m, **totals})
    grand = {
        "works_sum": sum(r["works_sum"] for r in rows),
        "hours_sum": sum(r["hours_sum"] for r in rows),
        "variable": sum(r["variable"] for r in rows),
        "base": sum(r["base"] for r in rows),
        "total": sum(r["total"] for r in rows),
    }
    return render_template("salary_report.html", rows=rows, grand=grand,
                           date_from=date_from.isoformat(), date_to=date_to.isoformat())


# ---------- Настройки ----------

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    s = Settings.get()
    if request.method == "POST":
        # Общие
        s.company_name = request.form.get("company_name", "").strip() or "АвтоСервис"
        s.legal_name = request.form.get("legal_name", "").strip() or None
        s.inn = request.form.get("inn", "").strip() or None
        s.ogrn = request.form.get("ogrn", "").strip() or None
        s.address = request.form.get("address", "").strip() or None
        s.phone = request.form.get("phone", "").strip() or None
        s.email = request.form.get("email", "").strip() or None
        s.director = request.form.get("director", "").strip() or None
        s.accountant = request.form.get("accountant", "").strip() or None
        s.bank_name = request.form.get("bank_name", "").strip() or None
        s.bik = request.form.get("bik", "").strip() or None
        s.account = request.form.get("account", "").strip() or None
        s.corr_account = request.form.get("corr_account", "").strip() or None
        s.vat_rate = _to_float(request.form.get("vat_rate")) or 0
        s.footer_note = request.form.get("footer_note", "").strip() or None
        s.warranty_days = int(_to_float(request.form.get("warranty_days")) or 14)

        # SMS
        s.sms_provider = request.form.get("sms_provider", "smsru").strip()
        s.sms_api_id = request.form.get("sms_api_id", "").strip() or None
        s.sms_email = request.form.get("sms_email", "").strip() or None
        s.sms_api_key = request.form.get("sms_api_key", "").strip() or None
        s.sms_login = request.form.get("sms_login", "").strip() or None
        s.sms_password = request.form.get("sms_password", "").strip() or None
        s.sms_sender = request.form.get("sms_sender", "").strip() or None
        s.sms_enabled = bool(request.form.get("sms_enabled"))

        # Логотип
        logo = request.files.get("logo")
        if logo and logo.filename:
            filename = secure_filename(logo.filename)
            logo.save(os.path.join(UPLOADS_DIR, filename))
            s.logo_filename = filename

        db.session.commit()
        flash("Настройки сохранены.", "success")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", s=s)


# ---------- Уведомления ----------

@app.route("/notifications")
def notifications_log():
    logs = SMSLog.query.order_by(SMSLog.id.desc()).limit(200).all()
    return render_template("notifications/log.html", logs=logs)


@app.route("/orders/<int:order_id>/notify", methods=["GET", "POST"])
def order_notify(order_id):
    order = db.session.get(Order, order_id)
    if order is None: abort(404)
    settings = Settings.get()

    if request.method == "POST":
        template = request.form.get("template", "").strip()
        message = request.form.get("message", "").strip()
        phone = request.form.get("phone", order.client.phone).strip()

        # Подстановка переменных
        text = message
        replacements = {
            "{client_name}": order.client.full_name,
            "{car}": f"{order.car.brand} {order.car.model}",
            "{order_id}": str(order.id),
            "{total}": f"{order.total:.2f}",
            "{company}": settings.company_name or "АвтоСервис",
        }
        for k, v in replacements.items():
            text = text.replace(k, v)

        success = False
        error = None
        if settings.sms_enabled and settings.sms_provider:
            try:
                provider = get_provider(settings)
                success, error = provider.send(phone, text)
            except Exception as e:
                success = False
                error = str(e)
        else:
            error = "SMS-уведомления отключены или не настроены."

        log = SMSLog(
            phone=phone,
            message=text,
            provider=settings.sms_provider,
            status="sent" if success else "failed",
            error=error,
            order_id=order.id,
        )
        db.session.add(log)
        db.session.commit()

        if success:
            flash("SMS отправлено.", "success")
        else:
            flash(f"Ошибка отправки: {error}", "danger")
        return redirect(url_for("order_detail", order_id=order.id))

    # Шаблоны по умолчанию
    default_templates = [
        {
            "name": "Машина готова",
            "text": "Здравствуйте, {client_name}! Ваш автомобиль {car} готов к выдаче. Заказ-наряд №{order_id}, сумма {total} ₽. {company}",
        },
        {
            "name": "Напоминание о ТО",
            "text": "{client_name}, напоминаем о плановом ТО для {car}. Запишитесь в {company}.",
        },
        {
            "name": "Запись подтверждена",
            "text": "{client_name}, ваша запись на {car} подтверждена. Ждём вас в {company}.",
        },
    ]

    return render_template("notifications/order_notify.html",
                           order=order, settings=settings, default_templates=default_templates)


# ---------- Печать ----------

@app.route("/orders/<int:order_id>/print/order")
def print_order(order_id):
    order = db.session.get(Order, order_id)
    if order is None: abort(404)
    return render_template("print/order.html", order=order, s=Settings.get())


@app.route("/orders/<int:order_id>/print/act")
def print_act(order_id):
    order = db.session.get(Order, order_id)
    if order is None: abort(404)
    return render_template("print/act.html", order=order, s=Settings.get())


@app.route("/orders/<int:order_id>/print/invoice")
def print_invoice(order_id):
    order = db.session.get(Order, order_id)
    if order is None: abort(404)
    return render_template("print/invoice.html", order=order, s=Settings.get())


@app.route("/orders/<int:order_id>/print/warranty")
def print_warranty(order_id):
    order = db.session.get(Order, order_id)
    if order is None: abort(404)
    return render_template("print/warranty.html", order=order, s=Settings.get())


# ---------- Календарь ----------

@app.route("/calendar")
def calendar_view():
    week_start_str = request.args.get("week", "").strip()
    anchor = _parse_date(week_start_str) or date.today()
    monday = anchor - timedelta(days=anchor.weekday())
    days = [monday + timedelta(days=i) for i in range(7)]
    start = datetime.combine(days[0], datetime.min.time())
    end = datetime.combine(days[-1], datetime.max.time())

    posts = Post.query.filter_by(is_active=True).order_by(Post.sort_order, Post.id).all()
    appointments = (Appointment.query
        .filter(Appointment.start_at >= start)
        .filter(Appointment.start_at <= end)
        .order_by(Appointment.start_at).all())

    by_day = {d.isoformat(): [] for d in days}
    for a in appointments:
        key = a.start_at.date().isoformat()
        if key in by_day: by_day[key].append(a)

    return render_template("calendar/index.html", days=days, by_day=by_day, posts=posts,
                           monday=monday,
                           week_prev=(monday - timedelta(days=7)).isoformat(),
                           week_next=(monday + timedelta(days=7)).isoformat(),
                           today=date.today())


@app.route("/appointments/new", methods=["GET", "POST"])
def appointment_new():
    if request.method == "POST":
        data, errors = _read_appointment_form(request.form)
        if errors:
            for e in errors: flash(e, "danger")
            return _render_appointment_form(None, request.form)
        appt = Appointment(**data)
        db.session.add(appt)
        db.session.commit()
        flash("Запись создана.", "success")
        return redirect(url_for("calendar_view",
            week=(appt.start_at.date() - timedelta(days=appt.start_at.weekday())).isoformat()))
    form = {}
    pre_date = request.args.get("date")
    pre_post = request.args.get("post_id")
    pre_hour = request.args.get("hour")
    if pre_date: form["start_date"] = pre_date
    if pre_post: form["post_id"] = pre_post
    if pre_hour: form["start_time"] = f"{pre_hour.zfill(2)}:00"
    return _render_appointment_form(None, form)


@app.route("/appointments/<int:appt_id>/edit", methods=["GET", "POST"])
def appointment_edit(appt_id):
    appt = db.session.get(Appointment, appt_id)
    if appt is None: abort(404)
    if request.method == "POST":
        data, errors = _read_appointment_form(request.form, exclude_id=appt.id)
        if errors:
            for e in errors: flash(e, "danger")
            return _render_appointment_form(appt, request.form)
        for k, v in data.items(): setattr(appt, k, v)
        db.session.commit()
        flash("Запись обновлена.", "success")
        return redirect(url_for("calendar_view",
            week=(appt.start_at.date() - timedelta(days=appt.start_at.weekday())).isoformat()))
    return _render_appointment_form(appt, {})


@app.route("/appointments/<int:appt_id>/delete", methods=["POST"])
def appointment_delete(appt_id):
    appt = db.session.get(Appointment, appt_id)
    if appt is None: abort(404)
    appt_date = appt.start_at.date()
    db.session.delete(appt)
    db.session.commit()
    flash("Запись удалена.", "success")
    return redirect(url_for("calendar_view",
        week=(appt_date - timedelta(days=appt_date.weekday())).isoformat()))


@app.route("/appointments/<int:appt_id>/status", methods=["POST"])
def appointment_status(appt_id):
    appt = db.session.get(Appointment, appt_id)
    if appt is None: abort(404)
    status = request.form.get("status", "").strip()
    if status in APPOINTMENT_STATUSES:
        appt.status = status
        db.session.commit()
        flash("Статус обновлён.", "success")
    return redirect(url_for("appointment_edit", appt_id=appt.id))


@app.route("/appointments/<int:appt_id>/to-order", methods=["POST"])
def appointment_to_order(appt_id):
    appt = db.session.get(Appointment, appt_id)
    if appt is None: abort(404)
    if appt.order_id:
        flash("Заказ-наряд для этой записи уже создан.", "info")
        return redirect(url_for("order_detail", order_id=appt.order_id))
    if appt.car is None:
        flash("К записи не привязан автомобиль — создайте заказ-наряд вручную.", "danger")
        return redirect(url_for("appointment_edit", appt_id=appt.id))
    order = Order(client_id=appt.client_id, car_id=appt.car_id, status="new",
                  intake_mileage=appt.car.mileage, complaint=appt.comment)
    db.session.add(order)
    db.session.flush()
    appt.order_id = order.id
    db.session.commit()
    flash(f"Создан заказ-наряд №{order.id}.", "success")
    return redirect(url_for("order_detail", order_id=order.id))


@app.route("/appointments/<int:appt_id>/move", methods=["POST"])
def appointment_move(appt_id):
    appt = db.session.get(Appointment, appt_id)
    if appt is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    new_date = _parse_date(data.get("date", "").strip())
    if new_date is None:
        return jsonify({"ok": False, "error": "bad date"}), 400
    duration = appt.end_at - appt.start_at
    new_start = datetime.combine(new_date, appt.start_at.time())
    new_end = new_start + duration
    if appt.post_id:
        conflict = (Appointment.query
            .filter(Appointment.post_id == appt.post_id)
            .filter(Appointment.id != appt.id)
            .filter(Appointment.status != "canceled")
            .filter(Appointment.start_at < new_end)
            .filter(Appointment.end_at > new_start).first())
        if conflict:
            return jsonify({"ok": False, "error": "conflict", "with": conflict.id}), 409
    appt.start_at = new_start
    appt.end_at = new_end
    db.session.commit()
    return jsonify({"ok": True, "date": new_date.isoformat()})


# ---------- Посты ----------

@app.route("/posts")
def posts_list():
    posts = Post.query.order_by(Post.sort_order, Post.id).all()
    return render_template("calendar/posts_list.html", posts=posts)


@app.route("/posts/new", methods=["GET", "POST"])
def post_new():
    if request.method == "POST":
        data, errors = _read_post_form(request.form)
        if errors:
            for e in errors: flash(e, "danger")
            return render_template("calendar/post_form.html", post=None, form=request.form)
        post = Post(**data)
        db.session.add(post)
        db.session.commit()
        flash("Пост добавлен.", "success")
        return redirect(url_for("posts_list"))
    return render_template("calendar/post_form.html", post=None, form={})


@app.route("/posts/<int:post_id>/edit", methods=["GET", "POST"])
def post_edit(post_id):
    post = db.session.get(Post, post_id)
    if post is None: abort(404)
    if request.method == "POST":
        data, errors = _read_post_form(request.form)
        if errors:
            for e in errors: flash(e, "danger")
            return render_template("calendar/post_form.html", post=post, form=request.form)
        for k, v in data.items(): setattr(post, k, v)
        db.session.commit()
        flash("Пост обновлён.", "success")
        return redirect(url_for("posts_list"))
    return render_template("calendar/post_form.html", post=post, form={})


@app.route("/posts/<int:post_id>/delete", methods=["POST"])
def post_delete(post_id):
    post = db.session.get(Post, post_id)
    if post is None: abort(404)
    for a in post.appointments:
        a.post_id = None
    db.session.delete(post)
    db.session.commit()
    flash("Пост удалён.", "success")
    return redirect(url_for("posts_list"))


# ---------- Отчёты ----------

@app.route("/reports")
def reports_index():
    date_from, date_to = _read_period(request.args)
    data = _build_report(date_from, date_to)
    return render_template("reports/index.html", **data,
                           date_from=date_from.isoformat(), date_to=date_to.isoformat())


@app.route("/reports/export.csv")
def reports_export():
    date_from, date_to = _read_period(request.args)
    data = _build_report(date_from, date_to)

    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, delimiter=";")

    writer.writerow(["Отчёт", f"с {date_from.isoformat()} по {date_to.isoformat()}"])
    writer.writerow([])
    writer.writerow(["Показатель", "Значение"])
    writer.writerow(["Закрытых заказов", data["totals"]["orders"]])
    writer.writerow(["Выручка всего", f"{data['totals']['revenue']:.2f}"])
    writer.writerow(["  Работы", f"{data['totals']['works']:.2f}"])
    writer.writerow(["  Запчасти", f"{data['totals']['parts']:.2f}"])
    writer.writerow(["Средний чек", f"{data['totals']['avg_check']:.2f}"])
    writer.writerow(["Новых клиентов", data["totals"]["new_clients"]])
    writer.writerow([])

    writer.writerow(["Топ услуг"])
    writer.writerow(["Услуга", "Количество", "Сумма"])
    for name, cnt, total in data["top_services"]:
        writer.writerow([name, cnt, f"{total:.2f}"])
    writer.writerow([])

    writer.writerow(["Топ мастеров"])
    writer.writerow(["Мастер", "Работ", "Нормо-часы", "Сумма"])
    for name, cnt, hours, total in data["top_masters"]:
        writer.writerow([name, cnt, f"{hours:.2f}", f"{total:.2f}"])
    writer.writerow([])

    writer.writerow(["Топ запчастей"])
    writer.writerow(["Наименование", "Количество", "Сумма"])
    for name, qty, total in data["top_parts"]:
        writer.writerow([name, f"{qty:.2f}", f"{total:.2f}"])

    csv_data = buf.getvalue()
    filename = f"report_{date_from.isoformat()}_{date_to.isoformat()}.csv"
    return Response(csv_data, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def _build_report(date_from, date_to):
    start = datetime.combine(date_from, datetime.min.time())
    end = datetime.combine(date_to, datetime.max.time())

    orders = (Order.query
        .filter(Order.status == "closed")
        .filter(Order.created_at >= start)
        .filter(Order.created_at <= end)
        .all())

    revenue = sum(o.total for o in orders)
    works_total = sum(o.total_works for o in orders)
    parts_total = sum(o.total_parts for o in orders)
    orders_count = len(orders)
    avg_check = revenue / orders_count if orders_count else 0

    new_clients = (Client.query
        .filter(Client.created_at >= start)
        .filter(Client.created_at <= end)
        .count())

    services = {}
    for o in orders:
        for w in o.works:
            key = w.name.strip()
            cnt, total = services.get(key, (0, 0))
            services[key] = (cnt + 1, total + (w.price or 0))
    top_services = sorted(((k, v[0], v[1]) for k, v in services.items()),
                          key=lambda x: x[2], reverse=True)[:15]

    masters = {}
    for o in orders:
        for w in o.works:
            if not w.master: continue
            key = w.master.strip()
            cnt, hours, total = masters.get(key, (0, 0, 0))
            masters[key] = (cnt + 1, hours + (w.hours or 0), total + (w.price or 0))
    top_masters = sorted(((k, v[0], v[1], v[2]) for k, v in masters.items()),
                         key=lambda x: x[3], reverse=True)[:15]

    parts = {}
    for o in orders:
        for p in o.parts:
            key = p.name.strip()
            qty, total = parts.get(key, (0, 0))
            parts[key] = (qty + (p.quantity or 0), total + p.total)
    top_parts = sorted(((k, v[0], v[1]) for k, v in parts.items()),
                       key=lambda x: x[2], reverse=True)[:15]

    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()

    monthly = []
    for (yy, mm) in months:
        m_start = datetime(yy, mm, 1)
        if mm == 12:
            m_end = datetime(yy + 1, 1, 1) - timedelta(seconds=1)
        else:
            m_end = datetime(yy, mm + 1, 1) - timedelta(seconds=1)
        m_orders = Order.query.filter(Order.status == "closed") \
            .filter(Order.created_at >= m_start) \
            .filter(Order.created_at <= m_end).all()
        m_rev = sum(o.total for o in m_orders)
        monthly.append({
            "label": f"{mm:02d}.{yy}",
            "revenue": m_rev,
            "orders": len(m_orders),
        })
    max_rev = max((m["revenue"] for m in monthly), default=0) or 1
    for m in monthly:
        m["percent"] = round(m["revenue"] / max_rev * 100, 1)

    return {
        "totals": {
            "orders": orders_count,
            "revenue": revenue,
            "works": works_total,
            "parts": parts_total,
            "avg_check": avg_check,
            "new_clients": new_clients,
        },
        "top_services": top_services,
        "top_masters": top_masters,
        "top_parts": top_parts,
        "monthly": monthly,
    }


# ---------- Вспомогательное ----------

def _render_appointment_form(appt, form):
    clients = Client.query.order_by(Client.full_name).all()
    posts = Post.query.filter_by(is_active=True).order_by(Post.sort_order, Post.id).all()
    masters = Master.query.filter_by(is_active=True).order_by(Master.full_name).all()
    return render_template("calendar/appointment_form.html",
                           appt=appt, form=form, clients=clients, posts=posts, masters=masters)


def _read_appointment_form(form, exclude_id=None):
    client_id = form.get("client_id", type=int)
    car_id = form.get("car_id", type=int)
    post_id = form.get("post_id", type=int)
    master_id = form.get("master_id", type=int)
    start_date = form.get("start_date", "").strip()
    start_time = form.get("start_time", "").strip()
    end_time = form.get("end_time", "").strip()
    status = form.get("status", "planned").strip()
    comment = form.get("comment", "").strip()

    errors = []
    client = db.session.get(Client, client_id) if client_id else None
    car = db.session.get(Car, car_id) if car_id else None
    post = db.session.get(Post, post_id) if post_id else None
    master = db.session.get(Master, master_id) if master_id else None

    if client is None: errors.append("Выберите клиента.")
    if car and client and car.client_id != client.id:
        errors.append("Автомобиль не принадлежит выбранному клиенту.")
    if status not in APPOINTMENT_STATUSES: status = "planned"

    sd = _parse_date(start_date)
    st = _parse_time(start_time)
    et = _parse_time(end_time)
    if sd is None: errors.append("Укажите дату.")
    if st is None or et is None: errors.append("Укажите время начала и окончания (ЧЧ:ММ).")

    start_dt = end_dt = None
    if sd and st and et:
        start_dt = datetime.combine(sd, st)
        end_dt = datetime.combine(sd, et)
        if end_dt <= start_dt: end_dt = end_dt + timedelta(days=1)
        if post:
            conflict = (Appointment.query
                .filter(Appointment.post_id == post.id)
                .filter(Appointment.status != "canceled")
                .filter(Appointment.start_at < end_dt)
                .filter(Appointment.end_at > start_dt))
            if exclude_id:
                conflict = conflict.filter(Appointment.id != exclude_id)
            c = conflict.first()
            if c:
                errors.append(f"Конфликт: пост «{post.name}» занят с "
                              f"{c.start_at.strftime('%H:%M')} до {c.end_at.strftime('%H:%M')}.")

    data = {
        "client_id": client.id if client else None,
        "car_id": car.id if car else None,
        "post_id": post.id if post else None,
        "master_id": master.id if master else None,
        "start_at": start_dt, "end_at": end_dt,
        "status": status, "comment": comment or None,
    }
    return data, errors


def _read_post_form(form):
    name = form.get("name", "").strip()
    description = form.get("description", "").strip()
    sort_order = int(_to_float(form.get("sort_order")) or 0)
    is_active = bool(form.get("is_active"))
    errors = []
    if not name: errors.append("Укажите название поста.")
    return {"name": name, "description": description or None,
            "sort_order": sort_order, "is_active": is_active}, errors


def _read_car_form(form):
    brand = form.get("brand", "").strip()
    model = form.get("model", "").strip()
    year_raw = form.get("year", "").strip()
    vin = form.get("vin", "").strip().upper()
    plate = form.get("plate", "").strip().upper()
    mileage_raw = form.get("mileage", "").strip()
    engine_volume = form.get("engine_volume", "").strip()
    transmission = form.get("transmission", "").strip()
    comment = form.get("comment", "").strip()
    errors = []
    if not brand: errors.append("Укажите марку.")
    if not model: errors.append("Укажите модель.")
    year = None
    if year_raw:
        try:
            year = int(year_raw)
            if year < 1900 or year > 2100: errors.append("Год в диапазоне 1900–2100.")
        except ValueError: errors.append("Год должен быть числом.")
    mileage = None
    if mileage_raw:
        try:
            mileage = int(mileage_raw)
            if mileage < 0: errors.append("Пробег не может быть отрицательным.")
        except ValueError: errors.append("Пробег должен быть числом.")
    return {"brand": brand, "model": model, "year": year, "vin": vin or None,
            "plate": plate or None, "mileage": mileage,
            "engine_volume": engine_volume or None,
            "transmission": transmission or None,
            "comment": comment or None}, errors


def _read_part_form(form):
    name = form.get("name", "").strip()
    sku = form.get("sku", "").strip()
    brand = form.get("brand", "").strip()
    unit = form.get("unit", "шт").strip() or "шт"
    purchase_price = _to_float(form.get("purchase_price")) or 0
    price = _to_float(form.get("price")) or 0
    min_stock = _to_float(form.get("min_stock")) or 0
    supplier_id = form.get("supplier_id", type=int)
    comment = form.get("comment", "").strip()
    errors = []
    if not name: errors.append("Укажите название запчасти.")
    if price < 0 or purchase_price < 0 or min_stock < 0:
        errors.append("Цены и минимальный остаток не могут быть отрицательными.")
    supplier = db.session.get(Supplier, supplier_id) if supplier_id else None
    if supplier_id and supplier is None: supplier_id = None
    return {"name": name, "sku": sku or None, "brand": brand or None, "unit": unit,
            "purchase_price": purchase_price, "price": price, "min_stock": min_stock,
            "supplier_id": supplier_id, "comment": comment or None}, errors


def _read_supplier_form(form):
    name = form.get("name", "").strip()
    contact_person = form.get("contact_person", "").strip()
    phone = form.get("phone", "").strip()
    email = form.get("email", "").strip()
    comment = form.get("comment", "").strip()
    errors = []
    if not name: errors.append("Укажите название поставщика.")
    return {"name": name, "contact_person": contact_person or None,
            "phone": phone or None, "email": email or None,
            "comment": comment or None}, errors


def _read_master_form(form):
    full_name = form.get("full_name", "").strip()
    specialization = form.get("specialization", "").strip()
    phone = form.get("phone", "").strip()
    email = form.get("email", "").strip()
    hourly_rate = _to_float(form.get("hourly_rate")) or 0
    percent = _to_float(form.get("percent")) or 0
    salary_base = _to_float(form.get("salary_base")) or 0
    is_active = bool(form.get("is_active"))
    comment = form.get("comment", "").strip()
    errors = []
    if not full_name: errors.append("Укажите ФИО мастера.")
    if hourly_rate < 0 or percent < 0 or salary_base < 0:
        errors.append("Ставки и оклад не могут быть отрицательными.")
    if percent > 100: errors.append("Процент не может быть больше 100.")
    return {"full_name": full_name, "specialization": specialization or None,
            "phone": phone or None, "email": email or None,
            "hourly_rate": hourly_rate, "percent": percent,
            "salary_base": salary_base, "is_active": is_active,
            "comment": comment or None}, errors


def _read_period(args):
    today = date.today()
    default_from = today.replace(day=1)
    default_to = today
    date_from = _parse_date(args.get("from")) or default_from
    date_to = _parse_date(args.get("to")) or default_to
    if date_from > date_to: date_from, date_to = date_to, date_from
    return date_from, date_to


def _parse_date(value):
    if not value: return None
    try: return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError: return None


def _parse_time(value):
    if not value: return None
    try: return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError: return None


def _works_for_master_period(master_id, date_from, date_to):
    start = datetime.combine(date_from, datetime.min.time())
    end = datetime.combine(date_to, datetime.max.time())
    return (OrderWork.query.join(Order, OrderWork.order_id == Order.id)
        .filter(OrderWork.master_id == master_id)
        .filter(Order.created_at >= start)
        .filter(Order.created_at <= end)
        .order_by(OrderWork.id.desc()).all())


def _calc_salary(master, works):
    works_sum = sum((w.price or 0) for w in works)
    hours_sum = sum((w.hours or 0) for w in works)
    bonus_percent = works_sum * (master.percent or 0) / 100.0
    hourly_part = hours_sum * (master.hourly_rate or 0)
    variable = max(bonus_percent, hourly_part)
    base = master.salary_base or 0
    return {"works_count": len(works), "works_sum": works_sum, "hours_sum": hours_sum,
            "bonus_percent": bonus_percent, "hourly_part": hourly_part,
            "variable": variable, "base": base, "total": base + variable}


def _to_float(value):
    if value is None: return None
    value = str(value).strip().replace(",", ".")
    if not value: return None
    try: return float(value)
    except ValueError: return None


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        _auto_migrate()
    app.run(debug=True)