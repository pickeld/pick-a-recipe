"""
Pick-a-Recipe Web UI
A Flask-based web interface for video recipe extraction with authentication and configuration management.
Supports parallel job processing with progress persistence.
"""

import os
import sys
import base64
import secrets
import tempfile
import threading
import json
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room

from database import (
    init_db, load_config, save_config,
    get_user, upsert_oidc_user,
    get_history, get_history_entry, get_history_count, delete_history_entry,
    delete_history_entries_bulk, delete_job_entry, delete_jobs_bulk,
    get_combined_history_and_jobs, get_combined_history_and_jobs_count,
    get_job, get_active_jobs,
    list_jobs_by_states, count_jobs_by_states, update_job_priority,
    get_pending_upload, get_pending_upload_by_job, get_pending_uploads,
    cleanup_expired_pending_uploads, cleanup_old_jobs,
    save_push_subscription, get_push_subscriptions, delete_push_subscription,
)
from job_manager import (
    init_job_manager, get_job_manager, resolve_max_concurrent,
    prune_artifact_dirs,
)
from uploaders import upload_recipe_to_targets, get_enabled_targets, format_targets

app = Flask(__name__)

# Behind reverse proxy (Cloudflare / NPM): trust X-Forwarded-* so _external URLs use HTTPS.
if os.environ.get('TRUST_PROXY', 'true').lower() in ('true', '1', 'yes'):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Path to the Vite SPA build output (ui/frontend/dist/)
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend', 'dist')

@app.route('/manifest.json')
def serve_manifest():
    dist_manifest = os.path.join(FRONTEND_DIST, 'manifest.webmanifest')
    if os.path.exists(dist_manifest):
        response = make_response(send_from_directory(FRONTEND_DIST, 'manifest.webmanifest'))
        response.headers['Content-Type'] = 'application/manifest+json'
        response.headers['Cache-Control'] = 'public, max-age=3600'
        return response
    response = make_response(app.send_static_file('manifest.json'))
    response.headers['Content-Type'] = 'application/manifest+json'
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

