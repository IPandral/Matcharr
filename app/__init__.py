from __future__ import annotations

import secrets
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request, session

from .config import Config
from .database.migrations import apply_migrations
from .database.rustydb import RustyDB, RustyDBConfig
from .extensions import set_db


def create_app(config: dict | object | None = None, database=None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.from_mapping(config) if isinstance(config, dict) else app.config.from_object(config)
    app.config["MEDIA_SERVER_TYPE"] = str(
        app.config.get("MEDIA_SERVER_TYPE", "plex")
    ).strip().lower()
    if app.config["MEDIA_SERVER_TYPE"] not in {"plex", "jellyfin"}:
        raise ValueError("MEDIA_SERVER_TYPE must be either 'plex' or 'jellyfin'.")

    if not app.testing:
        secret_key = (app.config["SECRET_KEY"] or "").strip()
        if len(secret_key) < 32 or secret_key == "generate-a-long-random-production-secret":
            raise ValueError("Set SECRET_KEY to a randomly generated value of at least 32 characters.")

    if database is None:
        db_password = (app.config["RUSTYDB_PASSWORD"] or "").strip()
        if not db_password or db_password in {"replace-this", "replace-this-with-a-long-random-password"}:
            raise ValueError("Set RUSTYDB_PASSWORD to a unique password for RustyDB.")
        tls_ca = app.config["RUSTYDB_TLS_CA"]
        if not tls_ca:
            raise ValueError("Set RUSTYDB_TLS_CA to the PEM CA bundle for RustyDB.")
        database = RustyDB(
            RustyDBConfig(
                host=app.config["RUSTYDB_HOST"],
                port=app.config["RUSTYDB_WIRE_PORT"],
                user=app.config["RUSTYDB_USERNAME"],
                password=app.config["RUSTYDB_PASSWORD"],
                tls_ca=tls_ca,
                connect_timeout=app.config["RUSTYDB_CONNECT_TIMEOUT"],
            )
        )
    app.extensions["matcharr_db"] = database
    set_db(database)

    from .identity.routes import bp as identity_bp
    from .rooms.api import bp as rooms_api_bp
    from .rooms.routes import bp as rooms_bp
    from .plex.coordinator import MediaSyncCoordinator
    from .plex.routes import bp as plex_bp

    app.register_blueprint(identity_bp)
    app.register_blueprint(rooms_bp)
    app.register_blueprint(rooms_api_bp)
    app.register_blueprint(plex_bp)
    media_sync_coordinator = MediaSyncCoordinator(app)
    app.extensions["media_sync_coordinator"] = media_sync_coordinator

    @app.before_request
    def csrf_setup():
        if app.config["MATCHARR_AUTO_SYNC"] and not app.config["TESTING"]:
            media_sync_coordinator.trigger_startup()
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        g.csrf_token = session["csrf_token"]
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.headers.get("X-CSRF-Token")
            if not supplied and not request.is_json:
                supplied = request.form.get("csrf_token")
            if not supplied or not secrets.compare_digest(supplied, session["csrf_token"]):
                return jsonify(error="Invalid CSRF token"), 400

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @app.context_processor
    def template_context():
        media_server_name = (
            "Jellyfin" if app.config["MEDIA_SERVER_TYPE"] == "jellyfin" else "Plex"
        )
        return {
            "csrf_token": lambda: session.get("csrf_token", ""),
            "media_server_name": media_server_name,
            "media_server_type": app.config["MEDIA_SERVER_TYPE"],
        }

    @app.cli.command("db-migrate")
    def migrate_command():
        completed = apply_migrations(database, Path(app.root_path).parent / "migrations")
        print("Applied: " + (", ".join(completed) if completed else "none"))

    @app.get("/health")
    def health():
        try:
            db_ok = database.ping()
        except Exception:
            db_ok = False
        try:
            sync = database.fetch_one(
                "SELECT status, movies_received, movies_created, movies_updated, "
                "error_message, started_at, completed_at FROM plex_sync_runs "
                "ORDER BY started_at DESC LIMIT 1"
            )
        except Exception:
            sync = None
        payload = {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "unavailable",
            "media_server": app.config["MEDIA_SERVER_TYPE"],
            "media_last_sync": sync or {"status": "never"},
            "plex_last_sync": sync or {"status": "never"},
        }
        return jsonify(payload), 200 if db_ok else 503

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", message="That page could not be found."), 404

    @app.errorhandler(500)
    def internal_error(_error):
        return render_template("error.html", message="Something went wrong. Please try again."), 500

    return app
