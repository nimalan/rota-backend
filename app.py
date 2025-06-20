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
    user = ma.Nested(UserSchema, only=("id", "username", "email"))

# Instantiate schemas
user_schema = UserSchema()
users_schema = UserSchema(many=True)
shift_schema = ShiftSchema()
shifts_schema = ShiftSchema(many=True)

# --- API Routes ---
@app.route('/')
def home(): return "Welcome!"

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data['email']).first()
    if user and bcrypt.check_password_hash(user.password, data['password']):
        return user_schema.jsonify(user)
    return jsonify({'message': 'Invalid credentials.'}), 401

@app.route('/users', methods=['POST'])
def add_user():
    data = request.get_json()
    hashed_password = bcrypt.generate_password_hash(data['password']).decode('utf-8')
    new_user = User(username=data['username'], email=data['email'], password=hashed_password, role=data.get('role', 'employee'))
    db.session.add(new_user)
    db.session.commit()
    # ... email logic ...
    return user_schema.jsonify(new_user), 201

# --- CORRECTED SHIFT ENDPOINT ---
@app.route('/shifts', methods=['GET'])
def get_shifts():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    # If date parameters are not provided, default to the current month.
    if not start_date_str or not end_date_str:
        today = datetime.utcnow()
        start_date = today.replace(day=1)
        end_date = start_date + relativedelta(months=+1) - timedelta(days=1)
    else:
        start_date = datetime.fromisoformat(start_date_str.replace('Z', ''))
        end_date = datetime.fromisoformat(end_date_str.replace('Z', ''))

    shifts = Shift.query.filter(Shift.start_time >= start_date, Shift.start_time <= end_date).all()
    return jsonify(shifts_schema.dump(shifts))


@app.route('/shifts', methods=['POST'])
def create_shifts():
    data = request.get_json()
    is_recurring = data.get('is_recurring', False)
    
    if not is_recurring:
        start_time = datetime.fromisoformat(data['start_time'].replace('Z', ''))
        end_time = datetime.fromisoformat(data['end_time'].replace('Z', ''))
        new_shift = Shift(start_time=start_time, end_time=end_time, user_id=data.get('user_id'))
        db.session.add(new_shift)
        db.session.commit()
        return shift_schema.jsonify(new_shift), 201
    else:
        start_date = datetime.fromisoformat(data['start_time'].replace('Z', ''))
        shift_start_time = start_date.time()
        shift_end_time = datetime.fromisoformat(data['end_time'].replace('Z', '')).time()
        duration_months = int(data.get('recurrence_months', 1))
        end_date = start_date + relativedelta(months=+duration_months)
        recurring_id = os.urandom(16).hex()
        created_shifts = []
        current_date = start_date
        while current_date.date() < end_date.date():
            shift_start_dt = datetime.combine(current_date.date(), shift_start_time)
            shift_end_dt = datetime.combine(current_date.date(), shift_end_time)
            new_shift = Shift(start_time=shift_start_dt, end_time=shift_end_dt, user_id=data.get('user_id'), recurring_shift_id=recurring_id)
            db.session.add(new_shift)
            created_shifts.append(new_shift)
            current_date += timedelta(weeks=1)
        db.session.commit()
        return jsonify(shifts_schema.dump(created_shifts)), 201

@app.route('/shifts/<int:id>', methods=['PUT'])
def update_shift(id):
    shift_to_update = Shift.query.get_or_404(id)
    data = request.get_json()
    apply_to_all = data.get('apply_to_all', False)
    if not apply_to_all or not shift_to_update.recurring_shift_id:
        shift_to_update.start_time = datetime.fromisoformat(data['start_time'].replace('Z', ''))
        shift_to_update.end_time = datetime.fromisoformat(data['end_time'].replace('Z', ''))
        shift_to_update.user_id = data.get('user_id', shift_to_update.user_id)
        shift_to_update.recurring_shift_id = None 
        db.session.commit()
        return shift_schema.jsonify(shift_to_update)
    else:
        recurring_id = shift_to_update.recurring_shift_id
        future_shifts = Shift.query.filter(Shift.recurring_shift_id == recurring_id, Shift.start_time >= shift_to_update.start_time).all()
        new_start_time = datetime.fromisoformat(data['start_time'].replace('Z', '')).time()
        new_end_time = datetime.fromisoformat(data['end_time'].replace('Z', '')).time()
        for shift in future_shifts:
            shift.start_time = datetime.combine(shift.start_time.date(), new_start_time)
            shift.end_time = datetime.combine(shift.end_time.date(), new_end_time)
            shift.user_id = data.get('user_id', shift.user_id)
        db.session.commit()
        return jsonify(shifts_schema.dump(future_shifts))

@app.route('/shifts/<int:id>', methods=['DELETE'])
def delete_shift(id):
    shift_to_delete = Shift.query.get_or_404(id)
    data = request.get_json() or {}
    apply_to_all = data.get('apply_to_all', False)
    if not apply_to_all or not shift_to_delete.recurring_shift_id:
        db.session.delete(shift_to_delete)
        db.session.commit()
        return jsonify({'message': 'Shift deleted successfully.'})
    else:
        recurring_id = shift_to_delete.recurring_shift_id
        future_shifts = Shift.query.filter(Shift.recurring_shift_id == recurring_id, Shift.start_time >= shift_to_delete.start_time).all()
        for shift in future_shifts:
            db.session.delete(shift)
        db.session.commit()
        return jsonify({'message': f'{len(future_shifts)} recurring shifts deleted.'})

# Other user management endpoints...
@app.route('/users', methods=['GET'])
def get_users(): return jsonify(users_schema.dump(User.query.all()))
@app.route('/users/<int:id>', methods=['DELETE'])
def delete_user(id): 
    db.session.delete(User.query.get_or_404(id))
    db.session.commit()
    return jsonify({'message': 'User deleted.'})

if __name__ == '__main__':
    app.run(debug=True)
