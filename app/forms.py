from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, EmailField, SubmitField, SelectField,
    BooleanField, HiddenField, TextAreaField, IntegerField, FloatField,
    DateField, DateTimeField,
)
from wtforms.validators import (
    DataRequired, Email, EqualTo, Length, Optional, NumberRange, ValidationError,
)
from app.models import User, Specialty


class LoginForm(FlaskForm):
    email = EmailField('Email Address', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember me')
    submit = SubmitField('Login')


class RegistrationForm(FlaskForm):
    full_name = StringField('Full Name', validators=[DataRequired(), Length(min=3, max=150)])
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=64)])
    email = EmailField('Email Address', validators=[DataRequired(), Email()])
    phone = StringField('Phone Number', validators=[Optional(), Length(max=30)])
    gender = SelectField('Gender', choices=[
        ('', 'Select gender'), ('Male', 'Male'), ('Female', 'Female'),
        ('Other', 'Other')], validators=[Optional()])
    user_type = SelectField('User Type', choices=[
        ('patient', 'Patient'),
        ('doctor', 'Doctor'),
        ('nurse', 'Nurse'),
        ('admin', 'Administrator'),
        ('lab_technician', 'Lab Technician'),
        ('radiologist', 'Radiologist'),
        ('pharmacist', 'Pharmacist'),
        ('receptionist', 'Receptionist'),
        ('dentist', 'Dentist'),
        ('physiotherapist', 'Physical Therapist'),
    ], validators=[DataRequired()])
    specialty_id = SelectField('Specialty (doctors only)', choices=[], validators=[Optional()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(), EqualTo('password')])
    submit = SubmitField('Create Account')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.specialty_id.choices = [(s.id, s.name) for s in Specialty.query.all()]

    def validate_username(self, username):
        if User.query.filter_by(username=username.data).first():
            raise ValidationError('Username already exists. Please choose a different one.')

    def validate_email(self, email):
        if User.query.filter_by(email=email.data.lower()).first():
            raise ValidationError('Email already registered. Please login.')
