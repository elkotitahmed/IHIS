from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from datetime import timedelta
from app import db, limiter
from app.models import User, Role, Patient, LoginAttempt
from app.forms import LoginForm, RegistrationForm
from app.routes.decorators import log_activity
from app.utils import utcnow

auth_bp = Blueprint('auth', __name__)


def _safe_next(value):
    """Return True only for a local, single-slash-relative redirect target.

    Guards the login ``?next=`` parameter against open redirects: a bare
    ``/path`` is allowed, but protocol-relative (``//host``) and absolute
    (``https://host``) URLs are rejected.""" 
    if not value or not isinstance(value, str):
        return False
    if not value.startswith('/'):
        return False
    # //host and ///host are protocol/network-relative — treat as unsafe.
    if value.startswith('//'):
        return False
    # Absolute URI with a scheme (e.g. https:...) is not local.
    if '://' in value or '\\' in value:
        return False
    return True

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
@limiter.limit("10 per minute")
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
        if user and user.locked_until and user.locked_until > utcnow():
            remaining = int((user.locked_until - utcnow()).total_seconds() // 60)
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
            user.last_login = utcnow()
            db.session.add(LoginAttempt(email=email, user_id=user.id, successful=True,
                                        ip_address=request.remote_addr))
            log_activity('LOGIN', 'user', user.id)
            db.session.commit()
            flash(f'Welcome back, {user.full_name}!', 'success')
            next_page = request.args.get('next')
            if _safe_next(next_page):
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
                    user.locked_until = utcnow() + timedelta(minutes=lock_minutes)
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
@limiter.limit("5 per hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    form = RegistrationForm()
    if form.validate_on_submit():
        # Public self-registration is PATIENT-only. Staff roles (Doctor, Nurse,
        # Admin, etc.) must be provisioned by an administrator, never self-served,
        # otherwise anyone could grant themselves an Admin account.
        user_type = 'patient'
        user = User(
            username=form.username.data,
            email=form.email.data.lower(),
            full_name=form.full_name.data,
            user_type=user_type,
        )
        user.set_password(form.password.data)

        role_name = ROLE_BY_USER_TYPE.get(user_type, 'Patient')
        role = Role.query.filter_by(name=role_name).first()
        if not role:
            role = Role(name=role_name, description=f'Role for {role_name}')
            db.session.add(role)
        if role not in user.roles:
            user.roles.append(role)

        db.session.add(user)
        db.session.commit()

        # Public self-registration is PATIENT-only (see the guard above); a
        # patient profile is always created at registration. Staff/clinical
        # profiles (Doctor, Nurse, ...) are provisioned by administrators only.
        # The chosen department (e.g. Dermatology) routes patient-uploaded
        # attachments to the right specialist portal.
        from app.models import Department
        dept_id = form.department_id.data
        if dept_id and not Department.query.get(int(dept_id)):
            dept_id = None
        db.session.add(Patient(
            user_id=user.id,
            phone=form.phone.data or None,
            gender=form.gender.data or None,
            department_id=int(dept_id) if dept_id else None,
        ))
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
