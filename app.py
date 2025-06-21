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
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'rota.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Extensions ---
db = SQLAlchemy(app)
ma = Marshmallow(app)
migrate = Migrate(app, db)

def send_email(recipient_email, recipient_name, subject, body):
    # (Email sending logic remains the same)
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

# --- NEW: Holiday Model ---
class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending') # pending, approved, rejected
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

# --- NEW: Holiday Schema ---
class HolidaySchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Holiday
        include_fk = True
        load_instance = True
    user = ma.Nested(UserSchema, only=("id", "username"))

# Instantiate schemas
user_schema = UserSchema()
users_schema = UserSchema(many=True)
shift_schema = ShiftSchema()
shifts_schema = ShiftSchema(many=True)
holiday_schema = HolidaySchema()
holidays_schema = HolidaySchema(many=True)

# --- API Routes ---
# (Login and User routes remain the same)
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

# --- SHIFT ENDPOINTS ---
@app.route('/shifts', methods=['GET'])
def get_shifts():
    start_date = datetime.fromisoformat(request.args.get('start_date')).date()
    end_date = datetime.fromisoformat(request.args.get('end_date')).date()
    shifts = Shift.query.filter(db.func.date(Shift.start_time) >= start_date, db.func.date(Shift.start_time) <= end_date).all()
    return jsonify(shifts_schema.dump(shifts))

# ... (other shift endpoints remain the same, logic for notifications etc. would be added here)
@app.route('/shifts', methods=['POST'])
def create_shifts():
    data = request.get_json()
    # ... logic for creating single vs recurring shifts ...
    return jsonify({"message": "Shifts created"}), 201

# --- NEW: HOLIDAY ENDPOINTS ---
@app.route('/holidays', methods=['GET'])
def get_holidays():
    """Fetches all holidays, optionally filtered by status."""
    status = request.args.get('status')
    query = Holiday.query
    if status:
        query = query.filter(Holiday.status == status)
    holidays = query.order_by(Holiday.start_date).all()
    return jsonify(holidays_schema.dump(holidays))

@app.route('/holidays', methods=['POST'])
def request_holiday():
    """Endpoint for a user to request a new holiday."""
    data = request.get_json()
    if not all(k in data for k in ['user_id', 'start_date', 'end_date']):
        return jsonify({"message": "Missing required fields"}), 400
    
    new_holiday = Holiday(
        user_id=data['user_id'],
        start_date=datetime.fromisoformat(data['start_date']).date(),
        end_date=datetime.fromisoformat(data['end_date']).date(),
        notes=data.get('notes'),
        status='pending' # Always starts as pending
    )
    db.session.add(new_holiday)
    db.session.commit()
    # ... notification to admin could be sent here ...
    return holiday_schema.jsonify(new_holiday), 201

@app.route('/holidays/<int:id>', methods=['PUT'])
def amend_holiday(id):
    """Endpoint for an admin to approve, reject, or change a holiday."""
    holiday = Holiday.query.get_or_404(id)
    data = request.get_json()
    
    # Update status if provided
    if 'status' in data and data['status'] in ['approved', 'rejected']:
        holiday.status = data['status']
        # ... notification to user could be sent here ...

    # Allow amending dates
    if 'start_date' in data:
        holiday.start_date = datetime.fromisoformat(data['start_date']).date()
    if 'end_date' in data:
        holiday.end_date = datetime.fromisoformat(data['end_date']).date()
        
    db.session.commit()
    return holiday_schema.jsonify(holiday)

@app.route('/holidays/<int:id>', methods=['DELETE'])
def delete_holiday(id):
    """Endpoint to delete a holiday request."""
    holiday = Holiday.query.get_or_404(id)
    db.session.delete(holiday)
    db.session.commit()
    return jsonify({"message": "Holiday request deleted."})

if __name__ == '__main__':
    app.run(debug=True)
