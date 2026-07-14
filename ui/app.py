"""
Pick-a-Recipe Web UI
A Flask-based web interface for video recipe extraction with authentication and configuration management.
Supports parallel job processing with progress persistence.
"""

import os
import sys
import base64
import secrets
import threading
import json
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response
from flask_socketio import SocketIO, emit, join_room, leave_room

from database import (
    init_db, load_config, save_config,
    verify_user, update_password, hash_password,
    get_user, get_user_by_email, get_user_by_google_id,
    create_user, list_users, delete_user,
    user_must_change_password, clear_must_change_password,
    link_google_account,
    get_history, get_history_entry, get_history_count, delete_history_entry,
    delete_history_entries_bulk, delete_job_entry, delete_jobs_bulk,
    get_combined_history_and_jobs, get_combined_history_and_jobs_count,
    get_job, get_active_jobs,
    create_pending_upload, get_pending_upload, get_pending_uploads,
    confirm_pending_upload, cancel_pending_upload, delete_pending_upload,
    cleanup_expired_pending_uploads, cleanup_old_jobs,
    save_push_subscription, get_push_subscriptions, delete_push_subscription,
)
from job_manager import init_job_manager, get_job_manager, resolve_max_concurrent

app = Flask(__name__)

# Serve manifest.json with correct MIME type and headers for PWA
@app.route('/manifest.json')
def serve_manifest():
    response = make_response(app.send_static_file('manifest.json'))
    response.headers['Content-Type'] = 'application/manifest+json'
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

# Serve service worker with correct MIME type and scope
@app.route('/sw.js')
def serve_sw():
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

