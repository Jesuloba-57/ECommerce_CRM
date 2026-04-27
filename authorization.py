import shortuuid
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from __init__ import db
from db_model import User

auth = Blueprint("auth", __name__)


@auth.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("views.home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user is None or not check_password_hash(user.password, password):
            flash("Incorrect email or password.", "error")
            return render_template("login.html")

        login_user(user, remember=True)
        flash(f"Welcome back, {user.display_name}.", "success")
        return redirect(request.args.get("next") or url_for("views.home"))

    return render_template("login.html")


@auth.route("/signup", methods=["GET", "POST"])
def sign_up():
    if current_user.is_authenticated:
        return redirect(url_for("views.seller_dashboard"))

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password1 = request.form.get("password1", "")
        password2 = request.form.get("password2", "")

        if User.query.filter_by(email=email).first():
            flash("That email is already registered.", "error")
        elif len(first_name) < 2:
            flash("First name must be at least 2 characters.", "error")
        elif len(email) < 5 or "@" not in email:
            flash("Enter a valid email address.", "error")
        elif len(password1) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif password1 != password2:
            flash("Passwords do not match.", "error")
        else:
            new_user = User(
                id=shortuuid.ShortUUID().random(length=15),
                email=email,
                password=generate_password_hash(password1),
                status=True,
                first_name=first_name,
                last_name=last_name,
            )
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user, remember=True)
            flash("Account created. You can start selling right away.", "success")
            return redirect(url_for("views.seller_dashboard"))

    return render_template("signup.html")


@auth.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("views.home"))