@app.route('/sw.js')
def serve_sw():
    dist_sw = os.path.join(FRONTEND_DIST, 'sw.js')
    if os.path.exists(dist_sw):
        response = make_response(send_from_directory(FRONTEND_DIST, 'sw.js'))
        response.headers['Content-Type'] = 'application/javascript'
        response.headers['Service-Worker-Allowed'] = '/'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    response = make_response(app.send_static_file('sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

# Secret key for session cookies - MUST be persistent across restarts
# Generate a stable key based on a file if FLASK_SECRET_KEY is not set
def _get_or_create_secret_key():
    """Get secret key from env or generate and persist one."""
    env_key = os.environ.get('FLASK_SECRET_KEY')
    if env_key:
        return env_key
    
    # Store the key in a file so it persists across restarts
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.flask_secret_key')
    if os.path.exists(key_file):
        with open(key_file, 'r') as f:
            return f.read().strip()
    
    # Generate and save a new key
    new_key = secrets.token_hex(32)
    try:
        with open(key_file, 'w') as f:
            f.write(new_key)
        os.chmod(key_file, 0o600)  # Restrict permissions
    except (IOError, OSError):
        pass  # If we can't write, still use the key for this session
    return new_key


# Health endpoints (intentionally unauthenticated so container/orchestrator
# health probes can reach them). /healthz is a cheap liveness ping; /api/health
# runs the yt-dlp + LLM checks and returns 200 when healthy, 503 otherwise.
@app.route('/healthz')
def healthz():
    return jsonify({'status': 'ok'}), 200


@app.route('/api/health')
def api_health():
    probe = request.args.get('probe') in ('1', 'true', 'yes')
    try:
        from health import run_health_checks
        report = run_health_checks(probe_network=probe)
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 503
    return jsonify(report), (200 if report['ok'] else 503)


app.secret_key = _get_or_create_secret_key()

# Configure session cookie settings
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
if os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('true', '1', 'yes'):
    app.config['SESSION_COOKIE_SECURE'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')


@app.context_processor
def inject_template_globals():
    """Expose debug flag, cache-bust version and approvals badge count."""
    import time
    return {
        'flask_debug': FLASK_DEBUG,
        'static_version': str(int(time.time())) if FLASK_DEBUG else '7',
        'approvals_count': _count_pending_approvals(),
    }


def _count_pending_approvals() -> int:
    """Live count for the sidebar badge, scoped to the signed-in user."""
    if not _is_logged_in():
        return 0
    try:
        from database import get_db
        params = []
        sql = ("SELECT COUNT(*) FROM pending_uploads "
               "WHERE status = 'pending' "
               "AND (expires_at IS NULL OR expires_at > datetime('now'))")
        if not _current_user_is_admin():
            sql += ' AND (user_id IS NULL OR user_id = ?)'
            params.append(_current_username())
        with get_db() as conn:
            return conn.execute(sql, params).fetchone()[0]
    except Exception as exc:
        print(f'[Badge] approvals count failed: {exc}')
        return 0


# Use threading mode instead of eventlet to avoid monkey-patching issues with SSL/requests
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Initialize database
init_db()

# Run startup health checks (yt-dlp + configured LLM). Non-fatal: logs clear,
# actionable errors so operators catch the top outage classes (PIC-34/PIC-42)
# before a user does. Results are also exposed via /api/health.
try:
    from health import run_startup_health_check
    run_startup_health_check()
except Exception as _health_exc:  # never let health checks block startup
    print(f"[Health] startup health check skipped: {_health_exc}")

# Initialize job manager (process func registered after definition below)
job_manager = init_job_manager(socketio)

# ===== Authentication mode =====
# 'authentik' (default) gates access behind Authentik single sign-on. 'none'
# turns authentication off and serves every request as one implicit local admin,
# for self-hosted setups that have no identity provider. Opting out is explicit
# on purpose: an unauthenticated instance exposes the LLM/Mealie/Tandoor API keys
# stored in Settings to anyone who can reach the port.
AUTH_MODE = (os.environ.get('AUTH_MODE') or 'authentik').strip().lower()
if AUTH_MODE not in ('authentik', 'none'):
    raise RuntimeError(
        f"Invalid AUTH_MODE={AUTH_MODE!r}. Use 'authentik' for Authentik single "
        "sign-on (default), or 'none' to run without authentication."
    )
AUTH_DISABLED = AUTH_MODE == 'none'
LOCAL_USERNAME = (os.environ.get('AUTH_LOCAL_USERNAME') or 'local').strip() or 'local'

# ===== Authentication via Authentik (OIDC) =====
# Single sign-on through the self-hosted Authentik instance at auth.pickel.me.
# Access requires membership in AUTHENTIK_USER_GROUP (admins additionally in
# AUTHENTIK_ADMIN_GROUP). In Authentik, add the "authentik read groups" scope
# mapping to the provider so the `groups` claim is included in tokens.
AUTHENTIK_ISSUER_URL = os.environ.get(
    'AUTHENTIK_ISSUER_URL',
    'https://auth.pickel.me/application/o/pick-a-recipe',
).rstrip('/')
AUTHENTIK_USER_GROUP = os.environ.get('AUTHENTIK_USER_GROUP', 'pick-a-recipe-users')
AUTHENTIK_ADMIN_GROUP = os.environ.get('AUTHENTIK_ADMIN_GROUP', 'admins')

oauth = None
if AUTH_DISABLED:
    from database import ensure_local_user
    ensure_local_user(LOCAL_USERNAME)
    print(f"[Auth] WARNING: AUTH_MODE=none — authentication is DISABLED. Every "
          f"request runs as local admin '{LOCAL_USERNAME}'. Only run this on a "
          f"trusted network; do not expose it to the internet.")
elif os.environ.get('AUTHENTIK_CLIENT_ID') and os.environ.get('AUTHENTIK_CLIENT_SECRET'):
    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)
    oauth.register(
        name='authentik',
        client_id=os.environ['AUTHENTIK_CLIENT_ID'],
        client_secret=os.environ['AUTHENTIK_CLIENT_SECRET'],
        server_metadata_url=f'{AUTHENTIK_ISSUER_URL}/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile groups'},
    )
else:
    print('[Auth] WARNING: AUTHENTIK_CLIENT_ID / AUTHENTIK_CLIENT_SECRET are not set — '
          'nobody can sign in. Configure Authentik OIDC, or set AUTH_MODE=none to '
          'run without authentication.')


def _is_logged_in() -> bool:
    return AUTH_DISABLED or 'user' in session


def _current_user_is_admin() -> bool:
    if AUTH_DISABLED:
        return True
    return bool(session.get('is_admin'))


def _current_username() -> str | None:
    """Effective owner for the request, independent of the session cookie.

    WebSocket handlers can run before any HTTP request has seeded the session,
    so AUTH_MODE=none needs a fallback that does not depend on the cookie.
    """
    if AUTH_DISABLED:
        return session.get('user') or LOCAL_USERNAME
    return session.get('user')


@app.before_request
def _seed_local_identity():
    """AUTH_MODE=none has no login flow, so stamp the local identity onto the
    session that the rest of the app reads for ownership scoping."""
    if AUTH_DISABLED and session.get('user') != LOCAL_USERNAME:
        session['user'] = LOCAL_USERNAME
        session['is_admin'] = True
        session.permanent = True


def _scope() -> tuple[str | None, bool]:
    """Owner scope for the current request: (user_id, is_admin)."""
    return _current_username(), _current_user_is_admin()


def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def api_login_required(f):
    """Decorator to require login for API routes (returns JSON error)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function


def _socketio_authenticated() -> bool:
    return _is_logged_in()


def _start_job_for_url(url: str, *, retry_from_history_id: int | None = None,
                       priority: int = 0, user_id: str | None = None) -> dict:
    jm = get_job_manager()
    if user_id is None:
        user_id = _current_username()
    job_id = jm.create_new_job(
        url, retry_from_history_id=retry_from_history_id,
        priority=priority, user_id=user_id,
    )
    jm.start_job(job_id, process_video_job)
    job = get_job(job_id)
    return {
        'job_id': job_id,
        'status': job.get('status', 'queued'),
        'url': url,
        'queue_position': job.get('queue_position', get_queue_position_safe(job_id)),
        'message': 'Job queued for processing',
    }


def get_queue_position_safe(job_id: str) -> int:
    from database import get_queue_position
    return get_queue_position(job_id)


def _run_cleanup_scheduler() -> None:
    """Periodic cleanup of old jobs and expired pending uploads."""
    def loop():
        import time
        while True:
            time.sleep(3600)
            try:
                cleanup_old_jobs(hours=72)
                cleanup_expired_pending_uploads()
                prune_artifact_dirs()
            except Exception as exc:
                print(f"[Cleanup] error: {exc}")

    t = threading.Thread(target=loop, daemon=True)
    t.start()


_run_cleanup_scheduler()


@app.route('/')
@login_required
def index():
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')

    shared_url = (
        session.pop('shared_url', None) or
        request.args.get('shared_url') or
        request.args.get('shared_text') or
        request.args.get('url') or
        request.args.get('text') or
        ''
    )
    auto_from_share = session.pop('auto_start_extraction', False)

    if shared_url and not shared_url.startswith('http'):
        import re
        url_match = re.search(r'(https?://[^\s]+)', shared_url)
        if url_match:
            shared_url = url_match.group(1)

    return render_template(
        'index.html',
        shared_url=shared_url,
        auto_start=(
            request.args.get('auto') in ('1', 'true', 'yes')
            or auto_from_share
        ),
        max_concurrent=resolve_max_concurrent(),
    )


@app.route('/jobs/<job_id>')
@login_required
def job_detail(job_id):
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')

    job = get_job(job_id)
    if not job:
        flash('Job not found', 'error')
        return redirect(url_for('index'))
    return render_template('job.html', job=job, max_concurrent=resolve_max_concurrent())


@app.route('/tasks')
@login_required
def tasks_page():
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return render_template('tasks.html')


@app.route('/share', methods=['GET', 'POST'])
def share():
    """Handle shared URLs from PWA share_target.
    
    NOTE: This route intentionally does NOT require login so that Android's
    share_target can POST data before authentication. The URL is saved to
    session first, then user is redirected to login if needed.
    """
    import re
    
    # Get shared content from POST form data (Android) or query params (fallback)
    if request.method == 'POST':
        shared_url = request.form.get('url') or ''
        shared_text = request.form.get('text') or ''
        shared_title = request.form.get('title', '')
    else:
        shared_url = request.args.get('url') or ''
        shared_text = request.args.get('text') or ''
        shared_title = request.args.get('title', '')
    
    # Try to extract URL from various sources
    # Priority: url param > text param > title param
    final_url = shared_url
    
    if not final_url and shared_text:
        # Apps like TikTok/Instagram often share URL in text field
        url_match = re.search(r'(https?://[^\s]+)', shared_text)
        if url_match:
            final_url = url_match.group(1)
        else:
            final_url = shared_text
    
    if not final_url and shared_title:
        url_match = re.search(r'(https?://[^\s]+)', shared_title)
        if url_match:
            final_url = url_match.group(1)
    
    # Store in session BEFORE checking auth - this preserves the URL through login
    session['shared_url'] = final_url
    session['auto_start_extraction'] = True
    
    # If user is not logged in, redirect to login (URL is preserved in session)
    if not _is_logged_in():
        return redirect(url_for('login'))
    
    # User is logged in, redirect to main page
    return redirect(url_for('index'))


@app.route('/login')
def login():
    """Login page — single sign-on via Authentik."""
    if _is_logged_in():
        return redirect(url_for('index'))
    spa_login = os.path.join(FRONTEND_DIST, 'index.html')
    if os.path.exists(spa_login):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return render_template('login.html', sso_enabled=oauth is not None)


def _authentik_redirect_uri() -> str:
    """OIDC callback URL — must match the redirect URI configured in Authentik."""
    explicit = os.environ.get('AUTHENTIK_REDIRECT_URI', '').strip()
    if explicit:
        return explicit
    public = os.environ.get('PUBLIC_URL', '').strip().rstrip('/')
    if public:
        return f'{public}/auth/callback'
    return url_for('auth_callback', _external=True)


@app.route('/auth/login')
def auth_login():
    """Redirect the user to Authentik for authentication."""
    if AUTH_DISABLED:
        return redirect(url_for('index'))
    if oauth is None:
        flash('Single sign-on is not configured. Set AUTHENTIK_CLIENT_ID and '
              'AUTHENTIK_CLIENT_SECRET, or set AUTH_MODE=none to run without '
              'authentication.', 'error')
        return redirect(url_for('login'))
    return oauth.authentik.authorize_redirect(_authentik_redirect_uri())


@app.route('/auth/callback')
def auth_callback():
    """Handle the OIDC callback from Authentik."""
    if oauth is None:
        return redirect(url_for('login'))

    if 'error' in request.args:
        reason = request.args.get('error_description') or request.args.get('error')
        flash(f'Sign-in failed: {reason}', 'error')
        return redirect(url_for('login'))

    try:
        token = oauth.authentik.authorize_access_token()
    except Exception as exc:
        print(f"[Auth] token exchange failed: {exc}")
        flash('Sign-in failed. Please try again.', 'error')
        return redirect(url_for('login'))

    userinfo = token.get('userinfo') or oauth.authentik.parse_id_token(token)
    sub = userinfo.get('sub')
    if not sub:
        flash('Sign-in failed: identity provider did not provide a subject claim.', 'error')
        return redirect(url_for('login'))

    groups = set(userinfo.get('groups') or [])
    is_admin = AUTHENTIK_ADMIN_GROUP in groups
    if not is_admin and AUTHENTIK_USER_GROUP not in groups:
        print(f"[Auth] denied login for sub={sub}: groups={sorted(groups)}")
        flash('Your account is not authorized to use Pick-a-Recipe. Ask an '
              'administrator to add you to the appropriate group in Authentik.', 'error')
        return redirect(url_for('login'))

    username = (
        userinfo.get('preferred_username')
        or (userinfo.get('email') or '').split('@')[0]
        or sub
    )

    pending_shared_url = session.get('shared_url')
    pending_auto_start = session.get('auto_start_extraction')

    user = upsert_oidc_user(
        sub=sub,
        username=username,
        email=userinfo.get('email'),
        name=userinfo.get('name'),
        avatar_url=userinfo.get('picture'),
        is_admin=is_admin,
    )

    session.clear()
    session['user'] = user['username']
    session['is_admin'] = is_admin
    session.permanent = True

    if pending_shared_url:
        session['shared_url'] = pending_shared_url
    if pending_auto_start:
        session['auto_start_extraction'] = True

    flash(f"Welcome, {user.get('name') or user['username']}!", 'success')
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    """Log out: clear the local session and end the Authentik session."""
    if AUTH_DISABLED:
        # Nothing to sign out of; _seed_local_identity would re-create the
        # session on the next request anyway.
        return redirect(url_for('index'))

    session.pop('user', None)
    session.pop('is_admin', None)

    end_session_url = None
    if oauth is not None:
        try:
            end_session_url = oauth.authentik.load_server_metadata().get('end_session_endpoint')
        except Exception as exc:
            print(f"[Auth] could not load OIDC metadata for logout: {exc}")

    if end_session_url:
        return redirect(end_session_url)
    return redirect(url_for('login'))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Settings page for configuration."""
    config = load_config()

    if request.method == 'POST':
        # Update configuration from form
        config['llm_provider'] = request.form.get('llm_provider', 'openai')
        config['openai_api_key'] = request.form.get('openai_api_key', '')
        config['openai_model'] = request.form.get('openai_model', '')
        config['gemini_api_key'] = request.form.get('gemini_api_key', '')
        config['gemini_model'] = request.form.get('gemini_model', '')
        config['recipe_lang'] = request.form.get('recipe_lang', 'hebrew')
        config['mealie_api_key'] = request.form.get('mealie_api_key', '')
        config['mealie_host'] = request.form.get('mealie_host', '')
        config['tandoor_api_key'] = request.form.get('tandoor_api_key', '')
        config['tandoor_host'] = request.form.get('tandoor_host', '')
        config['target_language'] = request.form.get('target_language', 'he')
        config['tandoor_enabled'] = 'true' if request.form.get('tandoor_enabled') else 'false'
        config['mealie_enabled'] = 'true' if request.form.get('mealie_enabled') else 'false'
        config['whisper_model'] = request.form.get('whisper_model', 'small')
        config['hf_token'] = request.form.get('hf_token', '')
        config['yt_dlp_cookies_file'] = request.form.get('yt_dlp_cookies_file', '')
        config['yt_dlp_cookies_browser'] = request.form.get('yt_dlp_cookies_browser', '')
        # Checkbox: present in form data only when checked
        config['confirm_before_upload'] = 'true' if request.form.get(
            'confirm_before_upload') else 'false'
        config['max_concurrent_jobs'] = request.form.get('max_concurrent_jobs', '3')

        save_config(config)
        get_job_manager().refresh_concurrency()
        flash('Settings saved successfully!', 'success')
        return redirect(url_for('settings'))

    return render_template(
        'settings.html',
        config=config,
        max_concurrent=resolve_max_concurrent(),
    )


# ===== Job API Endpoints =====

@app.route('/api/jobs', methods=['POST'])
@api_login_required
def create_job():
    """Create a new analysis job."""
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    result = _start_job_for_url(url)
    return jsonify(result)


@app.route('/api/jobs/batch', methods=['POST'])
@api_login_required
def create_jobs_batch():
    """Create multiple jobs from a list of URLs."""
    data = request.get_json() or {}
    urls = data.get('urls') or []
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.replace(',', '\n').split('\n') if u.strip()]
    if not urls:
        return jsonify({'error': 'No URLs provided'}), 400

    jobs = []
    for url in urls[:50]:
        jobs.append(_start_job_for_url(url.strip()))
    return jsonify({'jobs': jobs, 'count': len(jobs)})