# OAuth (Google SSO) — optional, enabled when env vars are set
oauth = None
if os.environ.get('GOOGLE_CLIENT_ID') and os.environ.get('GOOGLE_CLIENT_SECRET'):
    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)
    oauth.register(
        name='google',
        client_id=os.environ['GOOGLE_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

# Store pending recipe uploads waiting for confirmation
pending_uploads = {}


def _is_logged_in() -> bool:
    return 'user' in session


def _maybe_disable_registration_after_setup(username: str) -> None:
    """After first real login (non-default password), suggest locking registrations."""
    cfg = load_config()
    if cfg.get('allow_registration', 'true') == 'true':
        user = get_user(username)
        if user and not user.get('must_change_password'):
            # Leave enabled but admin can disable in settings
            pass


def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return redirect(url_for('login'))
        if user_must_change_password(session['user']):
            if request.endpoint not in ('setup_password', 'logout', 'static'):
                return redirect(url_for('setup_password'))
        return f(*args, **kwargs)
    return decorated_function


def api_login_required(f):
    """Decorator to require login for API routes (returns JSON error)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return jsonify({'error': 'Authentication required'}), 401
        if user_must_change_password(session['user']):
            return jsonify({'error': 'Password change required', 'redirect': '/setup-password'}), 403
        return f(*args, **kwargs)
    return decorated_function


def _socketio_authenticated() -> bool:
    return _is_logged_in()


def _start_job_for_url(url: str, *, retry_from_history_id: int | None = None, priority: int = 0) -> dict:
    jm = get_job_manager()
    job_id = jm.create_new_job(url, retry_from_history_id=retry_from_history_id, priority=priority)
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
            except Exception as exc:
                print(f"[Cleanup] error: {exc}")

    t = threading.Thread(target=loop, daemon=True)
    t.start()


_run_cleanup_scheduler()


@app.route('/')
@login_required
def index():
    """Main page with URL input and progress display."""
    # Check for shared URL from multiple sources:
    # 1. Session (set by /share route for POST requests)
    # 2. shared_url query param (from service worker redirect)
    # 3. shared_text query param (from service worker redirect)
    # 4. url/text query params (legacy/direct)
    shared_url = (
        session.pop('shared_url', None) or
        request.args.get('shared_url') or
        request.args.get('shared_text') or
        request.args.get('url') or
        request.args.get('text') or
        ''
    )
    auto_from_share = session.pop('auto_start_extraction', False)
    
    # Extract URL from shared text if needed (apps like TikTok share URLs in text)
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
    """Dedicated progress page for a single job."""
    job = get_job(job_id)
    if not job:
        flash('Job not found', 'error')
        return redirect(url_for('index'))
    return render_template('job.html', job=job, max_concurrent=resolve_max_concurrent())


@app.route('/history')
@login_required
def history():
    """History page showing all past recipe extractions."""
    return render_template('history.html')


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
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # User is logged in, redirect to main page
    return redirect(url_for('index'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page."""
    if _is_logged_in():
        if user_must_change_password(session['user']):
            return redirect(url_for('setup_password'))
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember_me = request.form.get('remember_me') == 'on'

        if verify_user(username, password):
            session['user'] = username
            if remember_me:
                session.permanent = True
            if user_must_change_password(username):
                flash('Please set a new password before continuing.', 'warning')
                return redirect(url_for('setup_password'))
            _maybe_disable_registration_after_setup(username)
            flash('Login successful!', 'success')
            return redirect(url_for('index'))
        flash('Invalid username or password', 'error')

    google_enabled = oauth is not None
    return render_template('login.html', google_enabled=google_enabled)


@app.route('/setup-password', methods=['GET', 'POST'])
def setup_password():
    """Force password change for default or flagged accounts."""
    if not _is_logged_in():
        return redirect(url_for('login'))

    username = session['user']
    user = get_user(username)
    if not user:
        return redirect(url_for('logout'))

    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        current_password = request.form.get('current_password', '')

        if user.get('auth_provider') == 'google' and not user.get('password_hash'):
            if new_password != confirm_password:
                flash('Passwords do not match', 'error')
            elif len(new_password) < 8:
                flash('Password must be at least 8 characters', 'error')
            else:
                update_password(username, new_password)
                clear_must_change_password(username)
                flash('Password set successfully!', 'success')
                return redirect(url_for('index'))
        elif not verify_user(username, current_password) and user_must_change_password(username):
            # Default admin first login — current password optional if still default
            if username == 'admin' and current_password in ('', 'admin123'):
                pass
            elif not verify_user(username, current_password):
                flash('Current password is incorrect', 'error')
                return render_template('setup_password.html', user=user)

        if new_password != confirm_password:
            flash('Passwords do not match', 'error')
        elif len(new_password) < 8:
            flash('Password must be at least 8 characters', 'error')
        else:
            update_password(username, new_password)
            clear_must_change_password(username)
            cfg = load_config()
            if cfg.get('allow_registration', 'true') == 'true':
                save_config({**cfg, 'allow_registration': 'false'})
            flash('Password updated! New registrations are now disabled.', 'success')
            return redirect(url_for('index'))

    return render_template('setup_password.html', user=user)


@app.route('/auth/google')
def auth_google():
    if oauth is None:
        flash('Google sign-in is not configured', 'error')
        return redirect(url_for('login'))
    redirect_uri = url_for('auth_google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route('/auth/google/callback')
def auth_google_callback():
    if oauth is None:
        return redirect(url_for('login'))

    try:
        token = oauth.google.authorize_access_token()
        userinfo = token.get('userinfo') or oauth.google.parse_id_token(token)
    except Exception:
        flash('Google sign-in failed', 'error')
        return redirect(url_for('login'))

    email = userinfo.get('email')
    google_id = userinfo.get('sub')
    name = userinfo.get('name') or email.split('@')[0] if email else 'user'
    avatar = userinfo.get('picture')

    user = get_user_by_google_id(google_id) or (get_user_by_email(email) if email else None)
    cfg = load_config()

    if not user:
        if cfg.get('allow_registration', 'true') != 'true':
            flash('Registration is disabled. Contact the administrator.', 'error')
            return redirect(url_for('login'))
        username = name.replace(' ', '_').lower()[:32]
        base = username
        n = 1
        while get_user(username):
            username = f"{base}{n}"
            n += 1
        create_user(
            username,
            password=None,
            email=email,
            google_id=google_id,
            auth_provider='google',
            avatar_url=avatar,
        )
        user = get_user(username)
    else:
        link_google_account(user['username'], google_id, email or user.get('email', ''), avatar)

    session['user'] = user['username']
    session.permanent = True
    if user_must_change_password(user['username']):
        return redirect(url_for('setup_password'))
    flash('Signed in with Google!', 'success')
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    """Logout and clear session."""
    session.pop('user', None)
    flash('You have been logged out.', 'info')
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
        config['output_target'] = request.form.get('output_target', 'tandoor')
        config['export_to_both'] = 'true' if request.form.get('export_to_both') else 'false'
        config['whisper_model'] = request.form.get('whisper_model', 'small')
        config['hf_token'] = request.form.get('hf_token', '')
        config['yt_dlp_cookies_file'] = request.form.get('yt_dlp_cookies_file', '')
        config['yt_dlp_cookies_browser'] = request.form.get('yt_dlp_cookies_browser', '')
        # Checkbox: present in form data only when checked
        config['confirm_before_upload'] = 'true' if request.form.get(
            'confirm_before_upload') else 'false'
        config['max_concurrent_jobs'] = request.form.get('max_concurrent_jobs', '3')
        config['allow_registration'] = 'true' if request.form.get('allow_registration') else 'false'

        save_config(config)
        get_job_manager().refresh_concurrency()
        flash('Settings saved successfully!', 'success')
        return redirect(url_for('settings'))

    users = list_users() if (get_user(session.get('user')) or {}).get('is_admin') else []
    return render_template(
        'settings.html',
        config=config,
        users=users,
        google_oauth_configured=oauth is not None,
        max_concurrent=resolve_max_concurrent(),
    )


@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """Change user password."""
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    username = session['user']

    if not verify_user(username, current_password):
        flash('Current password is incorrect', 'error')
    elif new_password != confirm_password:
        flash('New passwords do not match', 'error')
    elif len(new_password) < 6:
        flash('Password must be at least 6 characters', 'error')
    else:
        update_password(username, new_password)
        clear_must_change_password(username)
        flash('Password changed successfully!', 'success')

    return redirect(url_for('settings'))


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
    """List all active jobs."""
    jobs = get_active_jobs()
    return jsonify({'jobs': jobs})


@app.route('/api/jobs/<job_id>', methods=['GET'])
@api_login_required
def get_job_status(job_id):
    """Get status of a specific job."""
    job = get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
@api_login_required
def cancel_job_api(job_id):
    """Cancel a running job."""
    jm = get_job_manager()
    result = jm.cancel_job(job_id)
    if result:
        return jsonify({'status': 'cancelled', 'job_id': job_id})
    return jsonify({'error': 'Job not found or already completed'}), 404


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
        deleted_jobs = delete_jobs_bulk(job_ids)
    
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
    result = delete_job_entry(job_id)
    if result:
        return jsonify({'status': 'deleted', 'job_id': job_id})
    return jsonify({'error': 'Job not found'}), 404


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
    image_path = item.get('thumbnail_path')
    
    # Get target from request or use default
    data = request.get_json() or {}
    target = data.get('target', config.OUTPUT_TARGET)
    
    try:
        config.reload()
        
        if target == 'tandoor':
            from tandoor import Tandoor
            tandoor = Tandoor()
            result = tandoor.create_recipe(recipe_data)
            if image_path and os.path.exists(image_path) and result.get("id"):
                tandoor.upload_image(result["id"], image_path)
        elif target == 'mealie':
            from mealie import Mealie
            mealie = Mealie()
            result = mealie.create_recipe(recipe_data)
            recipe_slug = result.get("slug") or result.get("id")
            if image_path and os.path.exists(image_path) and recipe_slug:
                mealie.upload_image(recipe_slug, image_path)
        else:
            return jsonify({'error': f'Unknown target: {target}'}), 400
        
        return jsonify({
            'status': 'success',
            'message': f'Recipe re-uploaded to {target}',
            'target': target
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
    """Get all pending recipe uploads waiting for confirmation.
    
    This allows any device/session to see pending uploads and confirm/cancel them.
    """
    # Clean up expired uploads first
    cleanup_expired_pending_uploads()
    
    pending = get_pending_uploads()
    
    # Prepare response with image data for each pending upload
    results = []
    for upload in pending:
        item = {
            'upload_id': upload['id'],
            'job_id': upload['job_id'],
            'recipe': upload['recipe_data'],
            'output_target': upload['output_target'],
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
        image_candidates = upload.get('image_candidates', [])
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
    upload = get_pending_upload(upload_id)
    if not upload or upload['status'] != 'pending':
        return jsonify({'error': 'Pending upload not found'}), 404
    
    item = {
        'upload_id': upload['id'],
        'job_id': upload['job_id'],
        'recipe': upload['recipe_data'],
        'output_target': upload['output_target'],
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
    image_candidates = upload.get('image_candidates', [])
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
    
    return jsonify(item)


@app.route('/api/pending-uploads/<upload_id>/confirm', methods=['POST'])
@api_login_required
def confirm_pending_upload_api(upload_id):
    """Confirm a pending upload via REST API (works from any device/session)."""
    data = request.get_json() or {}
    selected_image_index = data.get('selected_image_index')
    
    # Update database
    result = confirm_pending_upload(upload_id, selected_image_index)
    if not result:
        return jsonify({'error': 'Pending upload not found or already processed'}), 404
    
    # Also trigger the in-memory event if it exists (for the waiting thread)
    if upload_id in pending_uploads:
        pending_uploads[upload_id]['confirmed'] = True
        if selected_image_index is not None:
            pending_uploads[upload_id]['selected_image_index'] = selected_image_index
        pending_uploads[upload_id]['event'].set()
    
    return jsonify({'status': 'confirmed', 'upload_id': upload_id})


@app.route('/api/pending-uploads/<upload_id>/cancel', methods=['POST'])
@api_login_required
def cancel_pending_upload_api(upload_id):
    """Cancel a pending upload via REST API (works from any device/session)."""
    # Update database
    result = cancel_pending_upload(upload_id)
    if not result:
        return jsonify({'error': 'Pending upload not found or already processed'}), 404
    
    # Also trigger the in-memory event if it exists (for the waiting thread)
    if upload_id in pending_uploads:
        pending_uploads[upload_id]['confirmed'] = False
        pending_uploads[upload_id]['event'].set()
    
    return jsonify({'status': 'cancelled', 'upload_id': upload_id})


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


@app.route('/api/users/<username>', methods=['DELETE'])
@api_login_required
def delete_user_api(username):
    me = get_user(session['user'])
    if not me or not me.get('is_admin'):
        return jsonify({'error': 'Admin required'}), 403
    if username == session['user']:
        return jsonify({'error': 'Cannot delete yourself'}), 400
    if delete_user(username):
        return jsonify({'status': 'deleted'})
    return jsonify({'error': 'User not found'}), 404


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


def process_video_job(job_id, jm):
    """Background task — delegates to shared pipeline module."""
    from pipeline import (
        PipelineStats,
        PreviewWaiter,
        run_extraction_pipeline,
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
            socketio.emit('recipe_preview', payload, room=f'job_{job_id}')
            socketio.emit('recipe_preview', payload)

        def emit_cancelled():
            socketio.emit('recipe_cancelled', {
                'job_id': job_id,
                'message': 'Recipe upload was cancelled',
            }, room=f'job_{job_id}')
            socketio.emit('recipe_cancelled', {
                'job_id': job_id,
                'message': 'Recipe upload was cancelled',
            })

        preview = PreviewWaiter(
            job_id=job_id,
            recipe_data={},
            image_path=None,
            image_candidates=[],
            best_image_index=0,
            output_target=app_config.OUTPUT_TARGET,
            export_to_both=app_config.EXPORT_TO_BOTH,
            emit_preview=emit_preview,
            wait_for_confirmation=lambda *a, **k: (False, 0),
            pending_uploads=pending_uploads,
            create_pending_upload_fn=create_pending_upload,
            get_pending_upload_fn=get_pending_upload,
            delete_pending_upload_fn=delete_pending_upload,
            is_cancelled=lambda: jm.is_cancelled(job_id),
            socketio_emit_cancelled=emit_cancelled,
        )

    result = run_extraction_pipeline(job['url'], reporter, stats=stats, preview=preview)

    if result.error == 'cancelled':
        return
    if result.error:
        if 'confirmation timed out' in (result.error or '').lower():
            jm.fail_job(job_id, 'Upload confirmation timed out', stats.llm_tokens_estimate)
        elif 'cancelled' in (result.error or '').lower():
            jm.update_progress(job_id, 'cancelled', 'Upload cancelled by user', 100)
        else:
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
    emit('connected', {'status': 'Connected to server'})


@socketio.on('subscribe_job')
def handle_subscribe_job(data):
    if not _socketio_authenticated():
        return False
    job_id = data.get('job_id')
    if job_id:
        job = get_job(job_id)
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
    if upload_id and upload_id in pending_uploads:
        pending_uploads[upload_id]['confirmed'] = True
        # Store the user's selected image index if provided
        if selected_image_index is not None:
            pending_uploads[upload_id]['selected_image_index'] = selected_image_index
        pending_uploads[upload_id]['event'].set()


@socketio.on('cancel_upload')
def handle_cancel_upload(data):
    if not _socketio_authenticated():
        return False
    upload_id = data.get('upload_id')
    if upload_id and upload_id in pending_uploads:
        pending_uploads[upload_id]['confirmed'] = False
        pending_uploads[upload_id]['event'].set()


# Register pipeline handler and restore queued jobs from DB
job_manager.set_process_func(process_video_job)


if __name__ == '__main__':
    load_dotenv()
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5006'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
    socketio.run(app, debug=debug, host=host, port=port, allow_unsafe_werkzeug=True)
