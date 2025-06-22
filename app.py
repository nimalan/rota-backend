import os
import uuid
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from dateutil.parser import isoparse

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_marshmallow import Marshmallow
from flask_cors import CORS
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

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or \
    'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'rota.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

if db_url:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "connect_args": {"options": "-c timezone=utc"}
    }

# --- Extensions ---
db = SQLAlchemy(app)
ma = Marshmallow(app)
migrate = Migrate(app, db)

# --- Helper Functions ---
def parse_iso_datetime(date_string):
    """Parse ISO 8601 datetime string with proper timezone handling"""
    try:
        dt = isoparse(date_string)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid datetime format: {date_string}") from e

def validate_shift_times(start_time, end_time):
    """Validate that shift times are logical"""
    if start_time >= end_time:
        raise ValueError("End time must be after start time")
    if (end_time - start_time) > timedelta(hours=24):
        raise ValueError("Shift duration cannot exceed 24 hours")

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
    recurring_shift_id = db.Column(db.String(36), nullable=True, index=True)

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
    start_time = ma.DateTime(format='iso')
    end_time = ma.DateTime(format='iso')
    
    class Meta:
        model = Shift
        include_fk = True
        load_instance = True
    
    user = ma.Nested(UserSchema, only=("id", "username", "email"))

class HolidaySchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Holiday
        include_fk = True
        load_instance = True
    
    user = ma.Nested(UserSchema, only=("id", "username"))

# Initialize schemas
user_schema = UserSchema()
users_schema = UserSchema(many=True)
shift_schema = ShiftSchema()
shifts_schema = ShiftSchema(many=True)
holiday_schema = HolidaySchema()
holidays_schema = HolidaySchema(many=True)

# --- API Routes ---
@app.route('/')
def home():
    return jsonify({"message": "Welcome to the Rota API"})

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        if not data or 'email' not in data or 'password' not in data:
            return jsonify({'message': 'Email and password are required'}), 400
        
        user = User.query.filter_by(email=data['email']).first()
        if user and bcrypt.check_password_hash(user.password, data['password']):
            return user_schema.jsonify(user)
        
        return jsonify({'message': 'Invalid credentials'}), 401
    except Exception as e:
        return jsonify({'message': 'Login failed', 'error': str(e)}), 500

@app.route('/users', methods=['GET'])
def get_users():
    try:
        users = User.query.all()
        return jsonify(users_schema.dump(users))
    except Exception as e:
        return jsonify({'message': 'Failed to fetch users', 'error': str(e)}), 500

@app.route('/shifts', methods=['GET'])
def get_shifts():
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        
        if not start_date_str or not end_date_str:
            return jsonify({"message": "Both start_date and end_date parameters are required"}), 400
        
        start_date = parse_iso_datetime(start_date_str)
        end_date = parse_iso_datetime(end_date_str)
        
        if start_date > end_date:
            return jsonify({"message": "start_date must be before end_date"}), 400
        
        shifts = Shift.query.filter(
            Shift.start_time >= start_date,
            Shift.start_time <= end_date
        ).all()
        
        return jsonify(shifts_schema.dump(shifts))
    except ValueError as e:
        return jsonify({"message": "Invalid date format", "error": str(e)}), 400
    except Exception as e:
        return jsonify({"message": "Failed to fetch shifts", "error": str(e)}), 500

@app.route('/shifts', methods=['POST'])
def create_shifts():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"message": "No data provided"}), 400

        required_fields = ['start_time', 'end_time']
        if not all(field in data for field in required_fields):
            return jsonify({"message": f"Missing required fields: {', '.join(required_fields)}"}), 400

        start_time = parse_iso_datetime(data['start_time'])
        end_time = parse_iso_datetime(data['end_time'])
        validate_shift_times(start_time, end_time)

        is_recurring = data.get('is_recurring', False)
        user_id = data.get('user_id')

        if not is_recurring:
            new_shift = Shift(
                start_time=start_time,
                end_time=end_time,
                user_id=user_id
            )
            db.session.add(new_shift)
            db.session.commit()
            return shift_schema.jsonify(new_shift), 201
        else:
            # Recurring shift logic
            recurrence_months = int(data.get('recurrence_months', 1))
            recurrence_interval = data.get('recurrence_interval', 'weekly').lower()
            
            if recurrence_interval not in ['daily', 'weekly', 'monthly']:
                return jsonify({"message": "Invalid recurrence interval"}), 400

            end_date = start_time + relativedelta(months=+recurrence_months)
            recurring_id = str(uuid.uuid4())
            created_shifts = []
            current_date = start_time

            while current_date.date() <= end_date.date():
                shift_start = current_date.replace(
                    hour=start_time.hour,
                    minute=start_time.minute,
                    second=0,
                    microsecond=0
                )
                shift_end = current_date.replace(
                    hour=end_time.hour,
                    minute=end_time.minute,
                    second=0,
                    microsecond=0
                )

                new_shift = Shift(
                    start_time=shift_start,
                    end_time=shift_end,
                    user_id=user_id,
                    recurring_shift_id=recurring_id
                )
                db.session.add(new_shift)
                created_shifts.append(new_shift)

                # Increment based on recurrence pattern
                if recurrence_interval == 'daily':
                    current_date += timedelta(days=1)
                elif recurrence_interval == 'weekly':
                    current_date += timedelta(weeks=1)
                elif recurrence_interval == 'monthly':
                    current_date += relativedelta(months=+1)

            db.session.commit()
            return jsonify(shifts_schema.dump(created_shifts)), 201

    except ValueError as e:
        return jsonify({"message": "Invalid input", "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": "Failed to create shifts", "error": str(e)}), 500

@app.route('/shifts/<int:id>', methods=['PUT'])
def update_shift(id):
    try:
        shift_to_update = Shift.query.get_or_404(id)
        data = request.get_json()
        
        if not data:
            return jsonify({"message": "No data provided"}), 400

        required_fields = ['start_time', 'end_time']
        if not all(field in data for field in required_fields):
            return jsonify({"message": f"Missing required fields: {', '.join(required_fields)}"}), 400

        new_start_time = parse_iso_datetime(data['start_time'])
        new_end_time = parse_iso_datetime(data['end_time'])
        validate_shift_times(new_start_time, new_end_time)

        apply_to_all = data.get('apply_to_all', False)
        user_id = data.get('user_id', shift_to_update.user_id)

        if not apply_to_all or not shift_to_update.recurring_shift_id:
            # Update single shift
            shift_to_update.start_time = new_start_time
            shift_to_update.end_time = new_end_time
            shift_to_update.user_id = user_id
            shift_to_update.recurring_shift_id = None
            db.session.commit()
            return shift_schema.jsonify(shift_to_update)
        else:
            # Update all future recurring shifts
            recurring_id = shift_to_update.recurring_shift_id
            future_shifts = Shift.query.filter(
                Shift.recurring_shift_id == recurring_id,
                Shift.start_time >= shift_to_update.start_time
            ).all()

            for shift in future_shifts:
                shift.start_time = shift.start_time.replace(
                    hour=new_start_time.hour,
                    minute=new_start_time.minute
                )
                shift.end_time = shift.end_time.replace(
                    hour=new_end_time.hour,
                    minute=new_end_time.minute
                )
                shift.user_id = user_id

            db.session.commit()
            return jsonify(shifts_schema.dump(future_shifts))

    except ValueError as e:
        return jsonify({"message": "Invalid input", "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": "Failed to update shift", "error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
