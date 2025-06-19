import os
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_marshmallow import Marshmallow
from flask_cors import CORS
from datetime import datetime, time, timedelta
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
    brevo_api_key = os.getenv('BREVO_API_KEY')
    sender_email = os.getenv('SENDER_EMAIL')
    sender_name = os.getenv('SENDER_NAME', 'Rota App')
    if not brevo_api_key or not sender_email:
        print("!!! Email not sent: BREVO_API_KEY or SENDER_EMAIL not set.")
        return False
    # ... (email sending logic remains the same)
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

# --- NEW: Recurring Shift Model ---
class RecurringShift(db.Model):
    """Stores the templates for weekly recurring shifts."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # Day of the week: 0=Monday, 1=Tuesday, ..., 6=Sunday
    day_of_week = db.Column(db.Integer, nullable=False) 
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    user = db.relationship('User', backref=db.backref('recurring_shifts', lazy=True))


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

# --- NEW: Recurring Shift Schema ---
class RecurringShiftSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = RecurringShift
        include_fk = True
        load_instance = True
    user = ma.Nested(UserSchema, only=("id", "username"))

# Instantiate schemas
user_schema = UserSchema()
users_schema = UserSchema(many=True)
shift_schema = ShiftSchema()
shifts_schema = ShiftSchema(many=True)
recurring_shift_schema = RecurringShiftSchema()
recurring_shifts_schema = RecurringShiftSchema(many=True)
@app.route('/')
def home():
    """A simple welcome route to confirm the backend is running."""
    return "Welcome to the Rota App Backend!"

# --- API Routes ---

# (Login and User routes remain the same)
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
    send_email(new_user.email, new_user.username, "Welcome to the Rota App!", f"Hi {new_user.username},{os.linesep}{os.linesep}Your account has been created.")
    return user_schema.jsonify(new_user), 201

# --- UPDATED: Shift Endpoints ---
@app.route('/shifts', methods=['GET'])
def get_shifts():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if not start_date_str or not end_date_str:
        return jsonify({"message": "start_date and end_date parameters are required"}), 400

    start_date = datetime.fromisoformat(start_date_str).date()
    end_date = datetime.fromisoformat(end_date_str).date()

    # 1. Get standard one-off shifts
    one_off_shifts = Shift.query.filter(Shift.start_time >= start_date, Shift.start_time <= end_date).all()
    
    # 2. Generate recurring shifts for the date range
    generated_shifts = []
    recurring_templates = RecurringShift.query.all()
    
    current_date = start_date
    while current_date <= end_date:
        for template in recurring_templates:
            if current_date.weekday() == template.day_of_week:
                # Combine date with time to create a full datetime object
                start_dt = datetime.combine(current_date, template.start_time)
                end_dt = datetime.combine(current_date, template.end_time)
                
                # Create a temporary Shift object (without saving it)
                generated_shift = Shift(
                    id=f"rec-{template.id}-{current_date.strftime('%Y%m%d')}", # Unique ID for the frontend
                    user_id=template.user_id,
                    start_time=start_dt,
                    end_time=end_dt,
                    user=template.user # Associate the user object
                )
                generated_shifts.append(generated_shift)
        current_date += timedelta(days=1)

    # 3. Combine both lists
    all_shifts = one_off_shifts + generated_shifts
    return jsonify(shifts_schema.dump(all_shifts))

# (POST, PUT, DELETE for one-off shifts remain largely the same)
@app.route('/shifts', methods=['POST'])
def add_shift():
    # This now only creates one-off shifts
    data = request.get_json()
    start_time = datetime.fromisoformat(data['start_time'].replace('Z', ''))
    end_time = datetime.fromisoformat(data['end_time'].replace('Z', ''))
    new_shift = Shift(start_time=start_time, end_time=end_time, user_id=data.get('user_id'))
    db.session.add(new_shift)
    db.session.commit()
    # ... (email notification logic)
    return shift_schema.jsonify(new_shift), 201

# --- NEW: Endpoints for Recurring Shift Templates ---
@app.route('/recurring-shifts', methods=['GET'])
def get_recurring_shifts():
    templates = RecurringShift.query.order_by(RecurringShift.day_of_week, RecurringShift.start_time).all()
    return jsonify(recurring_shifts_schema.dump(templates))

@app.route('/recurring-shifts', methods=['POST'])
def add_recurring_shift():
    data = request.get_json()
    if 'user_id' not in data or 'day_of_week' not in data or 'start_time' not in data or 'end_time' not in data:
        return jsonify({"message": "Missing required fields"}), 400
        
    new_template = RecurringShift(
        user_id=data['user_id'],
        day_of_week=data['day_of_week'],
        start_time=time.fromisoformat(data['start_time']),
        end_time=time.fromisoformat(data['end_time'])
    )
    db.session.add(new_template)
    db.session.commit()
    return recurring_shift_schema.jsonify(new_template), 201

@app.route('/recurring-shifts/<int:id>', methods=['DELETE'])
def delete_recurring_shift(id):
    template = RecurringShift.query.get_or_404(id)
    db.session.delete(template)
    db.session.commit()
    return jsonify({"message": "Recurring shift template deleted successfully."})


# (Other endpoints like get_users, delete_user, etc. are omitted for brevity but should remain in your file)
@app.route('/users', methods=['GET'])
def get_users(): return jsonify(users_schema.dump(User.query.all()))
@app.route('/users/<int:id>', methods=['DELETE'])
def delete_user(id): 
    db.session.delete(User.query.get_or_404(id))
    db.session.commit()
    return jsonify({'message': 'User deleted.'})

if __name__ == '__main__':
    app.run(debug=True)