@app.route('/api/jobs/retry', methods=['POST'])
@api_login_required
def retry_job():
    """Retry a failed extraction — starts immediately with live progress."""
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    history_id = data.get('history_id')

    if not url and history_id:
        item = get_history_entry(int(history_id))
        if item:
            url = item.get('url', '')

    if not url:
        return jsonify({'error': 'URL or history_id is required'}), 400

    result = _start_job_for_url(
        url,
        retry_from_history_id=int(history_id) if history_id else None,
        priority=1,
        user_id=session['user'],
    )
    result['auto_start'] = True
    return jsonify(result)


@app.route('/api/jobs/queue', methods=['GET'])
@api_login_required
def queue_stats():
    jm = get_job_manager()
    return jsonify(jm.get_queue_stats())


@app.route('/api/jobs', methods=['GET'])
@api_login_required
def list_jobs():
    """List all active jobs visible to the requesting user."""
    user_id, is_admin = _scope()
    jobs = get_active_jobs(user_id=user_id, is_admin=is_admin)
    return jsonify({'jobs': jobs})


@app.route('/api/jobs/<job_id>', methods=['GET'])
@api_login_required
def get_job_status(job_id):
    """Get status of a specific job."""
    jm = get_job_manager()
    user_id, is_admin = _scope()
    job = jm.get_job_status(job_id, user_id=user_id, is_admin=is_admin)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
