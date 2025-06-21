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
# --- UPDATED: More Robust Database Configuration ---
IS_PRODUCTION = os.environ.get('RENDER') is not None

if IS_PRODUCTION:
    # On Render, we MUST use the PostgreSQL database.
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError("FATAL: DATABASE_URL environment variable is not set on Render.")
    
    # Heroku/Render use "postgres://", but SQLAlchemy needs "postgresql://"
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
else:
    # For local development, we can use the SQLite file.
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'rota.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


# --- Extensions ---
db = SQLAlchemy(app)
ma = Marshmallow(app)
migrate = Migrate(app, db)

# --- (The rest of the file remains the same) ---

# --- Email Sending Function ---
def send_email(recipient_email, recipient_name, subject, body):
    brevo_api_key = os.getenv('BREVO_API_KEY')
    sender_email = os.getenv('SENDER_EMAIL')
    sender_name = os.getenv('SENDER_NAME', 'Rota App')
    if not brevo_api_key or not sender_email:
        print("!!! Email not sent: BREVO_API_KEY or SENDER_EMAIL not set.")
        return False
    api_url = 'https://api.brevo.com/v3/smtp/email'
    headers = {'accept': 'application/json', 'api-key': brevo_api_key, 'content-type': 'application/json'}
    payload = {"sender": {"name": sender_name, "email": sender_email}, "to": [{"email": recipient_email, "name": recipient_name}], "subject": subject, "htmlContent": f"<html><body><p>{body.replace(os.linesep, '<br>')}</p></body></html>"}
    response = requests.post(api_url, json=payload, headers=headers)
    if response.status_code == 201:
        print(f"Email successfully sent to {recipient_email}")
        return True
    else:
        print(f"!!! Failed to send email: {response.status_code} - {response.text}")
        return False

# --- Database Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    role = db.Column(db.String(20), nullable=False, default='employee')
    password = db.Column(db.String(128), nullable=False)

class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
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
        model = User
        load_instance = True
        exclude = ("password",) 

class ShiftSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Shift
        include_fk = True
        load_instance = True
    user = ma.Nested(UserSchema, only=("id", "username"))

class HolidaySchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Holiday
        include_fk = True
        load_instance = True
    user = ma.Nested(UserSchema, only=("id", "username"))

# Instantiate schemas
user_schema = UserSchema(); users_schema = UserSchema(many=True)
shift_schema = ShiftSchema(); shifts_schema = ShiftSchema(many=True)
holiday_schema = HolidaySchema(); holidays_schema = HolidaySchema(many=True)

# --- API Routes ---
@app.route('/')
def home(): return "Welcome!"

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json(); user = User.query.filter_by(email=data['email']).first()
    if user and bcrypt.check_password_hash(user.password, data['password']):
        return user_schema.jsonify(user)
    return jsonify({'message': 'Invalid credentials.'}), 401

@app.route('/users', methods=['POST'])
def add_user():
    data = request.get_json(); hashed_password = bcrypt.generate_password_hash(data['password']).decode('utf-8')
    new_user = User(username=data['username'], email=data['email'], password=hashed_password, role=data.get('role', 'employee'))
    db.session.add(new_user); db.session.commit()
    return user_schema.jsonify(new_user), 201

@app.route('/users', methods=['GET'])
def get_users(): return jsonify(users_schema.dump(User.query.all()))

@app.route('/shifts', methods=['GET'])
def get_shifts():
    start_date_str = request.args.get('start_date'); end_date_str = request.args.get('end_date')
    if not start_date_str or not end_date_str:
        today = datetime.utcnow(); start_date = today.replace(day=1); end_date = start_date + relativedelta(months=+1) - timedelta(days=1)
    else:
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '')); end_date = datetime.fromisoformat(end_date_str.replace('Z', ''))
    shifts = Shift.query.filter(Shift.start_time >= start_date, Shift.start_time <= end_date).all()
    return jsonify(shifts_schema.dump(shifts))

@app.route('/shifts', methods=['POST'])
def create_shifts():
    data = request.get_json(); is_recurring = data.get('is_recurring', False)
    if not is_recurring:
        new_shift = Shift(start_time=datetime.fromisoformat(data['start_time'].replace('Z', '')), end_time=datetime.fromisoformat(data['end_time'].replace('Z', '')), user_id=data.get('user_id'))
        db.session.add(new_shift); db.session.commit()
        return shift_schema.jsonify(new_shift), 201
    else:
        start_date = datetime.fromisoformat(data['start_time'].replace('Z', '')); shift_start_time = start_date.time()
        shift_end_time = datetime.fromisoformat(data['end_time'].replace('Z', '')).time(); duration_months = int(data.get('recurrence_months', 1))
        end_date = start_date + relativedelta(months=+duration_months); recurring_id = os.urandom(16).hex(); created_shifts = []
        current_date = start_date
        while current_date.date() < end_date.date():
            shift_start_dt = datetime.combine(current_date.date(), shift_start_time); shift_end_dt = datetime.combine(current_date.date(), shift_end_time)
            new_shift = Shift(start_time=shift_start_dt, end_time=shift_end_dt, user_id=data.get('user_id'), recurring_shift_id=recurring_id)
            db.session.add(new_shift); created_shifts.append(new_shift)
            current_date += timedelta(weeks=1)
        db.session.commit()
        return jsonify(shifts_schema.dump(created_shifts)), 201

# ... other endpoints ...

if __name__ == '__main__':
    app.run(debug=True)
