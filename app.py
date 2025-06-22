import os
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_marshmallow import Marshmallow
from marshmallow import fields as ma_fields 
from flask_cors import CORS
from datetime import datetime, time, timedelta, timezone
from dateutil.relativedelta import relativedelta
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv

# FINAL-VERSION-CHECK-BACKEND-V22
load_dotenv()

# --- Initialization & Configuration ---
app = Flask(__name__)
CORS(app)
bcrypt = Bcrypt(app)
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'rota.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
if db_url:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = { "connect_args": {"options": "-c timezone=utc"} }

# --- Extensions ---
db = SQLAlchemy(app)
ma = Marshmallow(app)
migrate = Migrate(app, db)

# --- Datetime Helper ---
def safe_fromisoformat(date_string):
    """Safely create a timezone-aware UTC datetime object from an ISO string."""
    if not isinstance(date_string, str):
        raise ValueError("Invalid date format: Expected a string.")
    
    dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
    return dt.astimezone(timezone.utc)


# --- Database Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True); username = db.Column(db.String(80), unique=True, nullable=False); email = db.Column(db.String(120), unique=True, nullable=False); role = db.Column(db.String(20), nullable=False, default='employee'); password = db.Column(db.String(128), nullable=False)
class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True); start_time = db.Column(db.DateTime(timezone=True), nullable=False); end_time = db.Column(db.DateTime(timezone=True), nullable=False); user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True); user = db.relationship('User', backref=db.backref('shifts', lazy=True)); recurring_shift_id = db.Column(db.String(36), nullable=True)
class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False); start_date = db.Column(db.Date, nullable=False); end_date = db.Column(db.Date, nullable=False); status = db.Column(db.String(20), nullable=False, default='pending'); notes = db.Column(db.Text, nullable=True); user = db.relationship('User', backref=db.backref('holidays', lazy=True))

# --- API Schemas ---
class UserSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = User; load_instance = True; exclude = ("password",) 
class ShiftSchema(ma.SQLAlchemyAutoSchema):
    start_time = ma_fields.DateTime(format='iso')
    end_time = ma_fields.DateTime(format='iso')
    class Meta:
        model = Shift; include_fk = True; load_instance = True
    user = ma.Nested(UserSchema, only=("id", "username"))
class HolidaySchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Holiday; include_fk = True; load_instance = True
    user = ma.Nested(UserSchema, only=("id", "username"))

user_schema=UserSchema(); users_schema=UserSchema(many=True)
shift_schema=ShiftSchema(); shifts_schema=ShiftSchema(many=True)
holiday_schema=HolidaySchema(); holidays_schema=HolidaySchema(many=True)

# --- API Routes ---
@app.route('/')
def home(): return "Welcome!"