@api_login_required
def cancel_job_api(job_id):
    """Cancel a running job."""
    jm = get_job_manager()
    user_id, is_admin = _scope()
    job = get_job(job_id, user_id=user_id, is_admin=is_admin)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    result = jm.cancel_job(job_id)
    if result:
        return jsonify({'status': 'cancelled', 'job_id': job_id})
    return jsonify({'error': 'Job not found or already completed'}), 404


_TASK_STATE_GROUPS = {
    'pending': ['queued'],
    'processing': ['running', 'uploading'],
    'awaiting_approval': ['awaiting_approval'],
    'active': ['queued', 'running', 'awaiting_approval', 'uploading'],
}
_ALL_STATES = ['queued', 'running', 'awaiting_approval', 'uploading',
               'completed', 'failed', 'cancelled', 'expired']


@app.route('/api/tasks', methods=['GET'])
@api_login_required
def list_tasks():
    """Unified task listing for the dashboard."""
    user_id, is_admin = _scope()
    scope = request.args.get('scope', 'mine')
    if scope == 'all' and not is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    state = request.args.get('state', 'active')
    limit = min(request.args.get('limit', 100, type=int), 500)
    offset = max(request.args.get('offset', 0, type=int), 0)

    scoped_user = None if scope == 'all' else user_id
    if state == 'recent':
        states = ['completed', 'failed', 'cancelled', 'expired']
    elif state == 'all':
        states = _ALL_STATES
    else:
        states = _TASK_STATE_GROUPS.get(state)
        if states is None:
            return jsonify({'error': f'Unknown state: {state}'}), 400

    tasks = list_jobs_by_states(
        states,
        user_id=scoped_user, is_admin=is_admin,
        limit=limit, offset=offset,
        updated_since_hours=72 if state == 'recent' else None,
    )
    counts = count_jobs_by_states(user_id=scoped_user, is_admin=is_admin)
    return jsonify({'tasks': tasks, 'counts': counts})


@app.route('/api/tasks/bulk', methods=['POST'])
@api_login_required
def bulk_task_action():
    """Apply an action to many jobs at once: cancel | approve | reject."""
    data = request.get_json() or {}
    action = data.get('action')
    ids = data.get('ids') or []
    if action not in ('cancel', 'approve', 'reject'):
        return jsonify({'error': 'action must be cancel, approve or reject'}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'ids must be a non-empty list'}), 400

    jm = get_job_manager()
    user_id, is_admin = _scope()
    results = []
    for raw_id in ids[:100]:
        job_id = str(raw_id)
        job = get_job(job_id, user_id=user_id, is_admin=is_admin)
        if not job:
            results.append({'id': job_id, 'ok': False, 'error': 'not found'})
            continue
        if action == 'cancel':
            ok = jm.cancel_job(job['id'])
            results.append({'id': job['id'], 'ok': bool(ok),
                            **({} if ok else {'error': 'not cancellable'})})
            continue

        upload = get_pending_upload_by_job(job['id'])
        if not upload or upload['status'] != 'pending':
            results.append({'id': job['id'], 'ok': False,
                            'error': 'no pending approval'})
            continue
        outcome = (jm.confirm_approval(upload['id'])
                   if action == 'approve' else jm.reject_approval(upload['id']))
        entry = {'id': job['id'], 'ok': bool(outcome.get('ok'))}
        if not outcome.get('ok'):
            entry['error'] = outcome.get('error')
        results.append(entry)

    succeeded = sum(1 for r in results if r['ok'])
    return jsonify({'results': results, 'succeeded': succeeded,
                    'failed': len(results) - succeeded})


@app.route('/api/jobs/<job_id>/priority', methods=['PATCH'])
@api_login_required
def set_job_priority(job_id):
    """Reposition a queued job."""
    data = request.get_json() or {}
    try:
        priority = int(data.get('priority'))
    except (TypeError, ValueError):
        return jsonify({'error': 'priority must be an integer'}), 400

    user_id, is_admin = _scope()
    job = get_job(job_id, user_id=user_id, is_admin=is_admin)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    if job['status'] != 'queued':
        return jsonify({'error': 'Only queued jobs can be reordered'}), 409

    if not update_job_priority(job_id, priority,
                               user_id=user_id, is_admin=is_admin):
        return jsonify({'error': 'Job not found'}), 404
    get_job_manager().refresh_queue_positions()
    return jsonify({'job_id': job_id, 'priority': priority})


