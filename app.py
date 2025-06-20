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

class RecurringShift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
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

# --- API Routes ---

@app.route('/')
def home(): return "Welcome to the Rota App Backend!"
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

# --- THIS IS THE FIX: The /shifts endpoint is now more flexible ---
@app.route('/shifts', methods=['GET'])
def get_shifts():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    # If date parameters are not provided, default to the current month.
    if not start_date_str or not end_date_str:
        today = datetime.utcnow()
        start_date = today.replace(day=1).date()
        next_month = today.replace(day=28) + timedelta(days=4)
        end_date = (next_month - timedelta(days=next_month.day)).date()
    else:
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '')).date()
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '')).date()

    # Get standard one-off shifts
    one_off_shifts = Shift.query.filter(db.func.date(Shift.start_time) >= start_date, db.func.date(Shift.start_time) <= end_date).all()
    
    # Generate recurring shifts
    generated_shifts = []
    recurring_templates = RecurringShift.query.all()
    
    current_date = start_date
    while current_date <= end_date:
        if current_date.weekday() in [template.day_of_week for template in recurring_templates]:
            for template in recurring_templates:
                if current_date.weekday() == template.day_of_week:
                    start_dt = datetime.combine(current_date, template.start_time)
                    end_dt = datetime.combine(current_date, template.end_time)
                    generated_shift = Shift(
                        id=f"rec-{template.id}-{current_date.strftime('%Y%m%d')}",
                        user_id=template.user_id,
                        start_time=start_dt,
                        end_time=end_dt,
                        user=template.user
                    )
                    generated_shifts.append(generated_shift)
        current_date += timedelta(days=1)

    all_shifts = one_off_shifts + generated_shifts
    return jsonify(shifts_schema.dump(all_shifts))

@app.route('/shifts', methods=['POST'])
def add_shift():
    data = request.get_json()
    start_time = datetime.fromisoformat(data['start_time'].replace('Z', ''))
    end_time = datetime.fromisoformat(data['end_time'].replace('Z', ''))
    new_shift = Shift(start_time=start_time, end_time=end_time, user_id=data.get('user_id'))
    db.session.add(new_shift)
    db.session.commit()
    # ... email notification logic ...
    return shift_schema.jsonify(new_shift), 201

@app.route('/recurring-shifts', methods=['GET'])
def get_recurring_shifts():
    templates = RecurringShift.query.order_by(RecurringShift.day_of_week, RecurringShift.start_time).all()
    return jsonify(recurring_shifts_schema.dump(templates))

@app.route('/recurring-shifts', methods=['POST'])
def add_recurring_shift():
    data = request.get_json()
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
    return jsonify({"message": "Recurring shift template deleted."})

# ... other endpoints like get_users, delete_user, etc.
@app.route('/users', methods=['GET'])
def get_users(): return jsonify(users_schema.dump(User.query.all()))
@app.route('/users/<int:id>', methods=['DELETE'])
def delete_user(id): 
    db.session.delete(User.query.get_or_404(id))
    db.session.commit()
    return jsonify({'message': 'User deleted.'})

if __name__ == '__main__':
    app.run(debug=True)
