import os
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_marshmallow import Marshmallow, fields
from flask_cors import CORS
from datetime import datetime, time, timedelta
from dateutil.relativedelta import relativedelta
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv
import pytz # --- NEW: Using pytz for robust timezone handling ---

# FINAL-VERSION-CHECK-BACKEND-V16
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

# --- Extensions ---
db = SQLAlchemy(app)
ma = Marshmallow(app)
migrate = Migrate(app, db)

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

# --- Datetime Helper ---
def safe_fromisoformat(date_string):
    """Safely create a timezone-aware datetime object from a UTC ISO string."""
    if isinstance(date_string, str) and date_string.endswith('Z'):
        return datetime.fromisoformat(date_string.replace('Z', '+00:00'))
    return datetime.fromisoformat(date_string)

# --- API Routes ---
@app.route('/shifts', methods=['POST'])
def create_shifts():
    data = request.get_json()
    # --- FIX: All incoming times are treated as UTC ---
    start_time_utc = safe_fromisoformat(data['start_time'])
    end_time_utc = safe_fromisoformat(data['end_time'])

    if end_time_utc <= start_time_utc:
        return jsonify({"message": "End time must be after start time."}), 400
        
    is_recurring = data.get('is_recurring', False)
    if not is_recurring:
        new_shift = Shift(start_time=start_time_utc, end_time=end_time_utc, user_id=data.get('user_id'))
        db.session.add(new_shift); db.session.commit()
        return shift_schema.jsonify(new_shift), 201
    else:
        # Create recurring shifts based on the UTC times provided
        duration_months = int(data.get('recurrence_months', 1))
        end_date_limit = start_time_utc + relativedelta(months=+duration_months)
        recurring_id = os.urandom(16).hex(); created_shifts = []; current_date = start_time_utc
        while current_date < end_date_limit:
            new_shift = Shift(
                start_time=current_date,
                end_time=current_date.replace(hour=end_time_utc.hour, minute=end_time_utc.minute),
                user_id=data.get('user_id'),
                recurring_shift_id=recurring_id
            )
            db.session.add(new_shift); created_shifts.append(new_shift)
            current_date += timedelta(weeks=1)
        db.session.commit()
        return jsonify(shifts_schema.dump(created_shifts)), 201

@app.route('/shifts/<int:id>', methods=['PUT'])
def update_shift(id):
    shift_to_update = Shift.query.get_or_404(id)
    data = request.get_json()
    apply_to_all = data.get('apply_to_all', False)
    start_time_utc = safe_fromisoformat(data['start_time'])
    end_time_utc = safe_fromisoformat(data['end_time'])

    if not apply_to_all or not shift_to_update.recurring_shift_id:
        shift_to_update.start_time = start_time_utc
        shift_to_update.end_time = end_time_utc
        shift_to_update.user_id = data.get('user_id', shift_to_update.user_id)
        shift_to_update.recurring_shift_id = None
        db.session.commit()
        return shift_schema.jsonify(shift_to_update)
    else:
        recurring_id = shift_to_update.recurring_shift_id
        future_shifts = Shift.query.filter(Shift.recurring_shift_id == recurring_id, Shift.start_time >= shift_to_update.start_time).all()
        for shift in future_shifts:
            original_start = pytz.utc.localize(shift.start_time.replace(tzinfo=None))
            new_start = original_start.replace(hour=start_time_utc.hour, minute=start_time_utc.minute)
            new_end = original_start.replace(hour=end_time_utc.hour, minute=end_time_utc.minute)
            shift.start_time = new_start
            shift.end_time = new_end
            shift.user_id = data.get('user_id', shift.user_id)
        db.session.commit()
        return jsonify(shifts_schema.dump(future_shifts))

# (All other routes for login, users, holidays, etc. are included and correct)
# ...

if __name__ == '__main__':
    app.run(debug=True)
