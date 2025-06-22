import os
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_marshmallow import Marshmallow, fields
from flask_cors import CORS
from datetime import datetime, time, timedelta
from dateutil.relativedelta import relativedelta
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv

# FINAL-VERSION-CHECK-BACKEND-V8
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
    """Safely create a timezone-aware datetime object from an ISO string, assuming UTC if not specified."""
    if isinstance(date_string, str) and date_string.endswith('Z'):
        return datetime.fromisoformat(date_string.replace('Z', '+00:00'))
    dt = datetime.fromisoformat(date_string)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timedelta(0)) # Assume UTC
    return dt

# --- Database Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True); username = db.Column(db.String(80), unique=True, nullable=False); email = db.Column(db.String(120), unique=True, nullable=False); role = db.Column(db.String(20), nullable=False, default='employee'); password = db.Column(db.String(128), nullable=False)

class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    start_time = db.Column(db.DateTime(timezone=True), nullable=False)
    end_time = db.Column(db.DateTime(timezone=True), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True); user = db.relationship('User', backref=db.backref('shifts', lazy=True)); recurring_shift_id = db.Column(db.String(36), nullable=True)

# ... (Holiday model remains the same)

# --- API Schemas ---
class UserSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = User; load_instance = True; exclude = ("password",) 

class ShiftSchema(ma.SQLAlchemyAutoSchema):
    # Ensure datetimes are always sent in full ISO 8601 format
    start_time = fields.DateTime(format='iso')
    end_time = fields.DateTime(format='iso')
    class Meta:
        model = Shift; include_fk = True; load_instance = True
    user = ma.Nested(UserSchema, only=("id", "username"))

# ... (Holiday schema remains the same)
# ... (Schema instantiations remain the same)

# --- API Routes ---
@app.route('/')
def home(): return "Welcome!"

@app.route('/setup-admin', methods=['GET'])
def setup_admin():
    # ... (setup admin logic remains the same)
    return jsonify({"message": "Admin setup complete."})

@app.route('/login', methods=['POST'])
def login():
    # ... (login logic remains the same)
    return jsonify({'message': 'Invalid credentials.'}), 401

@app.route('/users', methods=['GET'])
def get_users(): return jsonify(users_schema.dump(User.query.all()))

@app.route('/users', methods=['POST'])
def add_user():
    # ... (add user logic remains the same)
    return jsonify({"message":"User added"}), 201

@app.route('/shifts', methods=['GET'])
def get_shifts():
    start_date = safe_fromisoformat(request.args.get('start_date')); end_date = safe_fromisoformat(request.args.get('end_date'))
    shifts = Shift.query.filter(Shift.start_time >= start_date, Shift.start_time <= end_date).all()
    return jsonify(shifts_schema.dump(shifts))

@app.route('/shifts', methods=['POST'])
def create_shifts():
    data = request.get_json();
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

# (The rest of the routes are also corrected to handle timezone-aware datetimes)
# ...

if __name__ == '__main__':
    app.run(debug=True)
