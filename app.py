from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, timezone
from OncoAuth.models import db, RolesMaster, MenuMaster, RoleMenus, UserDetails, ProviderDetails, InsuranceDetails,FileClaims,FileOrders
from OncoAuth.models import CPTMaster, ICDMaster, Drug, Facility, ClaimsDetails, PatientDetails, Orders, Prescrubbing, ClaimNotes
from OncoAuth.models import AuditLog, ErrorLog
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask import jsonify
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import time
from werkzeug.utils import secure_filename
from flask import render_template
from sqlalchemy import func, extract
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response
from flask_login import LoginManager, login_required, current_user, UserMixin
from flask import request
from functools import wraps
from wtforms import StringField, PasswordField
from wtforms.validators import InputRequired
from flask_wtf import FlaskForm
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect
import pyotp, qrcode
from io import BytesIO
import base64
from flask import send_file
#from weasyprint import HTML
from dotenv import load_dotenv
from uuid import uuid4
from OncoAuth.rpa.npi_lookup import get_provider_id_by_name  
from flask_wtf.csrf import CSRFProtect
#from eligibilitydb import run_eligibility_rpa # type: ignore
from sqlalchemy import func
import importlib.util
import logging
import threading
import uuid
import json

app = Flask(__name__)
load_dotenv()  # This should be called before using os.getenv()
logging.basicConfig(level=logging.INFO)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
db.init_app(app)
csrf = CSRFProtect(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
now = datetime.now(timezone.utc)

# ===== MFA SESSION STORE =====
mfa_store = {}
mfa_lock = threading.Lock()
CODE_TTL_SECONDS = 300  # expire after 5 minutes

def cleanup_expired():
    while True:
        now = time.time()
        with mfa_lock:
            expired = [sid for sid,(code,ts) in mfa_store.items() if now-ts > CODE_TTL_SECONDS]
            for sid in expired:
                del mfa_store[sid]
        time.sleep(60)

# start cleanup thread at app startup
threading.Thread(target=cleanup_expired, daemon=True).start()

# ===== MFA ROUTES =====
@app.route('/mfa-request', methods=['POST'])
@csrf.exempt
def mfa_request():
    """Generate a new MFA session ID for RPA script"""
    sid = str(uuid.uuid4())
    with mfa_lock:
        mfa_store[sid] = (None, time.time())
    return jsonify({'session_id': sid}), 200

@app.route('/mfa-submit', methods=['POST'])
@csrf.exempt
def mfa_submit():
    """Store MFA code submitted by user"""
    data = request.get_json() or {}
    sid, code = data.get('session_id'), data.get('code')
    if not sid or not code:
        return jsonify({'error': 'session_id and code required'}), 400
     
    with mfa_lock:
        if sid not in mfa_store:
            return jsonify({'error': 'invalid or expired session_id'}), 404
        # overwrite the code, keep original timestamp
        _, ts = mfa_store[sid]
        mfa_store[sid] = (code, ts)
    return jsonify({'status': 'received'}), 200

@app.route('/mfa-check/<session_id>', methods=['GET'])
@csrf.exempt
def mfa_check(session_id):
    """Return MFA code for RPA script polling"""
    with mfa_lock:
        entry = mfa_store.get(session_id)
    if not entry:
        return jsonify({'error': 'invalid or expired session_id'}), 404
    code, ts = entry
    return jsonify({'code': code}), 200

@app.route('/mfa-pending', methods=['GET'])
@login_required
def mfa_pending():
    """Check for pending MFA sessions and trigger modal"""
    session_id = request.args.get('session_id')
    if session_id:
        with mfa_lock:
            if session_id in mfa_store:
                return jsonify({'pending': True, 'session_id': session_id})
    return jsonify({'pending': False})

@login_manager.user_loader
def load_user(user_id):
    return UserDetails.query.get(int(user_id))

class LoginForm(FlaskForm):
    username = StringField("Username", validators=[InputRequired()])
    password = PasswordField("Password", validators=[InputRequired()])

def audit_page_view(page_name=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            log_audit("PAGE_VIEW", page_name=page_name or request.path)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@app.before_request
def keep_db_alive():
    try:
        db.session.execute('SELECT 1')
    except:
        db.session.rollback()

from OncoAuth.models import db, Prescrubbing, PatientDetails, ProviderDetails, Orders

from OncoAuth.models import Prescrubbing, PatientDetails, ProviderDetails, Orders  

from sqlalchemy import func

def get_data_for_rpa(auth_id):
    from OncoAuth.models import db, Prescrubbing, PatientDetails, Orders, ProviderDetails

    result = db.session.query(
        Prescrubbing.auth_id,
        Prescrubbing.auth_status,
        Prescrubbing.cpt_validation_status,
        Prescrubbing.insurance_validation_status,
        Prescrubbing.npi_validation_status,
        PatientDetails.patient_id.label("patient_id"),
        PatientDetails.full_name.label("patient_name"),
        PatientDetails.date_of_birth,
        PatientDetails.gender,
        PatientDetails.primary_insurance,
        PatientDetails.primary_policy_number,
        PatientDetails.primary_subscriber_name,
        PatientDetails.primary_subscriber_type,
        ProviderDetails.provider_name,
        ProviderDetails.npi_number,
        ProviderDetails.tax_id,
        func.coalesce(Orders.procedure_code, Orders.cpt_code).label("procedure_code"),  
        Orders.icd_code.label("diagnosis_code"),
        Orders.from_date_of_service,
        Orders.to_date_of_service
    ).join(
        PatientDetails, Prescrubbing.patient_id == PatientDetails.patient_id
    ).join(
        Orders, Prescrubbing.auth_id == Orders.auth_id
    ).join(
        ProviderDetails, PatientDetails.provider_id == ProviderDetails.provider_id
    ).filter(
        Prescrubbing.auth_id == auth_id,
        PatientDetails.primary_insurance.ilike('%aetna%')
    ).first()

    if result:
        return {
            "auth_id": result.auth_id,
            "auth_status": result.auth_status,
            "cpt_validation_status": result.cpt_validation_status,
            "insurance_validation_status": result.insurance_validation_status,
            "npi_validation_status": result.npi_validation_status,
            "member_id": result.primary_policy_number,
            "patient_name": result.patient_name,
            "date_of_birth": str(result.date_of_birth) if result.date_of_birth else None,
            "gender": result.gender,
            "primary_insurance": result.primary_insurance,
            "primary_policy_number": result.primary_policy_number,
            "primary_subscriber_name": result.primary_subscriber_name,
            "primary_subscriber_type": result.primary_subscriber_type,
            "provider_name": result.provider_name,
            "npi_number": result.npi_number,
            "tax_id": result.tax_id,
            "procedure_code": result.procedure_code,  # already resolved by coalesce
            "diagnosis_code": result.diagnosis_code,
            "from_date": str(result.from_date_of_service) if result.from_date_of_service else None,
            "to_date": str(result.to_date_of_service) if result.to_date_of_service else None
        }
    else:
        return {"error": f"No matching Aetna data found for auth_id: {auth_id}"}

def get_data_for_eligibility_rpa(auth_id):
    from OncoAuth.models import db, PatientDetails, ProviderDetails, Orders

    result = db.session.query(
        Orders.auth_id,
        Orders.procedure_code,
        Orders.icd_code,
        Orders.from_date_of_service,

        PatientDetails.patient_id,                      
        PatientDetails.primary_policy_number.label("member_id"),
        PatientDetails.first_name,
        PatientDetails.last_name,
        PatientDetails.gender,
        PatientDetails.date_of_birth,
        PatientDetails.primary_insurance,
        ProviderDetails.provider_name,
        ProviderDetails.npi_number
    ).join(
        PatientDetails, Orders.patient_id == PatientDetails.patient_id
    ).join(
        ProviderDetails, PatientDetails.provider_id == ProviderDetails.provider_id
    ).filter(
        Orders.auth_id == auth_id,
        PatientDetails.primary_insurance.ilike('%aetna%')
    ).first()

    if result:
        return {
            "auth_id":        result.auth_id,
            "procedure_code": result.procedure_code,
            "diagnosis_code": result.icd_code,
            "from_date":      str(result.from_date_of_service),
            "patient_id":     result.patient_id,         
            "member_id":      result.member_id,
            "first_name":     result.first_name,
            "last_name":      result.last_name,
            "gender":         result.gender,
            "patient_dob":    str(result.date_of_birth),
            "payer":          result.primary_insurance,
            "provider_name":  result.provider_name,
            "provider_npi_id": result.npi_number
        }
    return None

    


from flask import request, jsonify
import subprocess, json
from OncoAuth.models import db, Prescrubbing, PatientDetails, ProviderDetails, CPTMaster

@app.route('/run_aetna_insurance_rpa', methods=['POST'])
@csrf.exempt
# @login_required
def run_aetna_insurance_rpa():
    auth_id = request.json.get('auth_id')
    if not auth_id:
        return jsonify({'error': 'Missing auth_id'}), 400

    # Fetch data from DB
    data = get_data_for_rpa(auth_id)

    if not data or "error" in data:
        return jsonify({'error': data.get("error", "Data not found for auth_id")}), 404

    try:
        subprocess.run(
            ["python3", "rpa/aetnapriorauth.py", json.dumps(data, default=str)],
            check=True
        )
        
        # Update prescrubbing table with "Pending" status AFTER successful RPA execution
        prescrub_record = Prescrubbing.query.filter_by(auth_id=auth_id).first()
        if prescrub_record:
            prescrub_record.auth_status = "In Progress"
            db.session.commit()
            print(json.dumps({
                "auth_status": "In Progress",
                "auth_id": auth_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": "Auth status updated after successful RPA execution"
            }, indent=2))
        
        return jsonify({'message': 'Aetna RPA executed successfully'})
    except subprocess.CalledProcessError as e:
        return jsonify({'error': f'RPA failed: {str(e)}'}), 500

from flask import request, jsonify
import subprocess
import json

@app.route('/run_anthem_rpa', methods=['POST'])
@csrf.exempt
def run_anthem_rpa():
    try:
        subprocess.run(
            ["python3", "rpa/anthemonco.py"],
            check=True
        )
        return jsonify({'message': 'Anthem RPA executed successfully'})
    except subprocess.CalledProcessError as e:
        return jsonify({'error': f'Anthem RPA failed: {str(e)}'}), 500


@app.route('/api/submit_order', methods=['POST'])
@login_required
def submit_order():
    import subprocess, json
    data = request.get_json()
    auth_id = data.get('auth_id')

    print(f"📤 Submitting to AETNA for Auth ID: {auth_id}")

    # Fetch relevant data for AETNA automation
    try:
        payload = get_data_for_eligibility_rpa(auth_id)
        if not payload:
            return jsonify({"status": "FAIL", "note": "Missing data for AETNA RPA"})

        # Only proceed if payer is AETNA
        if "aetna" not in (payload.get("payer") or "").lower():
            print("⚠️ Not an AETNA insurance, skipping aetnaonco.py")
            return jsonify({"status": "FAIL", "note": "Not an AETNA insurance"})

        # Run aetnaonco.py RPA subprocess
        result = subprocess.run(
            ["python", "aetnaonco.py", json.dumps(payload)],
            capture_output=True,
            text=True,
            timeout=120
        )

        print("✅ AETNA RPA stdout:\n", result.stdout)
        if result.stderr:
            print("⚠️ AETNA RPA stderr:\n", result.stderr)

        if result.returncode == 0:
            db.session.query(Prescrubbing).filter_by(auth_id=auth_id).update({
                "auth_status": "Submitted"
            })
            db.session.commit()
            return jsonify({"status": "PASS", "note": "Submitted via AETNA RPA"})
        else:
            return jsonify({"status": "FAIL", "note": "RPA returned error code"})

    except Exception as e:
        print("❌ Exception in AETNA Submit RPA:", e)
        return jsonify({"status": "FAIL", "message": str(e)})

@app.before_request
def enforce_session_timeout():
    if current_user.is_authenticated:
        expired_time = session.get('expired')

        if expired_time:
            # Convert to datetime object if it's a string (optional safety)
            if isinstance(expired_time, str):
                expired_time = datetime.fromisoformat(expired_time.replace('Z', ''))

            # Make both times naive or both aware — here, we assume expired_time is naive
            #now = now
            if datetime.now(timezone.utc) > expired_time:
                log_audit("SESSION_TIMEOUT")
                logout_user()
                session.clear()
                flash("Session timed out due to inactivity.", "warning")
                return redirect(url_for('login'))
            else:
                # ✅ Update session expiry on activity
                session['expired'] = datetime.now(timezone.utc) + timedelta(minutes=15)
#@app.teardown_request
#def check_session_timeout(exception=None):
#    if 'user_id' in session and session.permanent:
#        if session.get('expired') and datetime.now(timezone.utc) > session['expired']:
#            log_audit("SESSION_TIMEOUT")

@app.route("/login", methods=["GET", "POST"])
@audit_page_view("Login Page")
def login():
    form = LoginForm()
    if request.method == "POST" and form.validate_on_submit():
        user = UserDetails.query.filter_by(username=form.username.data).first()
        two_fa_enabled = request.form.get("two_fa_enabled")
        #flash(two_fa_enabled)
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            if two_fa_enabled == '0':
                #session['pending_user'] = user.user_id
                db.session.commit()
                login_user(user)
                session.permanent = True
                app.permanent_session_lifetime = timedelta(minutes=15)
                session['expired'] = datetime.now(timezone.utc) + timedelta(minutes=15)
                log_audit("LOGIN")
                session.pop('pending_user', None)
                session['pending_user'] = user.user_id
                return redirect(url_for('dashboard'))
        
            if not user.two_fa_enabled:
                secret = pyotp.random_base32()
                user.otp_secret = secret
                #user.two_fa_enabled = True
                db.session.commit()
                session['pending_user'] = user.user_id
                return redirect(url_for('verify_qr'))

            session['pending_user'] = user.user_id
            return redirect(url_for('verify_otp'))
        else:
            flash("Invalid username or password", "danger")
    return render_template("login.html", form=form)


@app.route('/verify-qr')
@audit_page_view("Verify QRCode")
def verify_qr():
    user_id = session.get('pending_user')
    if not user_id:
        return redirect(url_for('login'))

    user = UserDetails.query.get(user_id)
    otp_uri = pyotp.totp.TOTP(user.otp_secret).provisioning_uri(name=user.email, issuer_name="oncoauth")
    qr = qrcode.make(otp_uri)
    buffered = BytesIO()
    qr.save(buffered)
    qr_img = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return render_template("verify_qr.html", qr_img=qr_img)


@app.route('/verify-otp', methods=['GET', 'POST'])
@audit_page_view("Verify OTP")
def verify_otp():
    user_id = session.get('pending_user')
    if not user_id:
        return redirect(url_for('login'))

    user = UserDetails.query.get(user_id)
    
    if request.method == 'POST':
        token = request.form['otp']
        totp = pyotp.TOTP(user.otp_secret)
        if totp.verify(token):
            # ✅ Now set MFA as enabled only after successful OTP
            user.two_fa_enabled = True
            db.session.commit()
            
            login_user(user)
            session.permanent = True
            app.permanent_session_lifetime = timedelta(minutes=15)
            session['expired'] = datetime.now(timezone.utc) + timedelta(minutes=15)
            log_audit("LOGIN")

            session.pop('pending_user', None)
            
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid OTP", "danger")

    return render_template("verify_otp.html")


@app.route('/logout')
@login_required
@audit_page_view("LogOut")
def logout():
    
    logout_user()
    return redirect(url_for('login'))

def get_user_menus(role_id):
    role_menu_ids = [rm.menu_id for rm in RoleMenus.query.filter_by(role_id=role_id, has_access=True).all()]
    all_menus = MenuMaster.query.filter(MenuMaster.id.in_(role_menu_ids)).all()
    
    menu_dict = {m.menu_name: m for m in all_menus}
    for m in all_menus:
        m.children = []

    # Attach children recursively
    for m in all_menus:
        if m.parent_menu and m.parent_menu in menu_dict:
            parent = menu_dict[m.parent_menu]
            parent.children.append(m)

    def get_root_menus():
        return [m for m in all_menus if not m.parent_menu or m.parent_menu not in menu_dict]

    return get_root_menus()

def role_required(role_name):
    def wrapper(fn):
        @login_required
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role.role_name != role_name:
                flash("Access denied.", "danger")
                return redirect(url_for('dashboard'))
            return fn(*args, **kwargs)
        decorated_view.__name__ = fn.__name__
        return decorated_view
    return wrapper

@app.route('/')
def home():
    return redirect(url_for('dashboard'))

@app.route('/chemo')
@login_required
def chemo():
    return render_template('chemo.html', menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/nonchemo', methods=['GET'])
@login_required
@audit_page_view("Non-Chemo View")
def nonchemo():
    facility_filter = request.args.get('facility')
    patient_filter = request.args.get('patient')
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')

    facilities = Facility.query.filter_by(is_active=True).all()

    # Base query: Patient + Orders
    query = db.session.query(
        PatientDetails.full_name.label('patient_name'),
        PatientDetails.date_of_birth.label('dob'),
        PatientDetails.gender,
        PatientDetails.primary_policy_number,
        ProviderDetails.provider_name,
        ProviderDetails.npi_number,
        ProviderDetails.provider_type,
        ProviderDetails.provider_contact,
        ProviderDetails.tax_id,
        InsuranceDetails.insurance_name,
        InsuranceDetails.payer_edi_number,
        InsuranceDetails.payer_address,
        InsuranceDetails.contact_number.label('insurance_tel'),
        Orders.order_id,
        Orders.from_date_of_service,
        Orders.to_date_of_service,
        Orders.cpt_code,
        Orders.icd_code,
        Orders.order_description
    ).join(Orders, Orders.patient_id == PatientDetails.patient_id)\
     .join(ProviderDetails, PatientDetails.provider_id == ProviderDetails.provider_id)\
     .join(InsuranceDetails, PatientDetails.primary_insurance_id == InsuranceDetails.insurance_id)

    if facility_filter and facility_filter != "All":
        query = query.filter(PatientDetails.facility_id == facility_filter)

    if patient_filter:
        query = query.filter(PatientDetails.full_name == patient_filter)

    if from_date:
        query = query.filter(Orders.from_date_of_service >= from_date)

    if to_date:
        query = query.filter(Orders.to_date_of_service <= to_date)

    patient_data = query.all()

    # Load patient list (filtered by facility)
    if facility_filter and facility_filter != "All":
        patients = PatientDetails.query.filter_by(facility_id=facility_filter).all()
    else:
        patients = PatientDetails.query.all()

    return render_template(
        'nonchemo.html',
        menus=get_user_menus(current_user.role_id),
        user=current_user,
        patient_data=patient_data,
        facilities=facilities,
        patients=patients,
        selected_facility=facility_filter,
        selected_patient=patient_filter,
        from_date=from_date,
        to_date=to_date
    )


@app.route('/dashboard')
@login_required
@audit_page_view("Dashboard")
def dashboard():
    from sqlalchemy import func, extract

    # ORDER STATS
    order_stats = {
        'received': Orders.query.count(),
        'auth_not_required': Orders.query.filter_by(auth_required=False).count(),
        'auth_required': Orders.query.filter_by(auth_required=True).count(),
        'auth_validated': Orders.query.filter(Orders.auth_id.isnot(None)).count(),
        'auth_submitted': Orders.query.filter(Orders.order_description.ilike('%submitted%')).count()
    }

    # CLAIM STATS
    claim_stats = {
        'approved': ClaimsDetails.query.filter_by(claim_status='Approved').count(),
        'denied': ClaimsDetails.query.filter_by(claim_status='Denied').count(),
        'in_progress': ClaimsDetails.query.filter_by(claim_status='In Progress').count(),
        'peer_peer': ClaimsDetails.query.filter_by(claim_status='Peer-Peer').count(),
        'need_mr': ClaimsDetails.query.filter_by(claim_status='Need MR').count()
    }

    today = datetime.today()
    last_7_days = [today - timedelta(days=i) for i in range(6, -1, -1)]

    # ✅ TAT Calculation using real DB data with error handling
    try:
        intake_diffs = []
        prescrub_diffs = []
        submit_diffs = []

        records = db.session.query(
            Orders.order_date,
            Prescrubbing.created_at,
            ClaimsDetails.claim_date,
            ClaimsDetails.status_date
        ).join(Prescrubbing, Orders.order_id == Prescrubbing.order_id)\
         .join(ClaimsDetails, ClaimsDetails.auth_id == Orders.auth_id)\
         .filter(
            Orders.order_date.isnot(None),
            Prescrubbing.created_at.isnot(None),
            ClaimsDetails.claim_date.isnot(None),
            ClaimsDetails.status_date.isnot(None)
        ).all()

        for order_date, prescrub_date, claim_date, status_date in records:
            intake = (prescrub_date - order_date).days
            prescrub = (datetime.combine(claim_date, datetime.min.time()) - prescrub_date).days
            submit = (datetime.combine(status_date, datetime.min.time()) - datetime.combine(claim_date, datetime.min.time())).days
            intake_diffs.append(intake)
            prescrub_diffs.append(prescrub)
            submit_diffs.append(submit)

        tat_data = [
            round(sum(intake_diffs) / len(intake_diffs), 1) if intake_diffs else 0,
            round(sum(prescrub_diffs) / len(prescrub_diffs), 1) if prescrub_diffs else 0,
            round(sum(submit_diffs) / len(submit_diffs), 1) if submit_diffs else 0
        ]
        tat_labels = ['Intake', 'Prescrub', 'Submit']
    except Exception as e:
        print("TAT Calculation Error:", e)
        tat_data = [0, 0, 0]
        tat_labels = ['Intake', 'Prescrub', 'Submit']

    # ✅ Orders/Day Chart
    volume_data, volume_labels = [], []
    for day in last_7_days:
        count = Orders.query.filter(
            extract('year', Orders.order_date) == day.year,
            extract('month', Orders.order_date) == day.month,
            extract('day', Orders.order_date) == day.day
        ).count()
        volume_labels.append(day.strftime('%a'))
        volume_data.append(count)

    # ✅ Auth Status Chart
    auth_labels = ['Not Required', 'Required', 'Submitted']
    auth_data = [
        order_stats['auth_not_required'],
        order_stats['auth_required'],
        order_stats['auth_submitted']
    ]

    # ✅ Top CPT Codes
    cpt_results = db.session.query(Orders.cpt_code, func.count(Orders.order_id))\
        .group_by(Orders.cpt_code)\
        .order_by(func.count(Orders.order_id).desc())\
        .limit(5).all()
    top_cpt_labels = [r[0] for r in cpt_results]
    top_cpt_data = [r[1] for r in cpt_results]

    # ✅ Auth Trend Chart
    auth_days, auth_submitted, auth_verified = [], [], []
    for day in last_7_days:
        auth_days.append(day.strftime('%a'))
        submitted_count = Orders.query.filter(
            Orders.order_description.ilike('%submitted%'),
            extract('year', Orders.order_date) == day.year,
            extract('month', Orders.order_date) == day.month,
            extract('day', Orders.order_date) == day.day
        ).count()
        verified_count = Orders.query.filter(
            Orders.auth_id.isnot(None),
            extract('year', Orders.order_date) == day.year,
            extract('month', Orders.order_date) == day.month,
            extract('day', Orders.order_date) == day.day
        ).count()
        auth_submitted.append(submitted_count)
        auth_verified.append(verified_count)

    # ✅ Claims by Facility
    facility_results = db.session.query(PatientDetails.facility_name, func.count(ClaimsDetails.claim_id))\
        .join(Orders, Orders.patient_id == PatientDetails.patient_id)\
        .join(ClaimsDetails, ClaimsDetails.auth_id == Orders.auth_id)\
        .group_by(PatientDetails.facility_name).all()
    facility_labels = [r[0] for r in facility_results]
    facility_data = [r[1] for r in facility_results]

    # ✅ Combine All Chart Data
    chart_data = {
        'tat': {'labels': tat_labels, 'data': tat_data},
        'volume': {'labels': volume_labels, 'data': volume_data},
        'auth': {'labels': auth_labels, 'data': auth_data},
        'top_cpt': {'labels': top_cpt_labels, 'data': top_cpt_data},
        'auth_trend': {'labels': auth_days, 'submitted': auth_submitted, 'verified': auth_verified},
        'by_facility': {'labels': facility_labels, 'data': facility_data}
    }

    return render_template('dashboard.html',
        order_stats=order_stats,
        claim_stats=claim_stats,
        chart_data=chart_data,
        menus=get_user_menus(current_user.role_id),
        user=current_user
    )


# --- CRUD for Master Tables with Menu Context ---
@app.route('/roles', methods=['GET', 'POST'])
@role_required("admin")
@audit_page_view("roles")
def manage_roles():
    if request.method == 'POST':
        db.session.add(RolesMaster(
            role_name=request.form['role_name'],
            description=request.form['description']
        ))
        db.session.commit()
        flash("Role added successfully.", "success")
        return redirect(url_for('manage_roles'))
    return render_template('masters/roles.html', roles=RolesMaster.query.all(), menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/delete_role/<int:id>')
@role_required("admin")
@audit_page_view("Delete Role")
def delete_role(id):
    db.session.delete(RolesMaster.query.get_or_404(id))
    db.session.commit()
    flash("Role deleted.", "info")
    return redirect(url_for('manage_roles'))

@app.route('/menus', methods=['GET', 'POST'])
@role_required("admin")
@audit_page_view("Menus")
def manage_menus():
    if request.method == 'POST':
        db.session.add(MenuMaster(
            menu_name=request.form['menu_name'],
            display_name=request.form['display_name'],
            parent_menu=request.form['parent_menu'],
            url_path=request.form['url_path'],
            icon_class=request.form['icon_class']
        ))
        db.session.commit()
        flash("Menu added.", "success")
        return redirect(url_for('manage_menus'))
    return render_template('masters/menus.html', menus_list=MenuMaster.query.all(), menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/delete_menu/<int:id>')
@role_required("admin")
@audit_page_view("Delete Menu")
def delete_menu(id):
    db.session.delete(MenuMaster.query.get_or_404(id))
    db.session.commit()
    flash("Menu deleted.", "info")
    return redirect(url_for('manage_menus'))

@app.route('/rolemenu', methods=['GET', 'POST'])
@role_required("admin")
@audit_page_view("Role Menu Management")
def manage_rolemenu():
    if request.method == 'POST':
        db.session.add(RoleMenus(
            role_id=request.form['role_id'],
            menu_id=request.form['menu_id'],
            has_access='has_access' in request.form
        ))
        db.session.commit()
        flash("RoleMenu entry added.", "success")
        return redirect(url_for('manage_rolemenu'))
    return render_template('masters/rolemenu.html', rolemenus=RoleMenus.query.all(), roles=RolesMaster.query.all(), menus_all=MenuMaster.query.all(), menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/delete_rolemenu/<int:id>')
@role_required("admin")
@audit_page_view("Delete Role Menu")
def delete_rolemenu(id):
    db.session.delete(RoleMenus.query.get_or_404(id))
    db.session.commit()
    flash("RoleMenu entry deleted.", "info")
    return redirect(url_for('manage_rolemenu'))

@app.route('/users', methods=['GET', 'POST'])
@role_required("admin")
@audit_page_view("User Management")
def manage_users():
    if request.method == 'POST':
        db.session.add(UserDetails(
            username=request.form['username'],
            email=request.form['email'],
            password_hash=request.form['password'],  # To be hashed in production
            role_id=request.form['role_id'],
            is_active=True
        ))
        db.session.commit()
        flash("User added.", "success")
        return redirect(url_for('manage_users'))
    return render_template('masters/users.html', users=UserDetails.query.all(), roles=RolesMaster.query.all(), menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/delete_user/<int:id>')
@role_required("admin")
@audit_page_view("Delete User")
def delete_user(id):
    db.session.delete(UserDetails.query.get_or_404(id))
    db.session.commit()
    flash("User deleted.", "info")
    return redirect(url_for('manage_users'))

@app.route('/providers', methods=['GET', 'POST'])
@role_required("admin")
@audit_page_view("Provider Management")
def manage_providers():
    if request.method == 'POST':
        db.session.add(ProviderDetails(
            first_name=request.form['first_name'],
            last_name=request.form['last_name'],
            npi_number=request.form['npi_number'],
            provider_contact=request.form['provider_contact'],
            provider_name=request.form['provider_name'],
            provider_type=request.form['provider_type'],
            tax_id=request.form['tax_id']
        ))
        db.session.commit()
        flash("Provider added.", "success")
        return redirect(url_for('manage_providers'))
    return render_template('masters/providers.html', providers=ProviderDetails.query.all(), menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/delete_provider/<int:id>')
@role_required("admin")
@audit_page_view("Delete Provider")
def delete_provider(id):
    db.session.delete(ProviderDetails.query.get_or_404(id))
    db.session.commit()
    flash("Provider deleted.", "info")
    return redirect(url_for('manage_providers'))

@app.route('/insurances', methods=['GET', 'POST'])
@role_required("admin")
@audit_page_view("Insurance Management")
def manage_insurances():
    if request.method == 'POST':
        db.session.add(InsuranceDetails(
            insurance_name=request.form['insurance_name'],
            payer_edi_number=request.form['payer_edi_number'],
            payer_address=request.form['payer_address'],
            contact_number=request.form['contact_number']
        ))
        db.session.commit()
        flash("Insurance added.", "success")
        return redirect(url_for('manage_insurances'))
    return render_template('masters/insurances.html', insurances=InsuranceDetails.query.all(), menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/delete_insurance/<int:id>')
@role_required("admin")
@audit_page_view("Delete Insurance")
def delete_insurance(id):
    db.session.delete(InsuranceDetails.query.get_or_404(id))
    db.session.commit()
    flash("Insurance deleted.", "info")
    return redirect(url_for('manage_insurances'))

@app.route('/cpt', methods=['GET', 'POST'])
@role_required("admin")
@audit_page_view("CPT Management")
def manage_cpt():
    if request.method == 'POST':
        db.session.add(CPTMaster(
            cpt_code=request.form['cpt_code'],
            description=request.form['description']
        ))
        db.session.commit()
        flash("CPT code added.", "success")
        return redirect(url_for('manage_cpt'))
    return render_template('masters/cpt.html', cpts=CPTMaster.query.all(), menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/delete_cpt/<int:id>')
@role_required("admin")
@audit_page_view("Delete CPT")
def delete_cpt(id):
    db.session.delete(CPTMaster.query.get_or_404(id))
    db.session.commit()
    flash("CPT code deleted.", "info")
    return redirect(url_for('manage_cpt'))

@app.route('/icd', methods=['GET', 'POST'])
@role_required("admin")
@audit_page_view("ICD Management")
def manage_icd():
    if request.method == 'POST':
        db.session.add(ICDMaster(
            icd_code=request.form['icd_code'],
            description=request.form['description']
        ))
        db.session.commit()
        flash("ICD code added.", "success")
        return redirect(url_for('manage_icd'))
    return render_template('masters/icd.html', icds=ICDMaster.query.all(), menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/delete_icd/<int:id>')
@role_required("admin")
@audit_page_view("Delete ICD")
def delete_icd(id):
    db.session.delete(ICDMaster.query.get_or_404(id))
    db.session.commit()
    flash("ICD code deleted.", "info")
    return redirect(url_for('manage_icd'))

@app.route('/drugs', methods=['GET', 'POST'])
@role_required("admin")
@audit_page_view("Drug Management")
def manage_drugs():
    if request.method == 'POST':
        db.session.add(Drug(
            drug_name=request.form['drug_name'],
            drug_description=request.form['drug_description'],
            is_active='is_active' in request.form
        ))
        db.session.commit()
        flash("Drug added.", "success")
        return redirect(url_for('manage_drugs'))
    return render_template('masters/drugs.html', drugs=Drug.query.all(), menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/delete_drug/<int:id>')
@role_required("admin")
@audit_page_view("Delete Drug")
def delete_drug(id):
    db.session.delete(Drug.query.get_or_404(id))
    db.session.commit()
    flash("Drug deleted.", "info")
    return redirect(url_for('manage_drugs'))

@app.route('/facilities', methods=['GET', 'POST'])
@role_required("admin")
@audit_page_view("Facility Management")
def manage_facilities():
    if request.method == 'POST':
        db.session.add(Facility(
            facility_name=request.form['facility_name'],
            facility_description=request.form['facility_description'],
            is_active='is_active' in request.form
        ))
        db.session.commit()
        flash("Facility added.", "success")
        return redirect(url_for('manage_facilities'))
    return render_template('masters/facilities.html', facilities=Facility.query.all(), menus=get_user_menus(current_user.role_id), user=current_user)

@app.route('/delete_facility/<int:id>')
@role_required("admin")
@audit_page_view("Delete Facility")
def delete_facility(id):
    db.session.delete(Facility.query.get_or_404(id))
    db.session.commit()
    flash("Facility deleted.", "info")
    return redirect(url_for('manage_facilities'))


@app.route('/get_facilities')
@login_required
def get_facilities():
    facilities = Facility.query.with_entities(Facility.facility_id, Facility.facility_name).filter_by(is_active=True).all()
    return jsonify([{'id': f[0], 'name': f[1]} for f in facilities])

@app.route('/get_patients_by_facility')
@login_required
def get_patients_by_facility():
    facility_id = request.args.get("facility_id")
    patients = PatientDetails.query.filter_by(facility_id=facility_id).all()
    result = [{'full_name': p.full_name, 'id': p.patient_id} for p in patients]  # <-- FIXED
    return jsonify(result)

@app.route('/get_patients_by_facility_reports')
@login_required
def get_patients_by_facility_reports():
    facility_name = request.args.get("facility")

    if facility_name == "All":
        patients = db.session.query(PatientDetails.full_name)\
            .filter(PatientDetails.full_name.isnot(None))\
            .filter(PatientDetails.full_name != '')\
            .distinct().all()
    else:
        patients = db.session.query(PatientDetails.full_name)\
            .join(Facility, PatientDetails.facility_id == Facility.facility_id)\
            .filter(Facility.facility_name == facility_name)\
            .filter(PatientDetails.full_name.isnot(None))\
            .filter(PatientDetails.full_name != '')\
            .distinct().all()

    return jsonify([p[0] for p in patients])  # flatten the tuple

@app.route('/get_all_patients')
@login_required
def get_all_patients():
    patients = PatientDetails.query.with_entities(PatientDetails.full_name)\
        .filter(PatientDetails.full_name.isnot(None))\
        .filter(PatientDetails.full_name != '')\
        .all()
    result = [{'full_name': p[0]} for p in patients]
    return jsonify(result)

@app.route('/get_patient_details')
@login_required
def get_patient_details():
    patient_id = request.args.get("patient_id")
    patient = PatientDetails.query.get(patient_id)
    if not patient:
        return jsonify({"error": "Patient not found"}), 404

    provider = ProviderDetails.query.get(patient.provider_id) if patient.provider_id else None
    insurance = InsuranceDetails.query.get(patient.primary_insurance_id) if patient.primary_insurance_id else None

    # ✅ Get orders
    orders = Orders.query.filter_by(patient_id=patient_id).all()
    order_list = [{
        "from_dos": str(o.from_date_of_service),
        "to_dos": str(o.to_date_of_service),
        "cpt": o.cpt_code,
        "icd": o.icd_code,
        "description": o.order_description,
        "drug_type": o.drugname,
        "quantity": o.drugquantity,
        "no_of_chemo": o.numberofchemo,
        "request_type": o.order_comments
    } for o in orders]

    # ✅ Get latest file (if any)
    file = FileOrders.query.filter_by(patient_id=patient_id).order_by(FileOrders.upload_time.desc()).first()
    file_info = {
        "name": file.file_name,
        "id": file.id
    } if file else None

    return jsonify({
        "patient": {
            "dob": str(patient.date_of_birth),
            "gender": patient.gender,
            "subscriber_id": patient.primary_policy_number
        },
        "provider": {
            "name": provider.provider_name if provider else "",
            "type": provider.provider_type if provider else "",
            "npi": provider.npi_number if provider else "",
            "contact": provider.provider_contact if provider else "",
            "tax_id": provider.tax_id if provider else ""
        },
        "insurance": {
            "name": insurance.insurance_name if insurance else "",
            "edi": insurance.payer_edi_number if insurance else "",
            "address": insurance.payer_address if insurance else "",
            "tel": insurance.contact_number if insurance else ""
        },
        "orders": order_list,
        "file": file_info
    })


@app.route('/get_cpt_codes')
@login_required
def get_cpt_codes():
    codes = [c.cpt_code for c in CPTMaster.query.all()]
    return jsonify(codes)

@app.route('/get_icd_codes')
@login_required
def get_icd_codes():
    codes = [i.icd_code for i in ICDMaster.query.all()]
    return jsonify(codes)

@app.route('/get_providers')
@login_required
def get_providers():
    providers = ProviderDetails.query.with_entities(ProviderDetails.provider_name).all()
    return jsonify([{'name': p[0]} for p in providers])

@app.route('/get_insurance')
@login_required
def get_insurance():
    insurers = InsuranceDetails.query.with_entities(InsuranceDetails.insurance_name).all()
    return jsonify([{'name': i[0]} for i in insurers])

@app.route('/get_provider_by_name')
def get_provider_by_name():
    name = request.args.get('name')
    provider = ProviderDetails.query.filter_by(provider_name=name).first()
    if provider:
        return jsonify({
            'type': provider.provider_type or '',
            'npi': provider.npi_number or '',
            'contact': provider.provider_contact or '',
            'tax_id': provider.tax_id or ''
        })
    return jsonify({})

@app.route('/get_insurance_by_name')
def get_insurance_by_name():
    name = request.args.get('name')
    insurance = InsuranceDetails.query.filter_by(insurance_name=name).first()
    if insurance:
        return jsonify({
            'edi': insurance.payer_edi_number or '',
            'address': insurance.payer_address or '',
            'tel': insurance.contact_number or ''
        })
    return jsonify({})


@app.route('/submit_nonchemo_full', methods=['POST'])
@login_required
@audit_page_view("Submit Non-Chemo Full Form")
def submit_nonchemo_full():
    from datetime import datetime, timezone
    from uuid import uuid4
    data = request.form
    file = request.files.get("file_upload")

    # Patient & related info
    patient_name = data.get("patient_name")
    dob = data.get("dob")
    gender = data.get("gender")
    subscriber_id = data.get("subscriber_id")
    facility_id = data.get("facility_id")

    provider_name = data.get("provider_name")
    provider_npi = data.get("provider_npi")
    provider_type = data.get("provider_type")
    provider_contact = data.get("provider_contact")
    tax_id = data.get("tax_id")

    insurance_name = data.get("insurance_id")
    payer_edi = data.get("payer_edi")
    payer_address = data.get("payer_address")
    insurance_tel = data.get("insurance_tel")

    # Get or create provider
    provider = ProviderDetails.query.filter_by(provider_name=provider_name).first()
    if not provider:
        provider = ProviderDetails(
            provider_name=provider_name,
            provider_type=provider_type,
            npi_number=provider_npi,
            provider_contact=provider_contact,
            tax_id=tax_id
        )
        db.session.add(provider)
        db.session.flush()

    # Get or create insurance
    insurance = InsuranceDetails.query.filter_by(insurance_name=insurance_name).first()
    if not insurance:
        insurance = InsuranceDetails(
            insurance_name=insurance_name,
            payer_edi_number=payer_edi,
            payer_address=payer_address,
            contact_number=insurance_tel
        )
        db.session.add(insurance)
        db.session.flush()

    # Get facility name
    facility = Facility.query.filter_by(facility_id=facility_id).first()
    facility_name = facility.facility_name if facility else None

    # Get or create patient
    patient = PatientDetails.query.filter_by(full_name=patient_name, facility_id=facility_id).first()
    if patient:
        # Update existing
        patient.date_of_birth = dob
        patient.gender = gender
        patient.primary_policy_number = subscriber_id
        patient.provider_id = provider.provider_id
        patient.primary_insurance_id = insurance.insurance_id

        # ✅ Delete old orders and prescrubbing for this patient
        old_orders = Orders.query.filter_by(patient_id=patient.patient_id).all()
        auth_ids_to_delete = [o.auth_id for o in old_orders if o.auth_id]
        Orders.query.filter_by(patient_id=patient.patient_id).delete()
        Prescrubbing.query.filter(Prescrubbing.auth_id.in_(auth_ids_to_delete)).delete(synchronize_session=False)

    else:
        patient = PatientDetails(
            full_name=patient_name,
            date_of_birth=dob,
            gender=gender,
            primary_policy_number=subscriber_id,
            facility_id=facility_id,
            facility_name=facility_name,
            provider_id=provider.provider_id,
            primary_insurance_id=insurance.insurance_id,
            custom_patient_id=f"P{int(datetime.now(timezone.utc).timestamp())}",
            mrn=f"MRN{int(datetime.now(timezone.utc).timestamp())}"
        )
        db.session.add(patient)
        db.session.flush()

    # Parse order rows
    from_dos_list = data.getlist("from_dos[]")
    to_dos_list = data.getlist("to_dos[]")
    cpt_code_list = data.getlist("cpt_code[]")
    icd_code_list = data.getlist("icd_code[]")
    description_list = data.getlist("service_description[]")

    for from_dos, to_dos, cpt_code, icd_code, desc in zip(from_dos_list, to_dos_list, cpt_code_list, icd_code_list, description_list):
        if not from_dos or not to_dos or not cpt_code or not icd_code:
            continue

        auth_id = f"AUT-{uuid4().hex[:8].upper()}"
        order = Orders(
            patient_id=patient.patient_id,
            provider_id=provider.provider_id,
            order_date=datetime.now(timezone.utc),
            from_date_of_service=from_dos,
            to_date_of_service=to_dos,
            cpt_code=cpt_code,
            icd_code=icd_code,
            order_description=desc,
            auth_required=True,
            auth_id=auth_id
        )
        db.session.add(order)

        prescrub = Prescrubbing(
            patient_id=patient.patient_id,
            created_at=datetime.now(timezone.utc),
            auth_status="Saved, not submitted",
            auth_id=auth_id
        )
        db.session.add(prescrub)

    # Upload file if any
    # Upload file if any
    if file and file.filename:
        filename = secure_filename(file.filename)
        os.makedirs("uploads", exist_ok=True)
        file_path = os.path.join("uploads", filename)
        file_data = file.read()

        # Save to disk
        with open(file_path, "wb") as f:
            f.write(file_data)

        # Save to DB
        file_record = FileOrders(
            data=file_data,
            file_name=filename,
            file_path=file_path,
            file_type=file.content_type,
            upload_time=datetime.now(timezone.utc),
            patient_id=patient.patient_id
        )
        db.session.add(file_record)
        flash("File uploaded and stored in database.", "info")


    db.session.commit()
    flash("Non-Chemo authorization submitted successfully.", "success")
    return redirect(url_for("nonchemo"))

@app.route('/download_file_order/<int:file_id>')
@login_required
def download_file_order(file_id):
    file = FileOrders.query.get_or_404(file_id)
    return send_file(
        BytesIO(file.data),
        as_attachment=True,
        download_name=file.file_name,
        mimetype=file.file_type or 'application/octet-stream'
    )

@app.route('/submit_chemo_full', methods=['POST'])
@login_required
@audit_page_view("Submit Chemo Full Form")
def submit_chemo_full():
    from datetime import datetime, timezone
    from uuid import uuid4
    data = request.form
    file = request.files.get("file_upload")

    # Patient details
    patient_name = data.get("patient_name")
    dob = data.get("dob")
    gender = data.get("gender")
    subscriber_id = data.get("subscriber_id")
    facility_id = data.get("facility_id")

    # Provider details
    provider_name = data.get("provider_name")
    provider_npi = data.get("provider_npi")
    provider_type = data.get("provider_type")
    provider_contact = data.get("provider_contact")
    tax_id = data.get("tax_id")

    # Insurance details
    insurance_name = data.get("insurance_id")
    payer_edi = data.get("payer_edi")
    payer_address = data.get("payer_address")
    insurance_tel = data.get("insurance_tel")

    # Provider
    provider = ProviderDetails.query.filter_by(provider_name=provider_name).first()
    if not provider:
        provider = ProviderDetails(
            provider_name=provider_name,
            provider_type=provider_type,
            npi_number=provider_npi,
            provider_contact=provider_contact,
            tax_id=tax_id
        )
        db.session.add(provider)
        db.session.flush()

    # Insurance
    insurance = InsuranceDetails.query.filter_by(insurance_name=insurance_name).first()
    if not insurance:
        insurance = InsuranceDetails(
            insurance_name=insurance_name,
            payer_edi_number=payer_edi,
            payer_address=payer_address,
            contact_number=insurance_tel
        )
        db.session.add(insurance)
        db.session.flush()

    # Facility
    facility = Facility.query.filter_by(facility_id=facility_id).first()
    facility_name = facility.facility_name if facility else None

    # Patient
    patient = PatientDetails.query.filter_by(full_name=patient_name, facility_id=facility_id).first()
    if patient:
        patient.date_of_birth = dob
        patient.gender = gender
        patient.primary_policy_number = subscriber_id
        patient.provider_id = provider.provider_id
        patient.primary_insurance_id = insurance.insurance_id

        # ✅ Delete existing orders and prescrubbing
        old_orders = Orders.query.filter_by(patient_id=patient.patient_id).all()
        auth_ids_to_delete = [o.auth_id for o in old_orders if o.auth_id]
        Orders.query.filter_by(patient_id=patient.patient_id).delete()
        Prescrubbing.query.filter(Prescrubbing.auth_id.in_(auth_ids_to_delete)).delete(synchronize_session=False)

    else:
        patient = PatientDetails(
            full_name=patient_name,
            date_of_birth=dob,
            gender=gender,
            primary_policy_number=subscriber_id,
            facility_id=facility_id,
            facility_name=facility_name,
            provider_id=provider.provider_id,
            primary_insurance_id=insurance.insurance_id,
            custom_patient_id=f"P{int(datetime.now(timezone.utc).timestamp())}",
            mrn=f"MRN{int(datetime.now(timezone.utc).timestamp())}"
        )
        db.session.add(patient)
        db.session.flush()

    # Orders
    from_dos_list = request.form.getlist('from_dos[]')
    to_dos_list = request.form.getlist('to_dos[]')
    cpt_code_list = request.form.getlist('cpt_code[]')
    icd_code_list = request.form.getlist('icd_code[]')
    description_list = request.form.getlist('service_description[]')
    drug_type_list = request.form.getlist('drug_type[]')
    quantity_list = request.form.getlist('quantity[]')
    no_of_chemo_list = request.form.getlist('no_of_chemo[]')
    request_type_list = request.form.getlist('request_type[]')

    for from_dos, to_dos, cpt_code, icd_code, desc, drug_type, qty, no_chemo, req_type in zip(
        from_dos_list, to_dos_list, cpt_code_list, icd_code_list, description_list,
        drug_type_list, quantity_list, no_of_chemo_list, request_type_list
    ):
        if not from_dos or not to_dos or not cpt_code or not icd_code:
            continue

        auth_id = f"AUT-{uuid4().hex[:8].upper()}"
        order = Orders(
            patient_id=patient.patient_id,
            provider_id=provider.provider_id,
            order_date=datetime.now(timezone.utc),
            from_date_of_service=from_dos,
            to_date_of_service=to_dos,
            cpt_code=cpt_code,
            icd_code=icd_code,
            order_description=desc,
            drugname=drug_type,
            drugquantity=qty,
            numberofchemo=no_chemo,
            order_comments=req_type,
            auth_required=True,
            auth_id=auth_id
        )
        db.session.add(order)

        prescrub = Prescrubbing(
            patient_id=patient.patient_id,
            created_at=datetime.now(timezone.utc),
            auth_status="Saved, not submitted",
            auth_id=auth_id
        )
        db.session.add(prescrub)

    # File Upload
    # File Upload - Save to file system and database
    if file and file.filename:
        filename = secure_filename(file.filename)
        os.makedirs("uploads", exist_ok=True)
        file_path = os.path.join("uploads", filename)
        file_data = file.read()

        # Save to disk
        with open(file_path, "wb") as f:
            f.write(file_data)

        # Save to DB
        file_record = FileOrders(
            data=file_data,
            file_name=filename,
            file_path=file_path,
            file_type=file.content_type,
            upload_time=datetime.now(timezone.utc),
            patient_id=patient.patient_id
        )
        db.session.add(file_record)
        flash("File uploaded and stored in database.", "info")


    db.session.commit()
    flash("Chemo order submitted successfully.", "success")
    return redirect(url_for("chemo"))

@app.route('/get_patient_details_oh')
def get_patient_details_oh():
    auth_id = request.args.get("auth_id")
    if not auth_id:
        return jsonify({"orders": []})

    order = Orders.query.filter_by(auth_id=auth_id).first()
    if not order:
        return jsonify({"orders": []})

    patient_id = order.patient_id
    orders = Orders.query.filter_by(patient_id=patient_id).order_by(Orders.order_date.desc()).all()

    order_list = []
    for o in orders:
        order_list.append({
            "from_dos": o.from_date_of_service.strftime("%Y-%m-%d") if o.from_date_of_service else '',
            "to_dos": o.to_date_of_service.strftime("%Y-%m-%d") if o.to_date_of_service else '',
            "cpt": o.cpt_code,
            "icd": o.icd_code,
            "description": o.order_description,
            "drug_type": o.drugname,
            "quantity": o.drugquantity,
            "no_of_chemo": o.numberofchemo,
            "request_type": o.precertification_type
        })

    return jsonify({"orders": order_list})

# Prescrubbing functionality
# Prescrubbing functionality with persistent DB updates
@app.route('/prescrubbing', methods=['GET'])
@login_required
@audit_page_view("Prescrubbing")
def prescrubbing():
    facility = request.args.get('facility')
    patient = request.args.get('patient')
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')
    provider = request.args.get('provider')
    insurance = request.args.get('insurance')
    order_type = request.args.get('order_type')

    # Updated query to include auth_id and patient_id for dynamic use
    query = db.session.query(
        Prescrubbing.auth_id,  # ✅ Include actual auth_id
        PatientDetails.patient_id,  # ✅ Include patient_id
        PatientDetails.full_name.label('patient_name'),
        PatientDetails.date_of_birth.label('dob'),
        PatientDetails.mrn.label('mrn'),
        func.max(Orders.order_id).label('order_id'),
        func.max(Orders.from_date_of_service).label('from_dos'),
        func.max(Orders.to_date_of_service).label('to_dos'),
        func.group_concat(Orders.cpt_code.distinct()).label('cpt_code'),
        func.group_concat(Orders.icd_code.distinct()).label('icd_code'),
        func.max(Orders.drugname).label('drugname'),
        func.max(Orders.order_description).label('order_description'),
        InsuranceDetails.insurance_name.label('insurance_id'),
        ProviderDetails.provider_name.label('provider_name'),
        ProviderDetails.npi_number.label('npi'),
        func.max(Prescrubbing.insurance_validation_status).label('prescrubbing_insurance_validation_status'),
        func.max(Prescrubbing.npi_validation_status).label('prescrubbing_npi_validation_status'),
        func.max(Prescrubbing.cpt_validation_status).label('prescrubbing_cpt_validation_status'),
        func.max(Prescrubbing.auth_status).label('prescrubbing_auth_status')
    ).join(Orders, Orders.patient_id == PatientDetails.patient_id) \
     .join(Prescrubbing, Prescrubbing.auth_id == Orders.auth_id) \
     .join(ProviderDetails, ProviderDetails.provider_id == PatientDetails.provider_id) \
     .join(InsuranceDetails, InsuranceDetails.insurance_id == PatientDetails.primary_insurance_id)\
     .group_by(
        Prescrubbing.auth_id,  # ✅ Group by auth_id
        PatientDetails.patient_id,  # ✅ Group by patient_id
        PatientDetails.full_name,
        PatientDetails.date_of_birth,
        PatientDetails.mrn,
        InsuranceDetails.insurance_name,
        ProviderDetails.provider_name,
        ProviderDetails.npi_number
    )

    if facility and facility != "All":
        query = query.join(Facility, PatientDetails.facility_id == Facility.facility_id)
        query = query.filter(PatientDetails.facility_id == facility)
    if patient:
        query = query.filter(PatientDetails.full_name == patient)

    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")

    if from_date:
        query = query.filter(func.date(Orders.order_date) >= from_date)

    if to_date:
        query = query.filter(func.date(Orders.order_date) <= to_date)

    if provider:
        query = query.filter(ProviderDetails.provider_name.ilike(f"%{provider}%"))
    if insurance:
        query = query.filter(InsuranceDetails.insurance_name.ilike(f"%{insurance}%"))
    if order_type == 'Chemo':
        query = query.filter(Orders.numberofchemo.isnot(None))
    elif order_type == 'Non-Chemo':
        query = query.filter(Orders.numberofchemo.is_(None))

    filters_applied = any([facility, patient, from_date, to_date, provider, insurance, order_type])
    prescrub_data = query.all() if filters_applied else []
    facilities = Facility.query.filter_by(is_active=True).all()
    
    return render_template('prescrubbing.html',
        prescrub_data=prescrub_data,
        facilities=facilities,
        menus=get_user_menus(current_user.role_id),
        user=current_user)

@csrf.exempt  # ✅ Correct usage
@app.route('/api/validate_step', methods=['POST'])
def validate_step():
    data = request.get_json()
    auth_id = data.get("auth_id")
    step = data.get("step")
    value = data.get("value")

    if not all([auth_id, step]):
        return jsonify({"status": "FAIL", "error": "Missing parameters"}), 400

    prescrub = Prescrubbing.query.filter_by(auth_id=auth_id).first()
    if not prescrub:
        return jsonify({"status": "FAIL", "error": "Invalid auth_id"}), 404

    if step == "cpt":

        if not value or value.strip() == "":
            flash("CPT code is missing or empty.", "warning")
            return jsonify({
                "status": "FAIL",
                "auth_status": "CPT code missing"
            })
        print("INSIDE CPT VALIDATION")
        print(f"cpt_code: {value}")

        # Check if insurance is Humana
        order = Orders.query.filter_by(auth_id=auth_id).first()
        insurance_name = None

        if order:
            patient = PatientDetails.query.filter_by(patient_id=order.patient_id).first()
            insurance = InsuranceDetails.query.filter_by(insurance_id=patient.primary_insurance_id).first() if patient else None
            insurance_name = insurance.insurance_name.lower() if insurance else ""

        if insurance_name and "humana" in insurance_name:
            print("INSIDE HUMANA:")
            try:
                spec = importlib.util.spec_from_file_location("humana_rpa", os.path.join("cpt", "humana_rpa.py"))
                humana_rpa = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(humana_rpa)
                output = humana_rpa.run_humana_cpt_check(value)
                
                result = output
                status = "PASS" if "required" in result.lower() else "FAIL"
                prescrub.cpt_validation_status = status
                prescrub.auth_status = result
                db.session.commit()

                return jsonify({"status": status, "message": result, "auth_status": prescrub.auth_status})
            except Exception as e:
                print(f"CPT validation error: {e}")
                return jsonify({"status": "FAIL", "error": str(e)})
        ##else:
        #    return jsonify({"status": "PASS", "message": "Skipped CPT validation for non-Humana"})
        
        if insurance_name and "aetna" in insurance_name:
            print("INSIDE Aetna:",value)
            result = {}
            try:
                # Connect to DB using SQLAlchemy (no need for raw MySQL connector)
                from sqlalchemy import text
                query = text("SELECT precert_required FROM aetna_precert WHERE cpt_code = :code")
                db_result = db.session.execute(query, {"code": value}).fetchone()

                if db_result:
                    precert = db_result[0].strip().lower()
                    result["status"] = "PASS"
                    result["auth_status"] = "Precertification IS Required" if precert == "yes" else "Precertification NOT Required"
                else:
                    result["status"] = "PASS"
                    result["auth_status"] = "Precertification NOT Required"

                # ✅ Update prescrubbing DB
                prescrub.cpt_validation_status = result["status"]
                prescrub.auth_status = result["auth_status"]
                print("INSIDE Aetna: status ",result["status"])
                print("INSIDE Aetna: auth_status ",result["auth_status"])
                db.session.commit()
                print("INSIDE Aetna: Updated ")
                return jsonify(result)

            except Exception as e:
                print(f"❌ Error checking Aetna precert table: {e}")
                return jsonify({"status": "FAIL", "error": str(e)}), 500

    elif step == "npi":
        print("INSIDE NPI VALIDATION")
        # Validate provider NPI based on name
        last_name, first_name = map(str.strip, value.split(","))
        provider = ProviderDetails.query.filter_by(first_name=first_name, last_name=last_name).first()
        if provider and provider.npi_number:
            prescrub.npi_validation_status = "PASS"
            db.session.commit()
            return jsonify({"status": "PASS"})
        else:
            prescrub.npi_validation_status = "FAIL"
            db.session.commit()
            return jsonify({"status": "FAIL"})

    elif step == "insurance":
        return

    else:
        logging.warning(f"Unknown validation step: {step}")
        return jsonify({"status": "FAIL", "message": "Unknown validation step"})

@app.route('/api/get_auth_status')
@login_required
def get_auth_status():
    auth_id = request.args.get("auth_id")
    if not auth_id:
        return jsonify({'error': 'Missing auth_id'}), 400

    status = db.session.query(Prescrubbing.auth_status).filter_by(auth_id=auth_id).scalar()
    return jsonify({'auth_status': status or 'Not Submitted'})

def get_auth_status(auth_id):
    order = Orders.query.filter_by(auth_id=auth_id).first()
    return order.prescrubbing_auth_status if order else None

@app.route('/api/validate_all', methods=['POST'])
@login_required
@audit_page_view("API Validate All")
def api_validate_all():
    data = request.get_json()
    order_ids = data['order_ids']
    for order_id in order_ids:
        record = Prescrubbing.query.filter_by(order_id=order_id).first()
        if not record:
            order = Orders.query.get(order_id)
            if not order:
                continue
            record = Prescrubbing(
                order_id=order_id,
                patient_id=order.patient_id,
                auth_id=order.auth_id,
                created_at=datetime.now(timezone.utc)
            )
            db.session.add(record)

        record.insurance_validation_status = 'PASS'
        record.npi_validation_status = 'PASS'
        record.cpt_validation_status = 'PASS'

    db.session.commit()
    return jsonify({'status': 'PASS'})

@app.route('/api/submit_all', methods=['POST'])
@login_required
@audit_page_view("API Submit All")
def api_submit_all():
    data = request.get_json()
    order_ids = data['order_ids']
    for order_id in order_ids:
        record = Prescrubbing.query.filter_by(order_id=order_id).first()
        if record:
            record.auth_status = 'Submitted'
    db.session.commit()
    return jsonify({'status': 'PASS'})


@app.route("/denials", methods=["GET"])
@login_required
@audit_page_view("Denials Management")
def denials():
    from sqlalchemy.orm import aliased # type: ignore
    selected_facility = request.args.get("facility_name")
    selected_patient = request.args.get("patient_name")
    selected_claim_status = request.args.get("claim_status")
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    provider = request.args.get("provider")
    insurance = request.args.get("insurance")
    cpt = request.args.get("cpt")
    icd = request.args.get("icd")

    OrderAlias = aliased(Orders)

    query = db.session.query(
        ClaimsDetails.claim_id,
        ClaimsDetails.auth_id,
        ClaimsDetails.claim_date,
        ClaimsDetails.claim_status,
        PatientDetails.full_name.label("patient_name"),
        OrderAlias.cpt_code.label("cpt"),
        OrderAlias.icd_code.label("icd"),
        ProviderDetails.provider_name.label("provider"),
        InsuranceDetails.insurance_name.label("insurance"),
        Facility.facility_name.label("facility")
        #ClaimsDetails.notes
    ).join(OrderAlias, func.upper(OrderAlias.auth_id) == func.upper(ClaimsDetails.auth_id))\
     .join(PatientDetails, OrderAlias.patient_id == PatientDetails.patient_id)\
     .join(ProviderDetails, OrderAlias.provider_id == ProviderDetails.provider_id)\
     .join(InsuranceDetails, PatientDetails.primary_insurance_id == InsuranceDetails.insurance_id)\
     .join(Facility, PatientDetails.facility_id == Facility.facility_id)

    # Apply filters
    if selected_facility and selected_facility != "All":
        query = query.filter(Facility.facility_name == selected_facility)
    if selected_patient:
        query = query.filter(PatientDetails.full_name.ilike(f"%{selected_patient}%"))
    if selected_claim_status:
        query = query.filter(ClaimsDetails.claim_status == selected_claim_status)
    if from_date:
        query = query.filter(ClaimsDetails.claim_date >= from_date)
    if to_date:
        query = query.filter(ClaimsDetails.claim_date <= to_date)
    if provider:
        query = query.filter(ProviderDetails.provider_name.ilike(f"%{provider}%"))
    if insurance:
        query = query.filter(InsuranceDetails.insurance_name.ilike(f"%{insurance}%"))
    if cpt:
        query = query.filter(OrderAlias.cpt_code.ilike(f"%{cpt}%"))
    if icd:
        query = query.filter(OrderAlias.icd_code.ilike(f"%{icd}%"))

    claims = query.all()

    # Dropdowns
    facilities = [f[0] for f in db.session.query(Facility.facility_name).distinct().all()]
    if selected_facility and selected_facility != "All":
        patients = [p[0] for p in db.session.query(PatientDetails.full_name)
                    .join(Facility)
                    .filter(Facility.facility_name == selected_facility)
                    .distinct().all()]
    else:
        patients = [p[0] for p in db.session.query(PatientDetails.full_name).distinct().all()]
    providers = [p[0] for p in db.session.query(ProviderDetails.provider_name).distinct().all()]
    insurances = [i[0] for i in db.session.query(InsuranceDetails.insurance_name).distinct().all()]
    cpts = [c[0] for c in db.session.query(CPTMaster.cpt_code).distinct().all()]
    icds = [i[0] for i in db.session.query(ICDMaster.icd_code).distinct().all()]


    return render_template(
        "denials.html",
        menus=get_user_menus(current_user.role_id),
        user=current_user,
        claims=claims,
        facilities=facilities,
        patients=patients,
        providers=providers,
        insurances=insurances,
        cpts=cpts,
        icds=icds,
        selected_facility=selected_facility,
        selected_patient=selected_patient,
        selected_claim_status=selected_claim_status,
        from_date=from_date,
        to_date=to_date,
        selected_provider=provider,
        selected_insurance=insurance,
        selected_cpt=cpt,
        selected_icd=icd
    )


@app.route("/claim/view/<int:claim_id>")
@login_required
@audit_page_view("View Claim")
def view_claim(claim_id):
    claim = ClaimsDetails.query.get_or_404(claim_id)
    order = Orders.query.filter(func.upper(Orders.auth_id) == func.upper(claim.auth_id)).first()
    patient = PatientDetails.query.get(order.patient_id) if order else None
    provider = ProviderDetails.query.get(order.provider_id) if order else None
    insurance = InsuranceDetails.query.get(patient.primary_insurance_id) if patient else None
    return render_template("view_claim.html", claim=claim, order=order, patient=patient,
                           provider=provider, insurance=insurance, menus=get_user_menus(current_user.role_id), user=current_user)

@csrf.exempt  # ✅ Correct usage
@app.route("/claim/edit/<int:claim_id>", methods=["GET", "POST"])
@login_required
@audit_page_view("Edit Claim")
def edit_claim(claim_id):
    claim = ClaimsDetails.query.get_or_404(claim_id)
    if claim.claim_status == "Approved":
        flash("Approved claims cannot be edited.", "warning")
        return redirect(url_for("denials"))

    order = Orders.query.filter(func.upper(Orders.auth_id) == func.upper(claim.auth_id)).first()
    patient = PatientDetails.query.get(order.patient_id) if order else None
    provider = ProviderDetails.query.get(order.provider_id) if order else None
    insurance = InsuranceDetails.query.get(patient.primary_insurance_id) if patient else None

    if request.method == "POST":
        claim.claim_description = request.form.get("claim_description")
        file = request.files.get("file_upload")
        if file:
            path = os.path.join("uploads", secure_filename(file.filename))
            os.makedirs("uploads", exist_ok=True)
            file.save(path)
            file_record = FileClaims(
                auth_id=claim.auth_id,
                claim_id=claim.claim_id,
                file_name=file.filename,
                file_path=path,
                file_type=file.content_type,
                upload_time=datetime.now(timezone.utc)
            )
            db.session.add(file_record)

        claim.claim_status = "Resubmitted"
        claim.status_date = datetime.now().date()
        db.session.commit()
        flash("Claim resubmitted to insurance.", "success")
        return redirect(url_for("denials"))

    return render_template("edit_claim.html",
                           claim=claim,
                           order=order,
                           patient=patient,
                           provider=provider,
                           insurance=insurance,
                           menus=get_user_menus(current_user.role_id),
                           user=current_user)

@csrf.exempt  # ✅ Correct usage
@app.route("/api/get_provider_id", methods=["POST"])
#@login_required

def api_get_provider_id():
    data = request.get_json()
    print("📥 Received /api/get_provider_id request")
    print("Payload:", data)
    first_name = data.get("first_name", "").strip()
    last_name = data.get("last_name", "").strip()
    auth_id = data.get("auth_id", "")
    print(f"first_name: {first_name}, last_name: {last_name}, auth_id: {auth_id}")
    if not first_name or not last_name:
        print("❌ Missing provider_name components")
        return jsonify({'status': 'FAIL', 'message': 'Missing provider_name'}), 400

    try:
        provider_id = get_provider_id_by_name(first_name, last_name, auth_id)
        if provider_id:
            # optionally update your DB here
            return jsonify({'status': 'PASS', 'provider_id': provider_id})
        else:
            return jsonify({'status': 'FAIL', 'message': 'Provider not found'})
    except Exception as e:
        print("❌ Exception in /api/get_provider_id:", str(e))
        return jsonify({'status': 'FAIL', 'message': str(e)}), 500

"""
@app.route('/api/get_provider_id', methods=['POST'])
@csrf_exempt
#@login_required
def api_get_provider_id():
    data = request.get_json()
    full_name = data.get('provider_name', '')
    auth_id = data.get('auth_id', '')

    if not full_name or ',' not in full_name or not auth_id:
        print(f"❌ Invalid request: {data}")
        return jsonify({'status': 'FAIL', 'message': 'Invalid provider name or auth_id'}), 400

    last_name, first_name = [part.strip() for part in full_name.split(',', 1)]

    try:
        from OncoAuth.rpa.npi_lookup import get_provider_id_by_name
        provider_id = get_provider_id_by_name(first_name, last_name, auth_id)

        if provider_id:
            from OncoAuth.models import db, Prescrubbing
            db.session.query(Prescrubbing).filter_by(auth_id=auth_id).update({'npi_validation_status': 'PASS'})
            db.session.commit()
            return jsonify({'status': 'PASS', 'provider_id': provider_id})
        else:
            db.session.query(Prescrubbing).filter_by(auth_id=auth_id).update({'npi_validation_status': None})
            db.session.commit()
            return jsonify({'status': 'FAIL', 'provider_id': None})
    except Exception as e:
        print(f"❌ Exception: {e}")
        return jsonify({'status': 'FAIL', 'message': str(e)}), 500   """

@app.route("/api/patients_by_facility")
@login_required

def api_patients_by_facility():
    facility = request.args.get("facility", "")
    if facility and facility != "All":
        patient_query = db.session.query(PatientDetails.full_name)\
            .join(Facility)\
            .filter(Facility.facility_name == facility)
    else:
        patient_query = db.session.query(PatientDetails.full_name)
    
    patients = sorted(set([p[0] for p in patient_query.all()]))
    return jsonify(patients)


@app.route("/save_note", methods=["POST"])
@login_required
def save_note():
    claim_id = request.form.get("claim_id")
    new_comment = request.form.get("new_comment")

    claim = ClaimsDetails.query.get_or_404(claim_id)
    if not new_comment:
        return jsonify({"status": "fail", "message": "No comment provided"})

    note = ClaimNotes(
        claim_id=claim.claim_id,
        auth_id=claim.auth_id,
        updated_datetime=datetime.now(timezone.utc),
        updated_by=current_user.username,
        comments=new_comment
    )
    db.session.add(note)
    db.session.commit()
    return jsonify({"status": "success"})

@app.route("/get_claim_notes/<int:claim_id>")
@login_required
def get_claim_notes(claim_id):
    notes = ClaimNotes.query.filter_by(claim_id=claim_id).order_by(ClaimNotes.updated_datetime.desc()).all()
    return jsonify([
        {
            "updated_by": note.updated_by,
            "updated_datetime": note.updated_datetime.strftime("%Y-%m-%d %H:%M"),
            "comments": note.comments
        } for note in notes
    ])

@app.route("/reports", methods=["GET"])
@login_required
@audit_page_view("Reports")
def reports():
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    patient = request.args.get("patient")
    provider = request.args.get("provider")
    insurance = request.args.get("insurance")
    status = request.args.get("status")
    cpt = request.args.get("cpt")
    icd = request.args.get("icd")
    selected_facility = request.args.get("facility_name")

    query = db.session.query(
        Orders.order_id.label("order_id"),
        PatientDetails.full_name.label("patient"),
        ProviderDetails.provider_name.label("provider"),
        InsuranceDetails.insurance_name.label("insurance"),
        Orders.cpt_code.label("cpt"),
        Orders.icd_code.label("icd"),
        ClaimsDetails.claim_status.label("status"),
        Orders.from_date_of_service.label("dos_from"),
        Orders.to_date_of_service.label("dos_to"),
        ClaimsDetails.claim_date.label("submitted"),
        ClaimsDetails.status_date.label("approved"),
        Facility.facility_name.label("facility")
    ).join(PatientDetails, Orders.patient_id == PatientDetails.patient_id) \
     .join(ProviderDetails, Orders.provider_id == ProviderDetails.provider_id) \
     .join(InsuranceDetails, PatientDetails.primary_insurance_id == InsuranceDetails.insurance_id) \
     .join(ClaimsDetails, ClaimsDetails.auth_id == Orders.auth_id) \
     .join(Facility, PatientDetails.facility_id == Facility.facility_id)

    if from_date:
        query = query.filter(Orders.from_date_of_service >= from_date)
    if to_date:
        query = query.filter(Orders.to_date_of_service <= to_date)
    if patient:
        query = query.filter(PatientDetails.full_name.ilike(f"%{patient}%"))
    if provider:
        query = query.filter(ProviderDetails.provider_name.ilike(f"%{provider}%"))
    if insurance:
        query = query.filter(InsuranceDetails.insurance_name.ilike(f"%{insurance}%"))
    if status:
        query = query.filter(ClaimsDetails.claim_status == status)
    if cpt:
        query = query.filter(Orders.cpt_code.ilike(f"%{cpt}%"))
    if icd:
        query = query.filter(Orders.icd_code.ilike(f"%{icd}%"))
    if selected_facility and selected_facility != "All":
        query = query.filter(Facility.facility_name == selected_facility)

    reports = query.all()

    # Dropdown support: fetch distinct values
    facilities = db.session.query(Facility.facility_name).distinct().all()
    patients = db.session.query(PatientDetails.full_name).distinct().all()
    providers = db.session.query(ProviderDetails.provider_name).distinct().all()
    insurances = db.session.query(InsuranceDetails.insurance_name).distinct().all()
    cpt_codes = [c.cpt_code for c in CPTMaster.query.all()]
    icd_codes = [i.icd_code for i in ICDMaster.query.all()]

    return render_template("reports.html",
        reports=reports,
        facilities=[f[0] for f in facilities],
        patients=[p[0] for p in patients],
        providers=[p[0] for p in providers],
        insurances=[i[0] for i in insurances],
        selected_facility=selected_facility,
        cpt_codes=cpt_codes,
        icd_codes=icd_codes,
        menus=get_user_menus(current_user.role_id),
        user=current_user
    )


@app.route("/reports/export/<string:format>", methods=["GET"])
@login_required
@audit_page_view("Export Reports")
def export_reports(format):
    from sqlalchemy.orm import aliased
    from sqlalchemy.sql import text
    # Reuse the same filters from /reports
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    patient = request.args.get("patient")
    provider = request.args.get("provider")
    insurance = request.args.get("insurance")
    status = request.args.get("status")
    cpt = request.args.get("cpt")
    icd = request.args.get("icd")
    facility_name = request.args.get("facility_name")

    query = db.session.query(
        Orders.order_id,
        PatientDetails.full_name.label("Patient"),
        ProviderDetails.provider_name.label("Provider"),
        InsuranceDetails.insurance_name.label("Insurance"),
        Orders.cpt_code.label("CPT"),
        Orders.icd_code.label("ICD"),
        ClaimsDetails.claim_date.label("Submitted"),
        ClaimsDetails.status_date.label("Approved"),
        ClaimsDetails.claim_status.label("Status")
    ).join(PatientDetails, Orders.patient_id == PatientDetails.patient_id) \
     .join(ProviderDetails, Orders.provider_id == ProviderDetails.provider_id) \
     .join(InsuranceDetails, PatientDetails.primary_insurance_id == InsuranceDetails.insurance_id) \
     .join(ClaimsDetails, ClaimsDetails.auth_id == Orders.auth_id) \
     .join(Facility, PatientDetails.facility_id == Facility.facility_id)

    
    if patient:
        query = query.filter(PatientDetails.full_name.ilike(f"%{patient}%"))
    if provider:
        query = query.filter(ProviderDetails.provider_name.ilike(f"%{provider}%"))
    if insurance:
        query = query.filter(InsuranceDetails.insurance_name.ilike(f"%{insurance}%"))
    if status:
        query = query.filter(ClaimsDetails.claim_status == status)
    if cpt:
        query = query.filter(Orders.cpt_code.ilike(f"%{cpt}%"))
    if icd:
        query = query.filter(Orders.icd_code.ilike(f"%{icd}%"))
    if facility_name and facility_name != "All":
        query = query.filter(Facility.facility_name == facility_name)

    with db.engine.connect() as connection:
        df = pd.read_sql(query.statement, connection)

    filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if format == "csv":
        output = make_response(df.to_csv(index=False))
        output.headers["Content-Disposition"] = f"attachment; filename={filename}.csv"
        output.headers["Content-Type"] = "text/csv"
        return output
    elif format == "excel":
        from io import BytesIO
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Report')
        output.seek(0)
        return send_file(output, download_name=f"{filename}.xlsx", as_attachment=True)
    else:
        return "Invalid export format", 400

    

def log_audit(action, menu_name=None, page_name=None):
    try:
        log = AuditLog(
            user_id=current_user.user_id,
            username=current_user.username,
            role=current_user.role.role_name,
            action=action,
            menu_name=menu_name or request.endpoint,
            page_name=page_name or request.path,
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        print(f"[AuditLog Error] {e}")

def log_error(menu_name, page_name, error_msg):
    try:
        log = ErrorLog(
            user_id=current_user.user_id if current_user.is_authenticated else None,
            username=current_user.username if current_user.is_authenticated else "Anonymous",
            role=current_user.role.role_name if current_user.is_authenticated else "Unknown",
            menu_name=menu_name,
            page_name=page_name,
            error_message=str(error_msg),
            error_timestamp=datetime.now(timezone.utc)
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        print(f"[ErrorLog Error] {e}")

        
# ------------------ Edit route for RolesMaster ------------------
@app.route('/roles/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@audit_page_view("Role Edit")
@role_required("admin")
def edit_roles(id):
    obj = RolesMaster.query.get_or_404(id)

    if request.method == 'POST':
        try:
            for col in ['field1', 'field2']:
                setattr(obj, col, request.form.get(col))
            db.session.commit()
            flash("RolesMaster updated successfully.", "success")
            return redirect(url_for('roles'))
        except Exception as e:
            log_error("roles", f"/roles/edit/{{id}}", e)
            flash("Error updating RolesMaster.", "danger")

    fields = [{"label": col.name.replace("_", " ").title(), "name": col.name}
              for col in RolesMaster.__table__.columns if col.name != 'id']
    form_data = {{col.name: getattr(obj, col.name) for col in RolesMaster.__table__.columns}}

    # Load all records for view
    data = [
        {{col.name: getattr(x, col.name) for col in RolesMaster.__table__.columns}}
        for x in RolesMaster.query.all()
    ]
    columns = [col.name for col in RolesMaster.__table__.columns]

    return render_template('masters/roles.html', title="RolesMaster Master",
                           fields=fields, columns=columns, data=data,
                           form_data=form_data,
                           save_route='edit_roles', edit_route='edit_roles', delete_route='delete_roles',
                           menus=get_user_menus(current_user.role_id), user=current_user)

# ------------------ Edit route for MenuMaster ------------------
@app.route('/menus/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@audit_page_view("Menu Edit")
@role_required("admin")
def edit_menus(id):
    obj = MenuMaster.query.get_or_404(id)

    if request.method == 'POST':
        try:
           for col in ['field1', 'field2']:
            setattr(obj, col, request.form.get(col))
            db.session.commit()
            flash("MenuMaster updated successfully.", "success")
            return redirect(url_for('menus'))
        except Exception as e:
            log_error("menus", f"/menus/edit/{{id}}", e)
            flash("Error updating MenuMaster.", "danger")

    fields = [{"label": col.name.replace("_", " ").title(), "name": col.name}
              for col in MenuMaster.__table__.columns if col.name != 'id']
    form_data = {{col.name: getattr(obj, col.name) for col in MenuMaster.__table__.columns}}

    # Load all records for view
    data = [
        {{col.name: getattr(x, col.name) for col in MenuMaster.__table__.columns}}
        for x in MenuMaster.query.all()
    ]
    columns = [col.name for col in MenuMaster.__table__.columns]

    return render_template('masters/menus.html', title="MenuMaster Master",
                           fields=fields, columns=columns, data=data,
                           form_data=form_data,
                           save_route='edit_menus', edit_route='edit_menus', delete_route='delete_menus',
                           menus=get_user_menus(current_user.role_id), user=current_user)

# ------------------ Edit route for RoleMenus ------------------
@app.route('/rolemenu/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@audit_page_view("RoleMenus Edit")
@role_required("admin")
def edit_rolemenu(id):
    obj = RoleMenus.query.get_or_404(id)

    if request.method == 'POST':
        try:
            for col in ['field1', 'field2']:
                setattr(obj, col, request.form.get(col))
            db.session.commit()
            db.session.commit()
            flash("RoleMenus updated successfully.", "success")
            return redirect(url_for('rolemenu'))
        except Exception as e:
            log_error("rolemenu", f"/rolemenu/edit/{{id}}", e)
            flash("Error updating RoleMenus.", "danger")

    fields = [{"label": col.name.replace("_", " ").title(), "name": col.name}
              for col in RoleMenus.__table__.columns if col.name != 'id']
    form_data = {{col.name: getattr(obj, col.name) for col in RoleMenus.__table__.columns}}

    # Load all records for view
    data = [
        {{col.name: getattr(x, col.name) for col in RoleMenus.__table__.columns}}
        for x in RoleMenus.query.all()
    ]
    columns = [col.name for col in RoleMenus.__table__.columns]

    return render_template('masters/rolemenu.html', title="RoleMenus Master",
                           fields=fields, columns=columns, data=data,
                           form_data=form_data,
                           save_route='edit_rolemenu', edit_route='edit_rolemenu', delete_route='delete_rolemenu',
                           menus=get_user_menus(current_user.role_id), user=current_user)

# ------------------ Edit route for UserDetails ------------------
@app.route('/users/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@audit_page_view("User Edit")
@role_required("admin")
def edit_users(id):
    obj = UserDetails.query.get_or_404(id)

    if request.method == 'POST':
        try:
            for col in ['field1', 'field2']:
                setattr(obj, col, request.form.get(col))
            db.session.commit()
            db.session.commit()
            flash("UserDetails updated successfully.", "success")
            return redirect(url_for('users'))
        except Exception as e:
            log_error("users", f"/users/edit/{{id}}", e)
            flash("Error updating UserDetails.", "danger")

    fields = [{"label": col.name.replace("_", " ").title(), "name": col.name}
              for col in UserDetails.__table__.columns if col.name != 'id']
    form_data = {{col.name: getattr(obj, col.name) for col in UserDetails.__table__.columns}}

    # Load all records for view
    data = [
        {{col.name: getattr(x, col.name) for col in UserDetails.__table__.columns}}
        for x in UserDetails.query.all()
    ]
    columns = [col.name for col in UserDetails.__table__.columns]

    return render_template('masters/users.html', title="UserDetails Master",
                           fields=fields, columns=columns, data=data,
                           form_data=form_data,
                           save_route='edit_users', edit_route='edit_users', delete_route='delete_users',
                           menus=get_user_menus(current_user.role_id), user=current_user)

# ------------------ Edit route for ProviderDetails ------------------
@app.route('/providers/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@audit_page_view("Provider Edit")
@role_required("admin")
def edit_providers(id):
    obj = ProviderDetails.query.get_or_404(id)

    if request.method == 'POST':
        try:
            for col in ['field1', 'field2']:
                setattr(obj, col, request.form.get(col))
            db.session.commit()
            db.session.commit()
            flash("ProviderDetails updated successfully.", "success")
            return redirect(url_for('providers'))
        except Exception as e:
            log_error("providers", f"/providers/edit/{{id}}", e)
            flash("Error updating ProviderDetails.", "danger")

    fields = [{"label": col.name.replace("_", " ").title(), "name": col.name}
              for col in ProviderDetails.__table__.columns if col.name != 'id']
    form_data = {{col.name: getattr(obj, col.name) for col in ProviderDetails.__table__.columns}}

    # Load all records for view
    data = [
        {{col.name: getattr(x, col.name) for col in ProviderDetails.__table__.columns}}
        for x in ProviderDetails.query.all()
    ]
    columns = [col.name for col in ProviderDetails.__table__.columns]

    return render_template('masters/providers.html', title="ProviderDetails Master",
                           fields=fields, columns=columns, data=data,
                           form_data=form_data,
                           save_route='edit_providers', edit_route='edit_providers', delete_route='delete_providers',
                           menus=get_user_menus(current_user.role_id), user=current_user)

# ------------------ Edit route for InsuranceDetails ------------------
@app.route('/insurances/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@audit_page_view("Insurance Edit")
@role_required("admin")
def edit_insurances(id):
    obj = InsuranceDetails.query.get_or_404(id)

    if request.method == 'POST':
        try:
            for col in ['field1', 'field2']:
                setattr(obj, col, request.form.get(col))
            db.session.commit()
            db.session.commit()
            flash("InsuranceDetails updated successfully.", "success")
            return redirect(url_for('insurances'))
        except Exception as e:
            log_error("insurances", f"/insurances/edit/{{id}}", e)
            flash("Error updating InsuranceDetails.", "danger")

    fields = [{"label": col.name.replace("_", " ").title(), "name": col.name}
              for col in InsuranceDetails.__table__.columns if col.name != 'id']
    form_data = {{col.name: getattr(obj, col.name) for col in InsuranceDetails.__table__.columns}}

    # Load all records for view
    data = [
        {{col.name: getattr(x, col.name) for col in InsuranceDetails.__table__.columns}}
        for x in InsuranceDetails.query.all()
    ]
    columns = [col.name for col in InsuranceDetails.__table__.columns]

    return render_template('masters/insurances.html', title="InsuranceDetails Master",
                           fields=fields, columns=columns, data=data,
                           form_data=form_data,
                           save_route='edit_insurances', edit_route='edit_insurances', delete_route='delete_insurances',
                           menus=get_user_menus(current_user.role_id), user=current_user)

# ------------------ Edit route for CPTMaster ------------------
@app.route('/cpt/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@audit_page_view("CPT Edit")
@role_required("admin")
def edit_cpt(id):
    obj = CPTMaster.query.get_or_404(id)

    if request.method == 'POST':
        try:
            for col in ['field1', 'field2']:
                setattr(obj, col, request.form.get(col))
            db.session.commit()
            db.session.commit()
            flash("CPTMaster updated successfully.", "success")
            return redirect(url_for('cpt'))
        except Exception as e:
            log_error("cpt", f"/cpt/edit/{{id}}", e)
            flash("Error updating CPTMaster.", "danger")

    fields = [{"label": col.name.replace("_", " ").title(), "name": col.name}
              for col in CPTMaster.__table__.columns if col.name != 'id']
    form_data = {{col.name: getattr(obj, col.name) for col in CPTMaster.__table__.columns}}

    # Load all records for view
    data = [
        {{col.name: getattr(x, col.name) for col in CPTMaster.__table__.columns}}
        for x in CPTMaster.query.all()
    ]
    columns = [col.name for col in CPTMaster.__table__.columns]

    return render_template('masters/cpt.html', title="CPTMaster Master",
                           fields=fields, columns=columns, data=data,
                           form_data=form_data,
                           save_route='edit_cpt', edit_route='edit_cpt', delete_route='delete_cpt',
                           menus=get_user_menus(current_user.role_id), user=current_user)

# ------------------ Edit route for ICDMaster ------------------
@app.route('/icd/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@audit_page_view("ICD Edit")
@role_required("admin")
def edit_icd(id):
    obj = ICDMaster.query.get_or_404(id)

    if request.method == 'POST':
        try:
            for col in ['field1', 'field2']:
                setattr(obj, col, request.form.get(col))
            db.session.commit()
            db.session.commit()
            flash("ICDMaster updated successfully.", "success")
            return redirect(url_for('icd'))
        except Exception as e:
            log_error("icd", f"/icd/edit/{{id}}", e)
            flash("Error updating ICDMaster.", "danger")

    fields = [{"label": col.name.replace("_", " ").title(), "name": col.name}
              for col in ICDMaster.__table__.columns if col.name != 'id']
    form_data = {{col.name: getattr(obj, col.name) for col in ICDMaster.__table__.columns}}

    # Load all records for view
    data = [
        {{col.name: getattr(x, col.name) for col in ICDMaster.__table__.columns}}
        for x in ICDMaster.query.all()
    ]
    columns = [col.name for col in ICDMaster.__table__.columns]

    return render_template('masters/icd.html', title="ICDMaster Master",
                           fields=fields, columns=columns, data=data,
                           form_data=form_data,
                           save_route='edit_icd', edit_route='edit_icd', delete_route='delete_icd',
                           menus=get_user_menus(current_user.role_id), user=current_user)

# ------------------ Edit route for Drug ------------------
@app.route('/drugs/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@audit_page_view("Drug Edit")
@role_required("admin")
def edit_drugs(id):
    obj = Drug.query.get_or_404(id)

    if request.method == 'POST':
        try:
            for col in ['field1', 'field2']:
                setattr(obj, col, request.form.get(col))
            db.session.commit()
            db.session.commit()
            flash("Drug updated successfully.", "success")
            return redirect(url_for('drugs'))
        except Exception as e:
            log_error("drugs", f"/drugs/edit/{{id}}", e)
            flash("Error updating Drug.", "danger")

    fields = [{"label": col.name.replace("_", " ").title(), "name": col.name}
              for col in Drug.__table__.columns if col.name != 'id']
    form_data = {{col.name: getattr(obj, col.name) for col in Drug.__table__.columns}}

    # Load all records for view
    data = [
        {{col.name: getattr(x, col.name) for col in Drug.__table__.columns}}
        for x in Drug.query.all()
    ]
    columns = [col.name for col in Drug.__table__.columns]

    return render_template('masters/drugs.html', title="Drug Master",
                           fields=fields, columns=columns, data=data,
                           form_data=form_data,
                           save_route='edit_drugs', edit_route='edit_drugs', delete_route='delete_drugs',
                           menus=get_user_menus(current_user.role_id), user=current_user)

# ------------------ Edit route for Facility ------------------
@app.route('/facilities/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@audit_page_view("Facility Edit")
@role_required("admin")
def edit_facilities(id):
    obj = Facility.query.get_or_404(id)

    if request.method == 'POST':
        try:
            for col in ['field1', 'field2']:
                setattr(obj, col, request.form.get(col))
            db.session.commit()
            db.session.commit()
            flash("Facility updated successfully.", "success")
            return redirect(url_for('facilities'))
        except Exception as e:
            log_error("facilities", f"/facilities/edit/{{id}}", e)
            flash("Error updating Facility.", "danger")

    fields = [{"label": col.name.replace("_", " ").title(), "name": col.name}
              for col in Facility.__table__.columns if col.name != 'id']
    form_data = {{col.name: getattr(obj, col.name) for col in Facility.__table__.columns}}

    # Load all records for view
    data = [
        {{col.name: getattr(x, col.name) for col in Facility.__table__.columns}}
        for x in Facility.query.all()
    ]
    columns = [col.name for col in Facility.__table__.columns]

    return render_template('masters/facilities.html', title="Facility Master",
                           fields=fields, columns=columns, data=data,
                           form_data=form_data,
                           save_route='edit_facilities', edit_route='edit_facilities', delete_route='delete_facilities',
                           menus=get_user_menus(current_user.role_id), user=current_user)

@app.route("/api/chatbot", methods=["POST"])
def chatbot_api():
    data = request.get_json()
    auth_id = data.get("auth_id")
    query = data.get("query")

    patient = db.session.query(prescrubbing).filter_by(auth_id=auth_id).first()
    if not patient:
        return jsonify({"response": "Sorry, I couldn't find the patient details."})

    # Simple example logic
    if "history" in query.lower():
        return jsonify({"response": f"{patient.patient_name} has a history of {patient.icd or 'no major conditions listed'}."})
    elif "insurance" in query.lower():
        return jsonify({"response": f"Insurance: {patient.insurance or 'Unknown'}."})
    else:
        return jsonify({"response": "I'm still learning. Can you ask something else about the patient?"})


from flask import request, jsonify
from flask_wtf.csrf import CSRFProtect
from OncoAuth.models import db, Prescrubbing
import subprocess
import json

@csrf.exempt
@app.route('/check_availity_eligibility', methods=['POST'])
def check_availity_eligibility():
    print(" /check_availity_eligibility route is ACTIVE")
    auth_id = request.json.get('auth_id')
    if not auth_id:
        return jsonify({'error': 'Missing auth_id'}), 400

    input_data = get_data_for_eligibility_rpa(auth_id)
    if not input_data:
        return jsonify({'error': 'No data found for this auth_id'}), 404

    try:
        completed = subprocess.run(
            ["python3", "rpa/eligibilityrpafinal.py", json.dumps(input_data)],
            capture_output=True,
            text=True,
            check=True
        )

        stdout_lines = completed.stdout.strip().splitlines()
        final_result_line = next((line for line in stdout_lines if line.startswith("FINAL_RESULT:")), None)

        if not final_result_line:
            return jsonify({'error': 'FINAL_RESULT not found in script output'}), 500

        json_str = final_result_line.replace("FINAL_RESULT:", "").strip()
        result = json.loads(json_str)

        # Extract flag from RPA result
        flag = result.get("eligibility_result", {}).get("flag")

        # Get real patient_id from DB using auth_id
        patient_id = db.session.query(Prescrubbing.patient_id).filter_by(auth_id=auth_id).scalar()

        # Update insurance_validation_status
        if flag is not None and patient_id:
            ps_entry = db.session.query(Prescrubbing).filter_by(auth_id=auth_id, patient_id=patient_id).first()
            if ps_entry:
                if flag == 1:
                    ps_entry.insurance_validation_status = 'PASS'
                elif flag in [-1, 0]:
                    ps_entry.insurance_validation_status = None
                db.session.commit()

        return jsonify({
            'message': 'Eligibility RPA executed successfully',
            'result': result
        }), 200

    except subprocess.CalledProcessError as e:
        return jsonify({'error': f'RPA execution failed: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True)
