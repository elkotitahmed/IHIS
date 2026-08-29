from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta
from app import db
from app.models import User, Role, Patient, Doctor, Specialty, LoginAttempt
from app.forms import LoginForm, RegistrationForm
from app.routes.decorators import log_activity

auth_bp = Blueprint('auth', __name__)

ROLE_BY_USER_TYPE = {
    'patient': 'Patient',
    'doctor': 'Doctor',
    'nurse': 'Nurse',
    'admin': 'Admin',
    'lab_technician': 'LabTechnician',
    'radiologist': 'Radiologist',
    'pharmacist': 'Pharmacist',
    'receptionist': 'Receptionist',
    'dentist': 'Dentist',
    'physiotherapist': 'Physiotherapist',
}


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower()
        user = User.query.filter_by(email=email).first()

        max_attempts = current_app.config.get('MAX_LOGIN_ATTEMPTS', 5)
        lock_minutes = current_app.config.get('LOCKOUT_MINUTES', 15)

        # Account lockout check
        if user and user.locked_until and user.locked_until > datetime.utcnow():
            remaining = int((user.locked_until - datetime.utcnow()).total_seconds() // 60)
            db.session.add(LoginAttempt(email=email, user_id=user.id, successful=False,
                                        ip_address=request.remote_addr))
            db.session.commit()
            flash(f'Account temporarily locked. Try again in about {remaining + 1} minute(s).', 'danger')
            return render_template('auth/login.html', form=form, title='Login')

        if user and user.check_password(form.password.data):
            if not user.is_active:
                db.session.add(LoginAttempt(email=email, user_id=user.id, successful=False,
                                            ip_address=request.remote_addr))
                db.session.commit()
                flash('This account is deactivated. Contact an administrator.', 'danger')
                return render_template('auth/login.html', form=form, title='Login')
            # Success: reset lockout counters
            user.failed_login_attempts = 0
            user.locked_until = None
            login_user(user, remember=form.remember.data)
            user.last_login = datetime.utcnow()
            db.session.add(LoginAttempt(email=email, user_id=user.id, successful=True,
                                        ip_address=request.remote_addr))
            log_activity('LOGIN', 'user', user.id)
            db.session.commit()
            flash(f'Welcome back, {user.full_name}!', 'success')
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('main.dashboard'))
        else:
            db.session.add(LoginAttempt(email=email,
                                        user_id=user.id if user else None,
                                        successful=False,
                                        ip_address=request.remote_addr))
            if user:
                user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
                if user.failed_login_attempts >= max_attempts:
                    user.locked_until = datetime.utcnow() + timedelta(minutes=lock_minutes)
                    user.failed_login_attempts = 0
                    db.session.commit()
                    flash(f'Too many failed attempts. Account locked for {lock_minutes} minutes.', 'danger')
                    return render_template('auth/login.html', form=form, title='Login')
                db.session.commit()
                remaining = max_attempts - user.failed_login_attempts
                flash(f'Invalid email or password. {remaining} attempt(s) remaining before lock.', 'danger')
            else:
                db.session.commit()
                flash('Invalid email or password.', 'danger')

    return render_template('auth/login.html', form=form, title='Login')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data.lower(),
            full_name=form.full_name.data,
            user_type=form.user_type.data,
        )
        user.set_password(form.password.data)

        role_name = ROLE_BY_USER_TYPE.get(form.user_type.data, 'Patient')
        role = Role.query.filter_by(name=role_name).first()
        if not role:
            role = Role(name=role_name, description=f'Role for {role_name}')
            db.session.add(role)
        if role not in user.roles:
            user.roles.append(role)

        db.session.add(user)
        db.session.commit()

        if form.user_type.data == 'patient':
            db.session.add(Patient(
                user_id=user.id,
                phone=form.phone.data or None,
                gender=form.gender.data or None,
            ))
            db.session.commit()
        elif form.user_type.data == 'doctor':
            specialty = Specialty.query.get(form.specialty_id.data) if form.specialty_id.data else None
            db.session.add(Doctor(user_id=user.id, specialty_id=specialty.id if specialty else None))
            db.session.commit()

        log_activity('REGISTER', 'user', user.id)
        db.session.commit()
        flash('Account created successfully! You can now log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', form=form, title='Register')


@auth_bp.route('/logout')
@login_required
def logout():
    log_activity('LOGOUT', 'user', current_user.id)
    db.session.commit()
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.home'))


@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """View profile and change the account password in one page."""
    password_changed = False
    if request.method == 'POST':
        current_pw = request.form.get('current_password', '')
        new_pw = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')

        if not current_user.check_password(current_pw):
            flash('Current password is incorrect.', 'danger')
        elif len(new_pw) < 8:
            flash('New password must be at least 8 characters.', 'danger')
        elif new_pw != confirm_pw:
            flash('New password and confirmation do not match.', 'danger')
        elif current_user.check_password(new_pw):
            flash('New password must be different from the current password.', 'danger')
        else:
            current_user.set_password(new_pw)
            log_activity('CHANGE_PASSWORD', 'user', current_user.id)
            db.session.commit()
            password_changed = True
            flash('Password updated successfully.', 'success')

    return render_template(
        'auth/profile.html',
        title='My Profile',
        password_changed=password_changed,
    )
