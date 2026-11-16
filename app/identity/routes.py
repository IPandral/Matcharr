from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from .services import change_identity, save_identity

bp = Blueprint("identity", __name__)


def _db():
    return current_app.extensions["matcharr_db"]


@bp.get("/")
def index():
    if session.get("participant_id"):
        return redirect(url_for("rooms.dashboard"))
    return render_template("index.html")


@bp.post("/name")
def set_name():
    try:
        participant_id = save_identity(
            _db(),
            session.get("participant_id"),
            request.form.get("display_name", ""),
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except ValueError as error:
        flash(str(error), "error")
        return render_template("index.html"), 400
    session["participant_id"] = participant_id
    session["display_name"] = request.form["display_name"].strip()
    session.permanent = True
    return redirect(url_for("rooms.dashboard"))


@bp.post("/name/change")
def change_name():
    participant_id = session.get("participant_id")
    if not participant_id:
        return redirect(url_for("identity.index"))
    try:
        name = change_identity(
            _db(),
            participant_id,
            request.form.get("display_name", ""),
            current_app.config["MATCHARR_TRANSACTION_RETRIES"],
        )
    except ValueError as error:
        flash(str(error), "error")
    else:
        session["display_name"] = name
        flash("Display name updated.", "success")
    return redirect(request.referrer or url_for("rooms.dashboard"))

