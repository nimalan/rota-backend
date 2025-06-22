import os
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_marshmallow import Marshmallow
from flask_cors import CORS
from datetime import datetime, time, timedelta
from dateutil.relativedelta import relativedelta
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv

load_dotenv()

# --- Initialization ---
app = Flask(__name__)
CORS(app)
bcrypt = Bcrypt(app)

# --- Configuration ---
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'rota.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Extensions ---
db = SQLAlchemy(app)
ma = Marshmallow(app)
migrate = Migrate(app, db)

# --- Email Sending Function ---
def send_email(recipient_email, recipient_name, subject, body):
    # This function is assumed to be working correctly with Brevo
    brevo_api_key = os.getenv('BREVO_API_KEY')
    sender_email = os.getenv('SENDER_EMAIL')
    sender_name = os.getenv('SENDER_NAME', 'Rota App')
    if not brevo_api_key or not sender_email:
        print("!!! Email not sent: BREVO_API_KEY or SENDER_EMAIL not set.")
        return False
    # ... email sending logic ...
    return True

# --- Database Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    role = db.Column(db.String(20), nullable=False, default='employee')
    password = db.Column(db.String(128), nullable=False)

class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    start_time = db.Column(db.DateTime(timezone=True), nullable=False)
    end_time = db.Column(db.DateTime(timezone=True), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    user = db.relationship('User', backref=db.backref('shifts', lazy=True))
    recurring_shift_id = db.Column(db.String(36), nullable=True)

class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')
    notes = db.Column(db.Text, nullable=True)
    user = db.relationship('User', backref=db.backref('holidays', lazy=True))

# --- API Schemas ---
class UserSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = User; load_instance = True; exclude = ("password",) 
class ShiftSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Shift; include_fk = True; load_instance = True
    user = ma.Nested(UserSchema, only=("id", "username", "email"))
class HolidaySchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Holiday; include_fk = True; load_instance = True
    user = ma.Nested(UserSchema, only=("id", "username"))

user_schema=UserSchema(); users_schema=UserSchema(many=True)
shift_schema=ShiftSchema(); shifts_schema=ShiftSchema(many=True)
holiday_schema=HolidaySchema(); holidays_schema=HolidaySchema(many=True)

# --- Datetime Helper Function ---
def safe_fromisoformat(date_string):
    """Safely create a timezone-aware datetime object from an ISO string."""
    if date_string.endswith('Z'):
        # Handles '2025-06-21T18:00:00.000Z' format from JavaScript
        return datetime.fromisoformat(date_string.replace('Z', '+00:00'))
    return datetime.fromisoformat(date_string)

# --- API Routes ---
@app.route('/')
def home(): return "Welcome!"

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json(); user = User.query.filter_by(email=data['email']).first()
    if user and bcrypt.check_password_hash(user.password, data['password']):
        return user_schema.jsonify(user)
    return jsonify({'message': 'Invalid credentials.'}), 401

@app.route('/users', methods=['GET'])
def get_users():
    return jsonify(users_schema.dump(User.query.all()))

@app.route('/shifts', methods=['GET'])
def get_shifts():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    if not start_date_str or not end_date_str:
        return jsonify({"message": "start_date and end_date are required"}), 400
    
    start_date = safe_fromisoformat(start_date_str)
    end_date = safe_fromisoformat(end_date_str)
    
    shifts = Shift.query.filter(Shift.start_time >= start_date, Shift.start_time <= end_date).all()
    return jsonify(shifts_schema.dump(shifts))

@app.route('/shifts', methods=['POST'])
def create_shifts():
    data = request.get_json(); is_recurring = data.get('is_recurring', False)
    if not is_recurring:
        new_shift = Shift(
            start_time=safe_fromisoformat(data['start_time']),
            end_time=safe_fromisoformat(data['end_time']),
            user_id=data.get('user_id')
        )
        db.session.add(new_shift); db.session.commit()
        return shift_schema.jsonify(new_shift), 201
    else:
        start_date = safe_fromisoformat(data['start_time'])
        shift_start_time = start_date.time()
        shift_end_time = safe_fromisoformat(data['end_time']).time()
        duration_months = int(data.get('recurrence_months', 1))
        end_date = start_date + relativedelta(months=+duration_months)
        recurring_id = os.urandom(16).hex(); created_shifts = []
        current_date = start_date
        while current_date.date() < end_date.date():
            shift_start_dt = datetime.combine(current_date.date(), shift_start_time)
            shift_end_dt = datetime.combine(current_date.date(), shift_end_time)
            new_shift = Shift(start_time=shift_start_dt, end_time=shift_end_dt, user_id=data.get('user_id'), recurring_shift_id=recurring_id)
            db.session.add(new_shift); created_shifts.append(new_shift)
            current_date += timedelta(weeks=1)
        db.session.commit()
        return jsonify(shifts_schema.dump(created_shifts)), 201

@app.route('/shifts/<int:id>', methods=['PUT'])
def update_shift(id):
    shift_to_update = Shift.query.get_or_404(id)
    data = request.get_json(); apply_to_all = data.get('apply_to_all', False)
    if not apply_to_all or not shift_to_update.recurring_shift_id:
        shift_to_update.start_time = safe_fromisoformat(data['start_time'])
        shift_to_update.end_time = safe_fromisoformat(data['end_time'])
        shift_to_update.user_id = data.get('user_id', shift_to_update.user_id)
        shift_to_update.recurring_shift_id = None 
        db.session.commit()
        return shift_schema.jsonify(shift_to_update)
    else:
        recurring_id = shift_to_update.recurring_shift_id
        future_shifts = Shift.query.filter(Shift.recurring_shift_id == recurring_id, Shift.start_time >= shift_to_update.start_time).all()
        new_start_time = safe_fromisoformat(data['start_time']).time()
        new_end_time = safe_fromisoformat(data['end_time']).time()
        for shift in future_shifts:
            shift.start_time = datetime.combine(shift.start_time.date(), new_start_time)
            shift.end_time = datetime.combine(shift.end_time.date(), new_end_time)
            shift.user_id = data.get('user_id', shift.user_id)
        db.session.commit()
        return jsonify(shifts_schema.dump(future_shifts))
        
@app.route('/holidays', methods=['GET'])
def get_holidays():
    holidays = Holiday.query.all()
    return jsonify(holidays_schema.dump(holidays))

@app.route('/holidays', methods=['POST'])
def request_holiday():
    data = request.get_json()
    new_holiday = Holiday(
        user_id=data['user_id'],
        start_date=safe_fromisoformat(data['start_date']).date(),
        end_date=safe_fromisoformat(data['end_date']).date(),
        notes=data.get('notes')
    )
    db.session.add(new_holiday); db.session.commit()
    return holiday_schema.jsonify(new_holiday), 201

# ... other routes can be added here ...

if __name__ == '__main__':
    app.run(debug=True)
