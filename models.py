from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


ORDER_STATUSES = {
    "new": "Новый",
    "in_progress": "В работе",
    "waiting_parts": "Ожидает запчасти",
    "done": "Готов",
    "closed": "Закрыт",
    "canceled": "Отменён",
}

MOVEMENT_TYPES = {
    "in": "Приход",
    "out": "Расход",
    "adjust": "Корректировка",
}

MOVEMENT_COLORS = {
    "in": "success",
    "out": "danger",
    "adjust": "warning",
}

APPOINTMENT_STATUSES = {
    "planned": "Запланирована",
    "confirmed": "Подтверждена",
    "in_progress": "В работе",
    "done": "Завершена",
    "canceled": "Отменена",
}

APPOINTMENT_COLORS = {
    "planned": "secondary",
    "confirmed": "info",
    "in_progress": "primary",
    "done": "success",
    "canceled": "danger",
}


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(30), nullable=False, unique=True, index=True)
    email = db.Column(db.String(120))
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    cars = db.relationship("Car", backref="client", cascade="all, delete-orphan", lazy=True, order_by="Car.id.desc()")
    orders = db.relationship("Order", backref="client", lazy=True)

    def __repr__(self):
        return f"<Client {self.id} {self.full_name}>"


class Car(db.Model):
    __tablename__ = "cars"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)

    brand = db.Column(db.String(80), nullable=False)
    model = db.Column(db.String(80), nullable=False)
    year = db.Column(db.Integer)
    vin = db.Column(db.String(40), index=True)
    plate = db.Column(db.String(20), index=True)
    mileage = db.Column(db.Integer)
    engine_volume = db.Column(db.String(20))
    transmission = db.Column(db.String(40))
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def title(self):
        parts = [self.brand, self.model]
        if self.year:
            parts.append(f"({self.year})")
        return " ".join(parts)

    def __repr__(self):
        return f"<Car {self.id} {self.brand} {self.model}>"


class Master(db.Model):
    __tablename__ = "masters"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False)
    specialization = db.Column(db.String(120))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    hourly_rate = db.Column(db.Float, default=0)
    percent = db.Column(db.Float, default=0)
    salary_base = db.Column(db.Float, default=0)
    is_active = db.Column(db.Boolean, default=True)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Master {self.id} {self.full_name}>"


class Post(db.Model):
    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Post {self.id} {self.name}>"


class Appointment(db.Model):
    __tablename__ = "appointments"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    car_id = db.Column(db.Integer, db.ForeignKey("cars.id"), nullable=True, index=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=True, index=True)
    master_id = db.Column(db.Integer, db.ForeignKey("masters.id"), nullable=True, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=True)

    start_at = db.Column(db.DateTime, nullable=False, index=True)
    end_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default="planned", index=True)
    comment = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    client = db.relationship("Client", backref="appointments")
    car = db.relationship("Car", backref="appointments")
    post = db.relationship("Post", backref="appointments")
    master = db.relationship("Master", backref="appointments")
    order = db.relationship("Order", backref="appointments")

    @property
    def status_label(self):
        return APPOINTMENT_STATUSES.get(self.status, self.status)

    def __repr__(self):
        return f"<Appointment {self.id} {self.start_at}>"


class Supplier(db.Model):
    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    contact_person = db.Column(db.String(200))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Supplier {self.id} {self.name}>"


