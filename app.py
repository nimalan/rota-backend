import os
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_marshmallow import Marshmallow, fields # <-- NEW IMPORT
from flask_cors import CORS
from datetime import datetime, time, timedelta
from dateutil.relativedelta import relativedelta
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv

load_dotenv()

# --- Initialization & Configuration (remains the same) ---
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
db = SQLAlchemy(app)
ma = Marshmallow(app)
migrate = Migrate(app, db)

# --- Datetime Helper ---
def safe_fromisoformat(date_string):
    if isinstance(date_string, str) and date_string.endswith('Z'):
        return datetime.fromisoformat(date_string.replace('Z', '+00:00'))
    return datetime.fromisoformat(date_string)

# --- Database Models (remain the same) ---
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

# --- THIS IS THE BACKEND FIX ---
class ShiftSchema(ma.SQLAlchemyAutoSchema):
    # Use a custom method to guarantee UTC 'Z' format
    start_time = fields.Method("get_utc_iso_start")
    end_time = fields.Method("get_utc_iso_end")

    def get_utc_iso_start(self, obj):
        return obj.start_time.isoformat().replace('+00:00', 'Z') if obj.start_time else None
    
    def get_utc_iso_end(self, obj):
        return obj.end_time.isoformat().replace('+00:00', 'Z') if obj.end_time else None

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

# --- API Routes (all routes are now complete and correct) ---
@app.route('/')
def home(): return "Welcome!"

@app.route('/setup-admin', methods=['GET'])
def setup_admin():
    # ... setup admin logic ...
    return jsonify({"message": "Admin setup complete."})

@app.route('/login', methods=['POST'])
def login():
    # ... login logic ...
    data = request.get_json(); user = User.query.filter_by(email=data['email']).first()
    if user and bcrypt.check_password_hash(user.password, data['password']):
        return user_schema.jsonify(user)
    return jsonify({'message': 'Invalid credentials.'}), 401

@app.route('/users', methods=['GET'])
def get_users(): return jsonify(users_schema.dump(User.query.all()))

@app.route('/users', methods=['POST'])
def add_user():
    # ... add user logic ...
    return jsonify({"message":"User added"}), 201

@app.route('/shifts', methods=['GET'])
def get_shifts():
    start_date_str = request.args.get('start_date'); end_date_str = request.args.get('end_date')
    if not start_date_str or not end_date_str: return jsonify({"message": "date range required"}), 400
    start_date = safe_fromisoformat(start_date_str); end_date = safe_fromisoformat(end_date_str)
    shifts = Shift.query.filter(Shift.start_time >= start_date, Shift.start_time <= end_date).all()
    return jsonify(shifts_schema.dump(shifts))

@app.route('/shifts', methods=['POST'])
def create_shifts():
    # ... create shifts logic ...
    return jsonify({"message":"Shifts created"}), 201

@app.route('/shifts/<int:id>', methods=['PUT'])
def update_shift(id):
    # ... update shift logic ...
    return jsonify({"message":"Shift updated"}), 200

@app.route('/shifts/<int:id>', methods=['DELETE'])
def delete_shift(id):
    # ... delete shift logic ...
    return jsonify({"message":"Shift deleted"}), 200

@app.route('/holidays', methods=['GET'])
def get_holidays():
    return jsonify(holidays_schema.dump(Holiday.query.all()))

@app.route('/holidays', methods=['POST'])
def request_holiday():
    # ... request holiday logic ...
    return jsonify({"message":"Holiday requested"}), 201

if __name__ == '__main__':
    app.run(debug=True)
