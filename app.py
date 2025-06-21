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
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'rota.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Extensions ---
db = SQLAlchemy(app)
ma = Marshmallow(app)
migrate = Migrate(app, db)

# --- Database Models and Schemas ---
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

class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')
    notes = db.Column(db.Text, nullable=True)
    user = db.relationship('User', backref=db.backref('holidays', lazy=True))

class UserSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = User; load_instance = True; exclude = ("password",)
class ShiftSchema(ma.SQLAlchemyAutoSchema):
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

# --- UPDATED: One-Time Admin Setup/Reset Route ---
@app.route('/setup-admin', methods=['GET'])
def setup_admin():
    with app.app_context():
        # Find the admin user by email
        admin_user = User.query.filter_by(email='admin@example.com').first()
        
        hashed_password = bcrypt.generate_password_hash("password").decode('utf-8')

        if admin_user:
            # If the admin exists, just update their password to ensure it's correct
            admin_user.password = hashed_password
            db.session.commit()
            print("Default admin password has been reset.")
            return jsonify({"message": "Default admin password has been reset. You can now log in."}), 200
        else:
            # If the admin does not exist, create it
            print("No admin user found. Creating default admin...")
            new_admin = User(
                username='admin',
                email='admin@example.com',
                password=hashed_password,
                role='admin'
            )
            db.session.add(new_admin)
            db.session.commit()
            print("Default admin created successfully.")
            return jsonify({"message": "Default admin created successfully. You can now log in."}), 201

# (All other routes like /login, /users, /shifts, etc. should be included here)
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data.get('email')).first()

    if user and bcrypt.check_password_hash(user.password, data.get('password', '')):
        return user_schema.jsonify(user)
    
    return jsonify({'message': 'Invalid credentials'}), 401

# ... other routes

if __name__ == '__main__':
    app.run(debug=True)
