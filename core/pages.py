from flask import Blueprint, redirect, render_template, session

from .util import login_requerido, ip_local

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/login")
def login_page():
    if "user_id" in session:
        return redirect("/")
    return render_template("login.html", ip=ip_local())


@pages_bp.route("/")
@login_requerido
def index():
    return render_template("index.html")


@pages_bp.route("/etiquetas")
@login_requerido
def etiquetas():
    return render_template("etiquetas.html")