# ===== History API Endpoints =====

@app.route('/api/history', methods=['GET'])
@api_login_required
def get_history_api():
    """Get recipe history with pagination and filtering."""
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    status = request.args.get('status')
    search = request.args.get('search')
    
    items = get_history(limit=limit, offset=offset, status_filter=status, search=search)
    total = get_history_count(status_filter=status, search=search)
    
    return jsonify({
        'items': items,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@app.route('/api/history/<int:history_id>', methods=['GET'])
@api_login_required
def get_history_item(history_id):
    """Get a single history entry."""
    item = get_history_entry(history_id)
    if not item:
        return jsonify({'error': 'History entry not found'}), 404
    return jsonify(item)


@app.route('/api/history/<int:history_id>', methods=['DELETE'])
@api_login_required
def delete_history_item(history_id):
    """Delete a history entry."""
    result = delete_history_entry(history_id)
    if result:
        return jsonify({'status': 'deleted', 'id': history_id})
    return jsonify({'error': 'History entry not found'}), 404


@app.route('/api/history/bulk-delete', methods=['POST'])
@api_login_required
def bulk_delete_history():
    """Delete multiple history entries and/or job entries at once."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    history_ids = data.get('history_ids', [])
    job_ids = data.get('job_ids', [])
    
    if not history_ids and not job_ids:
        return jsonify({'error': 'No items to delete'}), 400
    
    deleted_history = 0
    deleted_jobs = 0
    
    if history_ids:
        deleted_history = delete_history_entries_bulk(history_ids)
    
    if job_ids:
        # Ownership gate: silently skip jobs the caller may not touch.
        user_id, is_admin = _scope()
        owned = [
            str(jid) for jid in job_ids
            if get_job(str(jid), user_id=user_id, is_admin=is_admin)
        ]
        if owned:
            deleted_jobs = delete_jobs_bulk(owned)
    
    total_deleted = deleted_history + deleted_jobs
    return jsonify({
        'status': 'deleted',
        'deleted_count': total_deleted,
        'deleted_history': deleted_history,
        'deleted_jobs': deleted_jobs
    })


@app.route('/api/recipes', methods=['GET'])
@api_login_required
def get_recipes_api():
    """Get combined recipe history and active jobs with pagination and filtering.
    
    This endpoint provides a unified view of:
    - Completed/failed recipes from history
    - In-progress jobs
    - Cancelled jobs
    """
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    status = request.args.get('status')
    search = request.args.get('search')
    
    items = get_combined_history_and_jobs(limit=limit, offset=offset, status_filter=status, search=search)
    total = get_combined_history_and_jobs_count(status_filter=status, search=search)
    
    return jsonify({
        'items': items,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@app.route('/api/jobs/<job_id>/delete', methods=['DELETE'])
@api_login_required
def delete_job_api(job_id):
    """Delete a job entry (for cancelled/failed jobs that aren't in history)."""
    user_id, is_admin = _scope()
    if not get_job(job_id, user_id=user_id, is_admin=is_admin):
        return jsonify({'error': 'Job not found'}), 404
    result = delete_job_entry(job_id)
    if result:
        return jsonify({'status': 'deleted', 'job_id': job_id})
    return jsonify({'error': 'Job not found'}), 404


def _resolve_history_image(item):
    """
    Resolve a usable image file for a history entry.

    Prefers the original file on disk, but falls back to the base64 thumbnail
    stored in the database (the on-disk copy under /tmp does not survive a
    container restart).

    Returns:
        (path, is_temp) where is_temp means the caller must delete the file.
    """
    image_path = item.get('thumbnail_path')
    if image_path and os.path.exists(image_path):
        return image_path, False

    thumbnail_data = item.get('thumbnail_data')
    if not thumbnail_data:
        return None, False

    try:
        raw = base64.b64decode(thumbnail_data)
    except (ValueError, TypeError):
        return None, False

    # Keep the .jpg suffix: both exporters derive the content type from it,
    # and thumbnails are stored as JPEG.
    try:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(raw)
            return tmp.name, True
    except OSError:
        return None, False


@app.route('/api/history/<int:history_id>/reupload', methods=['POST'])
@api_login_required
def reupload_recipe(history_id):
    """Re-upload a recipe from history to the target."""
    from config import config
    
    item = get_history_entry(history_id)
    if not item:
        return jsonify({'error': 'History entry not found'}), 404
    
    if not item.get('recipe_data'):
        return jsonify({'error': 'No recipe data available for this entry'}), 400
    
    recipe_data = item['recipe_data']

    # Get target from request or use the first enabled one
    data = request.get_json() or {}
    enabled = get_enabled_targets()
    target = data.get('target', enabled[0] if enabled else '')

    # The original image lives under /tmp, which is not persisted across
    # container restarts. Fall back to the base64 copy kept in history so the
    # image still gets pushed. Returns (path, cleanup_flag).
    image_path, image_is_temp = _resolve_history_image(item)

    try:
        config.reload()
        image_uploaded = False

        if target == 'tandoor':
            from tandoor import Tandoor
            tandoor = Tandoor()
            result = tandoor.create_recipe(recipe_data)
            if image_path and result.get("id"):
                image_uploaded = bool(tandoor.upload_image(result["id"], image_path))
        elif target == 'mealie':
            from mealie import Mealie
            mealie = Mealie()
            result = mealie.create_recipe(recipe_data)
            recipe_slug = result.get("slug") or result.get("id")
            if image_path and recipe_slug:
                image_uploaded = bool(mealie.upload_image(recipe_slug, image_path))
        else:
            hint = ('no recipe manager is enabled — enable Mealie and/or '
                    'Tandoor in Settings') if not target else f'Unknown target: {target}'
            return jsonify({'error': hint}), 400

        message = f'Recipe re-uploaded to {target}'
        if image_path and not image_uploaded:
            message += ' (image upload failed)'
        elif not image_path:
            message += ' (no image available)'

        return jsonify({
            'status': 'success',
            'message': message,
            'target': target,
            'image_uploaded': image_uploaded,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if image_is_temp and image_path:
            try:
                os.remove(image_path)
            except OSError:
                pass


# ===== SPA Support API Endpoints =====

@app.route('/api/me', methods=['GET'])
@api_login_required
def api_me():
    return jsonify({
        'user': session['user'],
        'is_admin': bool(session.get('is_admin', False)),
        'auth_disabled': AUTH_DISABLED,
        'shared_url': session.pop('shared_url', None),
        'auto_start': bool(session.pop('auto_start_extraction', False)),
    })


@app.route('/api/auth/status', methods=['GET'])
def api_auth_status():
    return jsonify({
        'sso_enabled': oauth is not None,
        'auth_disabled': AUTH_DISABLED,
    })


@app.route('/api/config', methods=['GET'])
@api_login_required
def api_get_config():
    return jsonify(load_config())


@app.route('/api/config', methods=['POST'])
@api_login_required
def api_post_config():
    from config import DEFAULT_CONFIG
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'JSON object required'}), 400
    valid_keys = set(DEFAULT_CONFIG.keys())
    filtered = {k: v for k, v in data.items() if k in valid_keys}
    if not filtered:
        return jsonify({'error': 'No valid configuration keys provided'}), 400
    current = load_config()
    current.update(filtered)
    save_config(current)
    return jsonify({'status': 'success', 'saved_keys': list(filtered.keys())})


# ===== Settings Export/Import API Endpoints =====

@app.route('/api/settings/export', methods=['GET'])
@api_login_required
def export_settings():
    """Export all settings as JSON for backup/transfer."""
    config = load_config()
    
    # Create export data with metadata
    export_data = {
        'version': '1.0',
        'exported_at': datetime.now().isoformat(),
        'settings': config
    }
    
    return jsonify(export_data)


@app.route('/api/settings/import', methods=['POST'])
@api_login_required
def import_settings():
    """Import settings from a JSON backup file."""
    from config import DEFAULT_CONFIG
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    # Handle both direct settings and wrapped format
    if 'settings' in data:
        settings = data['settings']
    else:
        settings = data
    
    # Validate that we have a dictionary
    if not isinstance(settings, dict):
        return jsonify({'error': 'Invalid settings format'}), 400
    
    # Only import known configuration keys
    valid_keys = set(DEFAULT_CONFIG.keys())
    filtered_settings = {k: v for k, v in settings.items() if k in valid_keys}
    
    if not filtered_settings:
        return jsonify({'error': 'No valid settings found in import data'}), 400
    
    # Save the imported settings
    current_config = load_config()
    current_config.update(filtered_settings)
    save_config(current_config)
    
    return jsonify({
        'status': 'success',
        'message': f'Imported {len(filtered_settings)} settings',
        'imported_keys': list(filtered_settings.keys())
    })


@app.route('/api/cookies/upload', methods=['POST'])
@api_login_required
def upload_cookies_file():
    """Upload a cookies.txt file for yt-dlp authentication.
    
    Saves the uploaded file to the data directory and updates the config.
    """
    if 'cookies_file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['cookies_file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Validate file extension
    if not file.filename.endswith('.txt'):
        return jsonify({'error': 'File must be a .txt file'}), 400
    
    # Read and validate content looks like a cookies file
    content = file.read().decode('utf-8', errors='ignore')
    
    # Basic validation: Netscape cookies files typically start with a comment
    # or have tab-separated values with domain names
    if not content.strip():
        return jsonify({'error': 'File is empty'}), 400
    
    # Check for basic cookies file structure (domain, flag, path, secure, expiration, name, value)
    lines = content.strip().split('\n')
    valid_lines = 0
    for line in lines:
        line = line.strip()
        if line.startswith('#') or not line:
            continue  # Comment or empty line
        parts = line.split('\t')
        if len(parts) >= 7:
            valid_lines += 1
    
    if valid_lines == 0:
        return jsonify({'error': 'File does not appear to be a valid Netscape cookies.txt format'}), 400
    
    # Save the file to the data directory
    from config import DATA_DIR
    cookies_path = os.path.join(DATA_DIR, 'cookies.txt')
    
    try:
        with open(cookies_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except IOError as e:
        return jsonify({'error': f'Failed to save cookies file: {str(e)}'}), 500
    
    # Update the configuration
    config = load_config()
    config['yt_dlp_cookies_file'] = cookies_path
    save_config(config)
    
    return jsonify({
        'status': 'success',
        'message': f'Cookies file uploaded ({valid_lines} cookies found)',
        'path': cookies_path
    })


@app.route('/api/cookies/delete', methods=['DELETE'])
@api_login_required
def delete_cookies_file():
    """Delete the uploaded cookies file."""
    from config import DATA_DIR
    
    cookies_path = os.path.join(DATA_DIR, 'cookies.txt')
    
    if os.path.exists(cookies_path):
        try:
            os.remove(cookies_path)
        except IOError as e:
            return jsonify({'error': f'Failed to delete cookies file: {str(e)}'}), 500
    
    # Clear the configuration
    config = load_config()
    config['yt_dlp_cookies_file'] = ''
    save_config(config)
    
    return jsonify({
        'status': 'success',
        'message': 'Cookies file deleted'
    })


# ===== Pending Uploads API Endpoints =====

@app.route('/api/pending-uploads', methods=['GET'])
@api_login_required
def get_pending_uploads_api():
    """Get pending recipe uploads waiting for confirmation, scoped to the
    requesting user (admins see all).

    This allows any device/session of the owner to see pending uploads and
    confirm/cancel them.
    """
    # Clean up expired uploads first
    cleanup_expired_pending_uploads()
    jm = get_job_manager()
    user_id, is_admin = _scope()
    pending = jm.get_approvals(user_id=user_id, is_admin=is_admin)
    
    # Prepare response with image data for each pending upload
    results = []
    for upload in pending:
        item = {
            'upload_id': upload['id'],
            'job_id': upload['job_id'],
            'recipe': upload['recipe_data'],
            'output_target': _display_target(upload.get('output_target')),
            'best_image_index': upload.get('best_image_index', 0),
            'selected_image_index': upload.get('selected_image_index', 0),
            'url': upload.get('url'),
            'video_title': upload.get('video_title'),
            'created_at': upload.get('created_at'),
            'expires_at': upload.get('expires_at'),
        }
        
        # Load image data if available
        image_path = upload.get('image_path')
        if image_path and os.path.exists(image_path):
            with open(image_path, 'rb') as f:
                item['image_data'] = base64.b64encode(f.read()).decode('utf-8')
        
        # Load candidate images
        image_candidates = upload.get('image_candidates') or []
        candidate_images_data = []
        for idx, candidate_path in enumerate(image_candidates):
            if os.path.exists(candidate_path):
                with open(candidate_path, 'rb') as f:
                    candidate_images_data.append({
                        'index': idx,
                        'data': base64.b64encode(f.read()).decode('utf-8'),
                        'path': candidate_path,
                        'is_best': idx == upload.get('best_image_index', 0)
                    })
        item['candidate_images'] = candidate_images_data

        results.append(item)

    return jsonify({'pending_uploads': results})


@app.route('/api/pending-uploads/<upload_id>', methods=['GET'])
@api_login_required
def get_pending_upload_api(upload_id):
    """Get a specific pending upload by ID."""
    user_id, is_admin = _scope()
    upload = get_pending_upload(upload_id, user_id=user_id, is_admin=is_admin)
    if not upload or upload['status'] != 'pending':
        return jsonify({'error': 'Pending upload not found'}), 404
    
    item = {
        'upload_id': upload['id'],
        'job_id': upload['job_id'],
        'recipe': upload['recipe_data'],
        'output_target': _display_target(upload.get('output_target')),
        'best_image_index': upload.get('best_image_index', 0),
        'selected_image_index': upload.get('selected_image_index', 0),
        'created_at': upload.get('created_at'),
        'expires_at': upload.get('expires_at'),
    }
    
    # Load image data if available
    image_path = upload.get('image_path')
    if image_path and os.path.exists(image_path):
        with open(image_path, 'rb') as f:
            item['image_data'] = base64.b64encode(f.read()).decode('utf-8')
    
    # Load candidate images
    image_candidates = upload.get('image_candidates') or []
    candidate_images_data = []
    for idx, candidate_path in enumerate(image_candidates):
        if candidate_path and os.path.exists(candidate_path):
            with open(candidate_path, 'rb') as f:
                candidate_images_data.append({
                    'index': idx,
                    'data': base64.b64encode(f.read()).decode('utf-8'),
                    'path': candidate_path,
                    'is_best': idx == upload.get('best_image_index', 0)
                })
    item['candidate_images'] = candidate_images_data

    return jsonify(item)


@app.route('/api/pending-uploads/<upload_id>/confirm', methods=['POST'])
@api_login_required
def confirm_pending_upload_api(upload_id):
    """Confirm a pending upload via REST API (works from any device/session)."""
    data = request.get_json() or {}
    selected_image_index = data.get('selected_image_index')

    user_id, is_admin = _scope()
    upload = get_pending_upload(upload_id, user_id=user_id, is_admin=is_admin)
    if not upload:
        return jsonify({'error': 'Pending upload not found or already processed'}), 404

    jm = get_job_manager()
    result = jm.confirm_approval(upload_id, selected_image_index)
    if not result.get('ok'):
        return jsonify({'error': result.get('error', 'Already processed')}), 404
    return jsonify({'status': 'confirmed', 'upload_id': upload_id,
                    'job_id': result.get('job_id')})


@app.route('/api/pending-uploads/<upload_id>/cancel', methods=['POST'])
@api_login_required
def cancel_pending_upload_api(upload_id):
    """Cancel a pending upload via REST API (works from any device/session)."""
    user_id, is_admin = _scope()
    upload = get_pending_upload(upload_id, user_id=user_id, is_admin=is_admin)
    if not upload:
        return jsonify({'error': 'Pending upload not found or already processed'}), 404

    jm = get_job_manager()
    result = jm.reject_approval(upload_id)
    if not result.get('ok'):
        return jsonify({'error': result.get('error', 'Already processed')}), 404
    return jsonify({'status': 'cancelled', 'upload_id': upload_id,
                    'job_id': result.get('job_id')})


# ===== Legacy API (kept for backward compatibility) =====

@app.route('/api/push/subscribe', methods=['POST'])
@api_login_required
def push_subscribe():
    data = request.get_json() or {}
    sub = data.get('subscription') or data
    endpoint = sub.get('endpoint')
    keys = sub.get('keys') or {}
    if not endpoint or not keys.get('p256dh') or not keys.get('auth'):
        return jsonify({'error': 'Invalid subscription'}), 400
    ok = save_push_subscription(session['user'], endpoint, keys['p256dh'], keys['auth'])
    return jsonify({'status': 'subscribed' if ok else 'error'})


@app.route('/api/push/unsubscribe', methods=['POST'])
@api_login_required
def push_unsubscribe():
    data = request.get_json() or {}
    endpoint = data.get('endpoint')
    if endpoint:
        delete_push_subscription(endpoint)
    return jsonify({'status': 'unsubscribed'})


@app.route('/api/process', methods=['POST'])
@api_login_required
def process_video():
    """Start video processing (legacy endpoint - redirects to job system)."""
    data = request.get_json() or {}
    url = data.get('url', '')
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    result = _start_job_for_url(url)
    return jsonify({'status': 'started', 'message': 'Processing started', **result})


def _emit_preview_payload(payload: dict) -> None:
    """Emit a recipe preview only to the owning user's rooms."""
    rooms = [f"job_{payload.get('job_id')}"]
    owner = payload.get('owner')
    if owner:
        rooms.append(f'user_{owner}')
    for room in rooms:
        socketio.emit('recipe_preview', payload, room=room)


def _display_target(raw: str | None) -> str:
    """Pretty label for a stored output_target (legacy keys or new labels)."""
    from uploaders import TARGET_LABELS
    if not raw:
        return 'recipe manager'
    return TARGET_LABELS.get(raw.strip().lower(), raw)


def _build_preview_payload(upload: dict) -> dict:
    """Reconstruct a recipe_preview payload from a stored pending upload."""
    item = {
        'job_id': upload['job_id'],
        'upload_id': upload['id'],
        'recipe': upload.get('recipe_data'),
        'image_data': None,
        'candidate_images': [],
        'best_image_index': upload.get('best_image_index', 0),
        'output_target': _display_target(upload.get('output_target')),
        'owner': upload.get('user_id'),
    }
    image_path = upload.get('image_path')
    if image_path and os.path.exists(image_path):
        with open(image_path, 'rb') as f:
            item['image_data'] = base64.b64encode(f.read()).decode('utf-8')
    for idx, candidate_path in enumerate(upload.get('image_candidates') or []):
        if candidate_path and os.path.exists(candidate_path):
            with open(candidate_path, 'rb') as f:
                item['candidate_images'].append({
                    'index': idx,
                    'data': base64.b64encode(f.read()).decode('utf-8'),
                    'path': candidate_path,
                    'is_best': idx == upload.get('best_image_index', 0),
                })
    return item


def resume_upload_job(job_id: str, jm) -> None:
    """Upload-phase worker: push an approved artifact to its targets."""
    upload = get_pending_upload_by_job(job_id)
    job = get_job(job_id)
    if not upload or not job or job['status'] != 'uploading':
        return

    recipe_data = upload.get('recipe_data')
    if not recipe_data:
        jm.fail_job(job_id, 'Approved recipe artifact is missing or corrupt')
        return

    jm.update_progress(job_id, 'upload',
                       f"Uploading to {upload.get('output_target') or 'recipe manager'}...", 95)

    candidates = upload.get('image_candidates') or []
    selected = upload.get('selected_image_index', upload.get('best_image_index', 0)) or 0
    image_path = upload.get('image_path')
    if candidates and 0 <= selected < len(candidates):
        image_path = candidates[selected]

    target_count = len(get_enabled_targets())
    final_target, failures = upload_recipe_to_targets(recipe_data, image_path)

    if failures and len(failures) == target_count:
        msgs = '; '.join(f"{t}: {msg}" for t, msg in failures)
        jm.fail_job(job_id, f'All uploads failed: {msgs}')
        return

    if failures:
        msgs = '; '.join(f"{t}: {msg}" for t, msg in failures)
        jm.update_progress(job_id, 'complete',
                           f'Uploaded to {final_target}. Failed: {msgs}', 100)
    else:
        jm.update_progress(job_id, 'complete',
                           f'Recipe uploaded successfully to {final_target}!', 100)

    jm.complete_job(job_id, recipe_data, image_path, final_target)


def process_video_job(job_id, jm):
    """Background task — delegates to shared pipeline module."""
    from pipeline import (
        PipelineStats,
        PreviewWaiter,
        run_url_pipeline,
    )
    from config import config as app_config

    job = get_job(job_id)
    if not job:
        return

    stats = PipelineStats()

    class Reporter:
        def is_cancelled(self):
            return jm.is_cancelled(job_id)

        def update(self, stage, message, percent, video_title=None):
            jm.update_progress(job_id, stage, message, percent, video_title)

    reporter = Reporter()

    preview = None
    if app_config.CONFIRM_BEFORE_UPLOAD:
        def emit_preview(payload):
            payload['owner'] = job.get('user_id')
            _emit_preview_payload(payload)

        preview = PreviewWaiter(
            job_id=job_id,
            target_label=format_targets(get_enabled_targets()),
            emit_preview=emit_preview,
            open_approval_fn=lambda **kw: jm.open_approval(**kw),
        )

    result = run_url_pipeline(job['url'], reporter, stats=stats, preview=preview)

    if result.awaiting_approval:
        # Slot-free approval: worker returns; the slot is now free and the
        # upload happens in a fresh worker once the user approves.
        return
    if result.error == 'cancelled':
        return
    if result.error:
        jm.fail_job(job_id, result.error, stats.llm_tokens_estimate)
        return

    jm.complete_job(
        job_id,
        result.recipe_data,
        result.image_path,
        result.output_target,
        llm_tokens=result.llm_tokens_estimate or stats.llm_tokens_estimate,
    )


# ===== WebSocket Handlers =====

@socketio.on('connect')
def handle_connect():
    if not _socketio_authenticated():
        return False
    join_room(f"user_{_current_username()}")
    emit('connected', {'status': 'Connected to server'})


@socketio.on('subscribe_job')
def handle_subscribe_job(data):
    if not _socketio_authenticated():
        return False
    job_id = data.get('job_id')
    if job_id:
        user_id, is_admin = _scope()
        job = get_job(job_id, user_id=user_id, is_admin=is_admin)
        if not job:
            emit('error', {'message': 'Job not found'})
            return
        join_room(f'job_{job_id}')
        emit('subscribed', {'job_id': job_id, 'status': 'subscribed'})


@socketio.on('unsubscribe_job')
def handle_unsubscribe_job(data):
    if not _socketio_authenticated():
        return False
    job_id = data.get('job_id')
    if job_id:
        leave_room(f'job_{job_id}')
        emit('unsubscribed', {'job_id': job_id, 'status': 'unsubscribed'})


@socketio.on('confirm_upload')
def handle_confirm_upload(data):
    if not _socketio_authenticated():
        return False
    upload_id = data.get('upload_id')
    selected_image_index = data.get('selected_image_index')
    if not upload_id:
        return
    user_id, is_admin = _scope()
    upload = get_pending_upload(upload_id, user_id=user_id, is_admin=is_admin)
    if not upload:
        emit('error', {'message': 'Pending upload not found'})
        return
    get_job_manager().confirm_approval(upload_id, selected_image_index)


@socketio.on('cancel_upload')
def handle_cancel_upload(data):
    if not _socketio_authenticated():
        return False
    upload_id = data.get('upload_id')
    if not upload_id:
        return
    user_id, is_admin = _scope()
    upload = get_pending_upload(upload_id, user_id=user_id, is_admin=is_admin)
    if not upload:
        emit('error', {'message': 'Pending upload not found'})
        return
    get_job_manager().reject_approval(upload_id)


# Register pipeline handler and restore queued jobs from DB
job_manager.set_process_func(process_video_job)
job_manager.set_resume_func(resume_upload_job)


def _reemit_parked_previews() -> None:
    """After a restart, re-emit previews for jobs parked in awaiting_approval."""
    jm = get_job_manager()
    try:
        for upload in jm.get_approvals():
            _emit_preview_payload(_build_preview_payload(upload))
    except Exception as exc:
        print(f'[Jobs] failed to re-emit parked previews: {exc}')


_reemit_parked_previews()


# ===== SPA Catch-All Route =====
# Must be registered AFTER every explicit route so those keep precedence.

@app.route('/<path:path>')
def spa_catch_all(path):
    if path.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    full_path = os.path.join(FRONTEND_DIST, path)
    if os.path.isfile(full_path):
        return send_from_directory(FRONTEND_DIST, path)
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return jsonify({'error': 'Not found'}), 404


if __name__ == '__main__':
    load_dotenv()
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5006'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
    socketio.run(app, debug=debug, host=host, port=port, allow_unsafe_werkzeug=True, use_reloader=debug)
