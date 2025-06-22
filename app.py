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
    
    start_time = ma.DateTime(format='iso')
    end_time = ma.DateTime(format='iso')
    user = ma.Nested(UserSchema, only=("id", "username", "email"))

# Initialize schemas
user_schema = UserSchema()
users_schema = UserSchema(many=True)
shift_schema = ShiftSchema()
shifts_schema = ShiftSchema(many=True)

# --- API Routes ---
@app.route('/shifts', methods=['POST'])
def create_shifts():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"message": "No data provided"}), 400

        # Validate required fields
        required_fields = ['start_time', 'end_time']
        if not all(field in data for field in required_fields):
            return jsonify({"message": f"Missing required fields: {', '.join(required_fields)}"}), 400

        # Parse and validate times
        start_time = parse_iso_datetime(data['start_time'])
        end_time = parse_iso_datetime(data['end_time'])
        validate_shift_times(start_time, end_time)

        # Get user_id if provided
        user_id = data.get('user_id')

        # Check if this is a recurring shift
        is_recurring = data.get('is_recurring', False)
        
        if not is_recurring:
            # Create single shift
            new_shift = Shift(
                start_time=start_time,
                end_time=end_time,
                user_id=user_id
            )
            db.session.add(new_shift)
            db.session.commit()
            return shift_schema.jsonify(new_shift), 201
        else:
            # Create recurring shifts
            recurrence_interval = data.get('recurrence_interval', 'weekly').lower()
            recurrence_count = int(data.get('recurrence_count', 4))  # Default to 4 occurrences
            
            if recurrence_interval not in ['daily', 'weekly', 'monthly']:
                return jsonify({"message": "Invalid recurrence interval"}), 400

            recurring_id = str(uuid.uuid4())
            created_shifts = []
            current_date = start_time

            for _ in range(recurrence_count):
                shift_start = current_date
                shift_end = end_time.replace(
                    year=current_date.year,
                    month=current_date.month,
                    day=current_date.day
                )

                new_shift = Shift(
                    start_time=shift_start,
                    end_time=shift_end,
                    user_id=user_id,
                    recurring_shift_id=recurring_id
                )
                db.session.add(new_shift)
                created_shifts.append(new_shift)

                # Calculate next occurrence
                if recurrence_interval == 'daily':
                    current_date += timedelta(days=1)
                elif recurrence_interval == 'weekly':
                    current_date += timedelta(weeks=1)
                elif recurrence_interval == 'monthly':
                    current_date += relativedelta(months=1)

            db.session.commit()
            return jsonify(shifts_schema.dump(created_shifts)), 201

    except ValueError as e:
        return jsonify({"message": "Invalid input", "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error creating shifts: {str(e)}")
        return jsonify({"message": "Failed to create shifts", "error": str(e)}), 500

@app.route('/shifts', methods=['GET'])
def get_shifts():
    try:
        shifts = Shift.query.order_by(Shift.start_time.asc()).all()
        return jsonify(shifts_schema.dump(shifts))
    except Exception as e:
        app.logger.error(f"Error fetching shifts: {str(e)}")
        return jsonify({"message": "Failed to fetch shifts", "error": str(e)}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # Create tables if they don't exist
    app.run(debug=True)