@app.route('/setup-admin', methods=['GET'])
def setup_admin():
    if 'RENDER' in os.environ: 
        return jsonify({"message": "This setup route is disabled in production."}), 403
    with app.app_context():
        admin_user = User.query.filter_by(email='admin@example.com').first()
        hashed_password = bcrypt.generate_password_hash("password").decode('utf-8')
        if admin_user:
            admin_user.password = hashed_password
            db.session.commit(); return jsonify({"message": "Default admin password has been reset."}), 200
        else:
            new_admin = User(username='admin', email='admin@example.com', password=hashed_password, role='admin')
            db.session.add(new_admin); db.session.commit()
            return jsonify({"message": "Default admin created successfully."}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json(); user = User.query.filter_by(email=data.get('email')).first()
    if user and bcrypt.check_password_hash(user.password, data.get('password', '')):
        return user_schema.jsonify(user)
    return jsonify({'message': 'Invalid credentials.'}), 401

@app.route('/users', methods=['GET'])
def get_users(): return jsonify(users_schema.dump(User.query.all()))

@app.route('/users', methods=['POST'])
def add_user():
    data = request.get_json(); hashed_password = bcrypt.generate_password_hash(data['password']).decode('utf-8')
    new_user = User(username=data['username'], email=data['email'], password=hashed_password, role=data.get('role', 'employee'))
    db.session.add(new_user); db.session.commit()
    return user_schema.jsonify(new_user), 201

@app.route('/shifts', methods=['GET'])
def get_shifts():
    start_date_str = request.args.get('start_date'); end_date_str = request.args.get('end_date')
    if not start_date_str or not end_date_str: return jsonify({"message": "date range required"}), 400
    start_date = safe_fromisoformat(start_date_str); end_date = safe_fromisoformat(end_date_str)
    shifts = Shift.query.filter(Shift.start_time >= start_date, Shift.start_time <= end_date).all()
    return jsonify(shifts_schema.dump(shifts))

@app.route('/shifts', methods=['POST'])
def create_shifts():
    data = request.get_json(); start_time_aware = safe_fromisoformat(data['start_time']); end_time_aware = safe_fromisoformat(data['end_time'])
    is_recurring = data.get('is_recurring', False)
    if not is_recurring:
        new_shift = Shift(start_time=start_time_aware.astimezone(timezone.utc), end_time=end_time_aware.astimezone(timezone.utc), user_id=data.get('user_id'))
        db.session.add(new_shift); db.session.commit()
        return shift_schema.jsonify(new_shift), 201
    else:
        shift_start_time = start_time_aware.time(); shift_end_time = end_time_aware.time()
        duration_months = int(data.get('recurrence_months', 1)); end_date = start_time_aware + relativedelta(months=+duration_months)
        recurring_id = os.urandom(16).hex(); created_shifts = []; current_date = start_time_aware
        while current_date.date() < end_date.date():
            shift_start_dt = datetime.combine(current_date.date(), shift_start_time, tzinfo=timezone.utc)
            shift_end_dt = datetime.combine(current_date.date(), shift_end_time, tzinfo=timezone.utc)
            new_shift = Shift(start_time=shift_start_dt, end_time=shift_end_dt, user_id=data.get('user_id'), recurring_shift_id=recurring_id)
            db.session.add(new_shift); created_shifts.append(new_shift)
            current_date += timedelta(weeks=1)
        db.session.commit()
        return jsonify(shifts_schema.dump(created_shifts)), 201

@app.route('/shifts/<int:id>', methods=['PUT'])
def update_shift(id):
    shift_to_update = Shift.query.get_or_404(id); data = request.get_json(); apply_to_all = data.get('apply_to_all', False)
    new_start_dt = safe_fromisoformat(data['start_time']); new_end_dt = safe_fromisoformat(data['end_time'])
    if not apply_to_all or not shift_to_update.recurring_shift_id:
        shift_to_update.start_time = new_start_dt; shift_to_update.end_time = new_end_dt
        shift_to_update.user_id = data.get('user_id', shift_to_update.user_id); shift_to_update.recurring_shift_id = None 
        db.session.commit(); return shift_schema.jsonify(shift_to_update)
    else:
        recurring_id = shift_to_update.recurring_shift_id
        future_shifts = Shift.query.filter(Shift.recurring_shift_id == recurring_id, Shift.start_time >= shift_to_update.start_time).all()
        new_start_time_obj = new_start_dt.time(); new_end_time_obj = new_end_dt.time()
        for shift in future_shifts:
            date = shift.start_time.date()
            shift.start_time = datetime.combine(date, new_start_time_obj, tzinfo=timezone.utc)
            shift.end_time = datetime.combine(date, new_end_time_obj, tzinfo=timezone.utc)
            shift.user_id = data.get('user_id', shift.user_id)
        db.session.commit(); return jsonify(shifts_schema.dump(future_shifts))

@app.route('/shifts/<int:id>', methods=['DELETE'])
def delete_shift(id):
    shift_to_delete = Shift.query.get_or_404(id); data = request.get_json() or {}; apply_to_all = data.get('apply_to_all', False)
    if not apply_to_all or not shift_to_delete.recurring_shift_id:
        db.session.delete(shift_to_delete); db.session.commit()
        return jsonify({'message': 'Shift deleted successfully.'})
    else:
        recurring_id = shift_to_delete.recurring_shift_id
        future_shifts = Shift.query.filter(Shift.recurring_shift_id == recurring_id, Shift.start_time >= shift_to_delete.start_time).all()
        for shift in future_shifts:
            db.session.delete(shift)
        db.session.commit(); return jsonify({'message': f'{len(future_shifts)} recurring shifts deleted.'})

@app.route('/holidays', methods=['GET'])
def get_holidays():
    return jsonify(holidays_schema.dump(Holiday.query.all()))

@app.route('/holidays', methods=['POST'])
def request_holiday():
    data = request.get_json(); new_holiday = Holiday(user_id=data['user_id'], start_date=datetime.fromisoformat(data['start_date']).date(), end_date=datetime.fromisoformat(data['end_date']).date(), notes=data.get('notes'))
    db.session.add(new_holiday); db.session.commit()
    return holiday_schema.jsonify(new_holiday), 201

@app.route('/holidays/<int:id>', methods=['PUT'])
def amend_holiday(id):
    holiday = Holiday.query.get_or_404(id); data = request.get_json()
    if 'status' in data and data['status'] in ['approved', 'rejected']:
        holiday.status = data['status']
    db.session.commit(); return holiday_schema.jsonify(holiday)

@app.route('/holidays/<int:id>', methods=['DELETE'])
def delete_holiday(id):
    holiday = Holiday.query.get_or_404(id); db.session.delete(holiday); db.session.commit()
    return jsonify({"message": "Holiday deleted"}), 200

if __name__ == '__main__':
    app.run(debug=True)