class Part(db.Model):
    __tablename__ = "parts"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(80), index=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    brand = db.Column(db.String(120))
    unit = db.Column(db.String(20), default="шт")
    purchase_price = db.Column(db.Float, default=0)
    price = db.Column(db.Float, default=0)
    stock = db.Column(db.Float, default=0)
    min_stock = db.Column(db.Float, default=0)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    supplier = db.relationship("Supplier", backref="parts")
    movements = db.relationship(
        "StockMovement",
        backref="part",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="StockMovement.id.desc()",
    )

    @property
    def is_low(self):
        try:
            return (self.stock or 0) <= (self.min_stock or 0) and (self.min_stock or 0) > 0
        except Exception:
            return False

    @property
    def title(self):
        if self.brand:
            return f"{self.name} ({self.brand})"
        return self.name

    def __repr__(self):
        return f"<Part {self.id} {self.name}>"


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    id = db.Column(db.Integer, primary_key=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False, index=True)
    movement_type = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Float, default=0)
    price = db.Column(db.Float, default=0)
    comment = db.Column(db.Text)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    order = db.relationship("Order", backref="stock_movements")

    @property
    def type_label(self):
        return MOVEMENT_TYPES.get(self.movement_type, self.movement_type)

    def __repr__(self):
        return f"<StockMovement {self.id} {self.movement_type} {self.quantity}>"


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    car_id = db.Column(db.Integer, db.ForeignKey("cars.id"), nullable=False, index=True)

    status = db.Column(db.String(20), nullable=False, default="new", index=True)
    intake_mileage = db.Column(db.Integer)
    complaint = db.Column(db.Text)
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at = db.Column(db.DateTime)

    car = db.relationship("Car", backref="orders")

    works = db.relationship(
        "OrderWork",
        backref="order",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="OrderWork.id",
    )
    parts = db.relationship(
        "OrderPart",
        backref="order",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="OrderPart.id",
    )

    @property
    def total_works(self):
        return sum((w.price or 0) for w in self.works)

    @property
    def total_parts(self):
        return sum(((p.quantity or 0) * (p.price or 0)) for p in self.parts)

    @property
    def total(self):
        return self.total_works + self.total_parts

    @property
    def status_label(self):
        return ORDER_STATUSES.get(self.status, self.status)

    @property
    def parts_not_written_off(self):
        return [p for p in self.parts if p.part_id and not p.written_off]

    def __repr__(self):
        return f"<Order {self.id} {self.status}>"


class OrderWork(db.Model):
    __tablename__ = "order_works"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)

    name = db.Column(db.String(200), nullable=False)
    hours = db.Column(db.Float, default=0)
    price = db.Column(db.Float, default=0)
    master = db.Column(db.String(120))
    master_id = db.Column(db.Integer, db.ForeignKey("masters.id"), nullable=True)

    master_obj = db.relationship("Master", backref="works")

    def __repr__(self):
        return f"<OrderWork {self.id} {self.name}>"


class OrderPart(db.Model):
    __tablename__ = "order_parts"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)

    name = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Float, default=1)
    price = db.Column(db.Float, default=0)

    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=True)
    written_off = db.Column(db.Boolean, default=False)

    stock_part = db.relationship("Part", backref="order_parts")

    @property
    def total(self):
        return (self.quantity or 0) * (self.price or 0)

    def __repr__(self):
        return f"<OrderPart {self.id} {self.name}>"


class Settings(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)

    company_name = db.Column(db.String(200), default="АвтоСервис")
    legal_name = db.Column(db.String(300))
    inn = db.Column(db.String(20))
    ogrn = db.Column(db.String(20))
    address = db.Column(db.String(300))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    director = db.Column(db.String(200))
    accountant = db.Column(db.String(200))

    bank_name = db.Column(db.String(200))
    bik = db.Column(db.String(20))
    account = db.Column(db.String(30))
    corr_account = db.Column(db.String(30))

    vat_rate = db.Column(db.Float, default=0)
    footer_note = db.Column(db.Text)
    logo_filename = db.Column(db.String(200))
    warranty_days = db.Column(db.Integer, default=14)

    # SMS-настройки
    sms_provider = db.Column(db.String(30), default="smsru")  # smsru, smsaero, bytehand, iqsms, gosms, smsc
    sms_api_id = db.Column(db.String(100))                    # для SMS.RU
    sms_email = db.Column(db.String(120))                     # для SMS Aero
    sms_api_key = db.Column(db.String(200))                   # для SMS Aero и других
    sms_login = db.Column(db.String(100))                     # для Bytehand, iqsms, smsc
    sms_password = db.Column(db.String(200))                  # для Bytehand, iqsms, smsc
    sms_sender = db.Column(db.String(50))                     # имя отправителя (подпись)
    sms_enabled = db.Column(db.Boolean, default=False)

    @classmethod
    def get(cls):
        s = cls.query.first()
        if s is None:
            s = cls()
            db.session.add(s)
            db.session.commit()
        return s


class SMSLog(db.Model):
    __tablename__ = "sms_logs"

    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(30), nullable=False)
    message = db.Column(db.Text)
    provider = db.Column(db.String(30))
    status = db.Column(db.String(20))  # sent, failed
    error = db.Column(db.Text)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    order = db.relationship("Order", backref="sms_logs")

    def __repr__(self):
        return f"<SMSLog {self.id} {self.phone} {self.status}>"