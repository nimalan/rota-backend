import os
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_marshmallow import Marshmallow
from flask_cors import CORS
from datetime import datetime
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

# --- UPDATED: Email Sending Function for Brevo (formerly Sendinblue) ---
def send_email(recipient_email, recipient_name, subject, body):
    """Sends an email using the Brevo API."""
    brevo_api_key = os.getenv('BREVO_API_KEY')
    sender_email = os.getenv('SENDER_EMAIL') # Your own email address
    sender_name = os.getenv('SENDER_NAME', 'Rota App') # A default sender name
    
    if not brevo_api_key or not sender_email:
        print("!!! Email not sent: BREVO_API_KEY or SENDER_EMAIL not set in .env file.")
        return False

    api_url = 'https://api.brevo.com/v3/smtp/email'
    
    headers = {
        'accept': 'application/json',
        'api-key': brevo_api_key,
        'content-type': 'application/json'
    }
    
    payload = {
        "sender": {
            "name": sender_name,
            "email": sender_email
        },
        "to": [
            {
                "email": recipient_email,
                "name": recipient_name
            }
        ],
        "subject": subject,
        "htmlContent": f"<html><body><p>{body.replace(os.linesep, '<br>')}</p></body></html>"
    }

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
    new_user = User(
        username=data['username'],
        email=data['email'],
        password=hashed_password,
        role=data.get('role', 'employee')
    )
    db.session.add(new_user)
    db.session.commit()
    subject = "Welcome to the Rota App!"
    body = f"Hi {new_user.username},{os.linesep}{os.linesep}Your account has been created.{os.linesep}You can now be assigned to shifts."
    send_email(new_user.email, new_user.username, subject, body)
    return user_schema.jsonify(new_user), 201

# --- UPDATED: Shift Management Endpoints with Notifications ---

@app.route('/shifts', methods=['POST'])
def add_shift():
    data = request.get_json()
    start_time = datetime.fromisoformat(data['start_time'].replace('Z', ''))
    end_time = datetime.fromisoformat(data['end_time'].replace('Z', ''))
    new_shift = Shift(start_time=start_time, end_time=end_time, user_id=data.get('user_id'))
    db.session.add(new_shift)
    db.session.commit()
    
    if new_shift.user_id:
        user = User.query.get(new_shift.user_id)
        if user and user.email:
            subject = "New Shift Assignment"
            body = f"Hi {user.username},{os.linesep}{os.linesep}You have been assigned a new shift:{os.linesep}{os.linesep}Date: {start_time.strftime('%A, %d %B %Y')}{os.linesep}Time: {start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}"
            send_email(user.email, user.username, subject, body)
            
    return shift_schema.jsonify(new_shift), 201

@app.route('/shifts/<int:id>', methods=['PUT'])
def update_shift(id):
    shift = Shift.query.get_or_404(id)
    original_user_id = shift.user_id
    
    data = request.get_json()
    shift.user_id = data.get('user_id', original_user_id)
    if 'start_time' in data:
        shift.start_time = datetime.fromisoformat(data['start_time'].replace('Z', ''))
    if 'end_time' in data:
        shift.end_time = datetime.fromisoformat(data['end_time'].replace('Z', ''))
        
    db.session.commit()

    new_user_id = shift.user_id
    if new_user_id != original_user_id:
        if original_user_id:
            original_user = User.query.get(original_user_id)
            if original_user and original_user.email:
                subject = "Shift Update: Cancellation"
                body = f"Hi {original_user.username},{os.linesep}{os.linesep}Your shift on {shift.start_time.strftime('%A, %d %B')} from {shift.start_time.strftime('%H:%M')} to {shift.end_time.strftime('%H:%M')} has been reassigned."
                send_email(original_user.email, original_user.username, subject, body)
        if new_user_id:
            new_user = User.query.get(new_user_id)
            if new_user and new_user.email:
                subject = "New Shift Assignment"
                body = f"Hi {new_user.username},{os.linesep}{os.linesep}You have been assigned a new shift:{os.linesep}{os.linesep}Date: {shift.start_time.strftime('%A, %d %B %Y')}{os.linesep}Time: {shift.start_time.strftime('%H:%M')} - {shift.end_time.strftime('%H:%M')}"
                send_email(new_user.email, new_user.username, subject, body)

    return shift_schema.jsonify(shift)

@app.route('/shifts/<int:id>', methods=['DELETE'])
def delete_shift(id):
    shift = Shift.query.get_or_404(id)
    user_id_to_notify = shift.user_id
    shift_details_for_email = f"Date: {shift.start_time.strftime('%A, %d %B %Y')}{os.linesep}Time: {shift.start_time.strftime('%H:%M')} - {shift.end_time.strftime('%H:%M')}"
    
    db.session.delete(shift)
    db.session.commit()
    
    if user_id_to_notify:
        user = User.query.get(user_id_to_notify)
        if user and user.email:
            subject = "Shift Cancellation"
            body = f"Hi {user.username},{os.linesep}{os.linesep}A shift assigned to you has been cancelled:{os.linesep}{os.linesep}{shift_details_for_email}"
            send_email(user.email, user.username, subject, body)
            
    return jsonify({'message': 'Shift deleted successfully.'})


# --- Other endpoints... ---
@app.route('/')
def home(): return "Welcome!"
@app.route('/users', methods=['GET'])
def get_users(): return jsonify(users_schema.dump(User.query.all()))
@app.route('/users/<int:id>', methods=['GET'])
def get_user(id): return user_schema.jsonify(User.query.get_or_404(id))
@app.route('/users/<int:id>', methods=['PUT'])
def update_user_details(id):
    user = User.query.get_or_404(id)
    data = request.get_json()
    user.username = data.get('username', user.username)
    user.email = data.get('email', user.email)
    user.role = data.get('role', user.role)
    db.session.commit()
    return user_schema.jsonify(user)
@app.route('/users/<int:id>', methods=['DELETE'])
def delete_user(id): 
    db.session.delete(User.query.get_or_404(id))
    db.session.commit()
    return jsonify({'message': 'User deleted.'})
@app.route('/shifts', methods=['GET'])
def get_shifts(): return jsonify(shifts_schema.dump(Shift.query.order_by(Shift.start_time).all()))
@app.route('/shifts/<int:id>', methods=['GET'])
def get_shift(id): return shift_schema.jsonify(Shift.query.get_or_404(id))

if __name__ == '__main__':
    app.run(debug=True)
