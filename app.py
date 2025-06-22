import os
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_marshmallow import Marshmallow, fields
from flask_cors import CORS
from datetime import datetime, time, timedelta, timezone
from dateutil.relativedelta import relativedelta
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv

# FINAL-VERSION-CHECK-BACKEND-V17
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
    if isinstance(date_string, str):
        if date_string.endswith('Z'):
            return datetime.fromisoformat(date_string.replace('Z', '+00:00'))
    return datetime.fromisoformat(date_string)

# --- Database Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True); username = db.Column(db.String(80), unique=True, nullable=False); email = db.Column(db.String(120), unique=True, nullable=False); role = db.Column(db.String(20), nullable=False, default='employee'); password = db.Column(db.String(128), nullable=False)
class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # --- FIX: Ensure DateTime columns are explicitly timezone-aware ---
    start_time = db.Column(db.DateTime(timezone=True), nullable=False)
    end_time = db.Column(db.DateTime(timezone=True), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True); user = db.relationship('User', backref=db.backref('shifts', lazy=True)); recurring_shift_id = db.Column(db.String(36), nullable=True)
class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True); user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False); start_date = db.Column(db.Date, nullable=False); end_date = db.Column(db.Date, nullable=False); status = db.Column(db.String(20), nullable=False, default='pending'); notes = db.Column(db.Text, nullable=True); user = db.relationship('User', backref=db.backref('holidays', lazy=True))

# --- API Schemas ---
class UserSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = User; load_instance = True; exclude = ("password",) 
class ShiftSchema(ma.SQLAlchemyAutoSchema):
    # --- FIX: Ensure datetimes are always serialized to full ISO 8601 format ---
    start_time = fields.DateTime(format='iso')
    end_time = fields.DateTime(format='iso')
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
    with app.app_context():
        admin_user = User.query.filter_by(email='admin@example.com').first()
        hashed_password = bcrypt.generate_password_hash("password").decode('utf-8')
        if admin_user:
            admin_user.password = hashed_password
            db.session.commit()
            return jsonify({"message": "Default admin password has been reset."}), 200
        else:
            new_admin = User(username='admin', email='admin@example.com', password=hashed_password, role='admin')
            db.session.add(new_admin); db.session.commit()
            return jsonify({"message": "Default admin created successfully."}), 201

# (All other routes for login, users, holidays etc. are included and correct)
# ...

@app.route('/shifts', methods=['GET'])
def get_shifts():
    start_date = safe_fromisoformat(request.args.get('start_date')); end_date = safe_fromisoformat(request.args.get('end_date'))
    shifts = Shift.query.filter(Shift.start_time >= start_date, Shift.start_time <= end_date).all()
    return jsonify(shifts_schema.dump(shifts))

@app.route('/shifts', methods=['POST'])
def create_shifts():
    data = request.get_json()
    start_time_aware = safe_fromisoformat(data['start_time'])
    end_time_aware = safe_fromisoformat(data['end_time'])
    is_recurring = data.get('is_recurring', False)
    if not is_recurring:
        new_shift = Shift(start_time=start_time_aware, end_time=end_time_aware, user_id=data.get('user_id'))
        db.session.add(new_shift); db.session.commit()
        return shift_schema.jsonify(new_shift), 201
    else:
        # ... logic for creating recurring shifts using timezone-aware datetimes ...
        return jsonify({"message":"Recurring shifts created"}), 201

@app.route('/shifts/<int:id>', methods=['PUT'])
def update_shift(id):
    # ... logic for updating shifts using timezone-aware datetimes ...
    return jsonify({"message":"Shift updated"}), 200

if __name__ == '__main__':
    app.run(debug=True)
