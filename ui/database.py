"""
Database module for Pick-a-Recipe UI
Uses SQLite to store configuration, user data, jobs, and recipe history.
"""

import os
import json
import sqlite3
import hashlib
import uuid
import bcrypt
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

# Import defaults from config module to avoid duplication
from config import DEFAULT_CONFIG

# Database file path - use /app/data for Docker persistence, fallback to local
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
from config import DB_FILE


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize the database with required tables."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create config table (key-value store)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create recipe_jobs table for tracking active analysis jobs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recipe_jobs (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER DEFAULT 0,
                current_stage TEXT,
                stage_message TEXT,
                video_title TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create pending_uploads table for recipe confirmations
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_uploads (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                recipe_data TEXT NOT NULL,
                image_path TEXT,
                image_candidates TEXT,
                output_target TEXT,
                selected_image_index INTEGER DEFAULT 0,
                best_image_index INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES recipe_jobs(id)
            )
        ''')
        
        # Create recipe_history table for completed recipes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recipe_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                url TEXT NOT NULL,
                video_title TEXT,
                recipe_name TEXT,
                recipe_data TEXT,
                thumbnail_path TEXT,
                thumbnail_data TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                output_target TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES recipe_jobs(id)
            )
        ''')
        
        conn.commit()

        _migrate_schema(conn)

        # Create default admin user if no users exist
        cursor.execute('SELECT COUNT(*) FROM users')
        if cursor.fetchone()[0] == 0:
            create_user('admin', 'admin123', must_change_password=True, is_admin=True)
        
        # Initialize default config values if not exist
        cursor.execute('SELECT COUNT(*) FROM config')
        if cursor.fetchone()[0] == 0:
            for key, value in DEFAULT_CONFIG.items():
                set_config_value(key, value)

        # Migrate retired Gemini model ids in existing databases.
        # gemini-2.0-flash(-lite) were removed from the API and return 404,
        # so rewrite them to the current default.
        cursor.execute(
            "UPDATE config SET value = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE key = 'gemini_model' AND value IN ('gemini-2.0-flash', 'gemini-2.0-flash-lite')",
            (DEFAULT_CONFIG['gemini_model'],)
        )
        conn.commit()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations for existing databases."""
    cursor = conn.cursor()

    user_columns = {
        'email': 'TEXT',
        'google_id': 'TEXT',
        'auth_provider': "TEXT DEFAULT 'local'",
        'must_change_password': 'INTEGER DEFAULT 0',
        'is_admin': 'INTEGER DEFAULT 0',
        'avatar_url': 'TEXT',
    }
    for col, typedef in user_columns.items():
        try:
            cursor.execute(f'ALTER TABLE users ADD COLUMN {col} {typedef}')
        except sqlite3.OperationalError:
            pass

    job_columns = {
        'retry_from_history_id': 'INTEGER',
        'llm_tokens_used': 'INTEGER DEFAULT 0',
        'queue_priority': 'INTEGER DEFAULT 0',
    }
    for col, typedef in job_columns.items():
        try:
            cursor.execute(f'ALTER TABLE recipe_jobs ADD COLUMN {col} {typedef}')
        except sqlite3.OperationalError:
            pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()

    cursor.execute("UPDATE users SET is_admin = 1 WHERE username = 'admin' AND is_admin = 0")
    conn.commit()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def _verify_password_hash(stored_hash: str, password: str) -> bool:
    """Verify password against bcrypt or legacy SHA-256 hash."""
    if stored_hash.startswith('$2'):
        return bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
    return stored_hash == hashlib.sha256(password.encode()).hexdigest()


def _upgrade_password_hash(username: str, password: str) -> None:
    """Upgrade legacy SHA-256 hash to bcrypt on successful login."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET password_hash = ? WHERE username = ?',
            (hash_password(password), username),
        )
        conn.commit()


# ===== User Functions =====

def create_user(
    username: str,
    password: str | None = None,
    *,
    email: str | None = None,
    google_id: str | None = None,
    auth_provider: str = 'local',
    must_change_password: bool = False,
    is_admin: bool = False,
    avatar_url: str | None = None,
) -> bool:
    """Create a new user."""
    pw_hash = hash_password(password) if password else ''
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO users
                   (username, password_hash, email, google_id, auth_provider,
                    must_change_password, is_admin, avatar_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (username, pw_hash, email, google_id, auth_provider,
                 int(must_change_password), int(is_admin), avatar_url),
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False


def get_user(username: str) -> Optional[Dict[str, Any]]:
    """Get user record by username."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_google_id(google_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE google_id = ?', (google_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def list_users() -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, username, email, auth_provider, is_admin, created_at FROM users ORDER BY username'
        )
        return [dict(row) for row in cursor.fetchall()]


def delete_user(username: str) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM users WHERE username = ?', (username,))
        conn.commit()
        return cursor.rowcount > 0


def user_must_change_password(username: str) -> bool:
    user = get_user(username)
    return bool(user and user.get('must_change_password'))


def clear_must_change_password(username: str) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET must_change_password = 0 WHERE username = ?',
            (username,),
        )
        conn.commit()


def link_google_account(username: str, google_id: str, email: str, avatar_url: str | None = None) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''UPDATE users SET google_id = ?, email = ?, auth_provider = 'google',
               avatar_url = COALESCE(?, avatar_url) WHERE username = ?''',
            (google_id, email, avatar_url, username),
        )
        conn.commit()
        return cursor.rowcount > 0


def verify_user(username: str, password: str) -> bool:
    """Verify user credentials."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT password_hash FROM users WHERE username = ?',
            (username,)
        )
        row = cursor.fetchone()
        if row and row['password_hash'] and _verify_password_hash(row['password_hash'], password):
            if not row['password_hash'].startswith('$2'):
                _upgrade_password_hash(username, password)
            return True
        return False


def update_password(username: str, new_password: str) -> bool:
    """Update user password."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET password_hash = ? WHERE username = ?',
            (hash_password(new_password), username)
        )
        conn.commit()
        return cursor.rowcount > 0


# ===== Config Functions =====

def set_config_value(key: str, value: str) -> bool:
    """Set a single config value."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
        ''', (key, str(value), str(value)))
        conn.commit()
        return True


def load_config() -> dict:
    """Load all configuration values."""
    config = DEFAULT_CONFIG.copy()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT key, value FROM config')
        for row in cursor.fetchall():
            config[row['key']] = row['value']
    return config


def save_config(config: dict) -> bool:
    """Save all configuration values."""
    with get_db() as conn:
        cursor = conn.cursor()
        for key, value in config.items():
            cursor.execute('''
                INSERT INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
            ''', (key, str(value), str(value)))
        conn.commit()
        return True


# ===== Job Functions =====

def create_job(url: str, *, retry_from_history_id: int | None = None, priority: int = 0) -> str:
    """Create a new analysis job and return its ID."""
    job_id = str(uuid.uuid4())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO recipe_jobs
            (id, url, status, progress, current_stage, stage_message,
             retry_from_history_id, queue_priority)
            VALUES (?, ?, 'queued', 0, 'queued', 'Waiting in queue...', ?, ?)
        ''', (job_id, url, retry_from_history_id, priority))
        conn.commit()
    return job_id


def get_queue_position(job_id: str) -> int:
    """Return 1-based queue position (0 if processing or not queued)."""
    job = get_job(job_id)
    if not job or job.get('status') != 'queued':
        return 0
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM recipe_jobs q
            WHERE q.status = 'queued'
            AND (
                q.queue_priority > ?
                OR (q.queue_priority = ? AND q.rowid < (SELECT rowid FROM recipe_jobs WHERE id = ?))
            )
        ''', (job.get('queue_priority', 0), job.get('queue_priority', 0), job_id))
        ahead = cursor.fetchone()[0]
        return ahead + 1


def count_queued_jobs() -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM recipe_jobs WHERE status = 'queued'")
        return cursor.fetchone()[0]


def get_queued_jobs() -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM recipe_jobs WHERE status = 'queued'
            ORDER BY queue_priority DESC, created_at ASC
        ''')
        return [dict(row) for row in cursor.fetchall()]


def update_job_tokens(job_id: str, tokens: int) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE recipe_jobs SET llm_tokens_used = ? WHERE id = ?',
            (tokens, job_id),
        )
        conn.commit()


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get a job by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM recipe_jobs WHERE id = ?', (job_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def get_active_jobs() -> List[Dict[str, Any]]:
    """Get all active (non-completed, non-failed, non-cancelled) jobs."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM recipe_jobs
            WHERE status NOT IN ('completed', 'failed', 'cancelled')
            ORDER BY
                CASE WHEN status = 'queued' THEN 0 ELSE 1 END,
                queue_priority DESC,
                created_at ASC
        ''')
        jobs = [dict(row) for row in cursor.fetchall()]
        for job in jobs:
            if job.get('status') == 'queued':
                job['queue_position'] = get_queue_position(job['id'])
        return jobs


def get_all_jobs() -> List[Dict[str, Any]]:
    """Get all jobs."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM recipe_jobs ORDER BY created_at DESC')
        return [dict(row) for row in cursor.fetchall()]


def update_job_progress(job_id: str, status: str, progress: int,
                        current_stage: str, stage_message: str,
                        video_title: Optional[str] = None) -> bool:
    """Update job progress."""
    with get_db() as conn:
        cursor = conn.cursor()
        if video_title:
            cursor.execute('''
                UPDATE recipe_jobs
                SET status = ?, progress = ?, current_stage = ?, stage_message = ?,
                    video_title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, progress, current_stage, stage_message, video_title, job_id))
        else:
            cursor.execute('''
                UPDATE recipe_jobs
                SET status = ?, progress = ?, current_stage = ?, stage_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, progress, current_stage, stage_message, job_id))
        conn.commit()
        return cursor.rowcount > 0


def fail_job(job_id: str, error_message: str) -> bool:
    """Mark a job as failed."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE recipe_jobs
            SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (error_message, job_id))
        conn.commit()
        return cursor.rowcount > 0


def cancel_job(job_id: str) -> bool:
    """Mark a job as cancelled."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE recipe_jobs
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (job_id,))
        conn.commit()
        return cursor.rowcount > 0


def complete_job(job_id: str) -> bool:
    """Mark a job as completed."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE recipe_jobs
            SET status = 'completed', progress = 100, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (job_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_job(job_id: str) -> bool:
    """Delete a job record."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM recipe_jobs WHERE id = ?', (job_id,))
        conn.commit()
        return cursor.rowcount > 0


# ===== History Functions =====

def create_history_entry(job_id: str, url: str, video_title: Optional[str],
                         recipe_name: Optional[str], recipe_data: Optional[Dict],
                         thumbnail_path: Optional[str], thumbnail_data: Optional[str],
                         status: str, error_message: Optional[str] = None,
                         output_target: Optional[str] = None) -> Optional[int]:
    """Create a history entry for a completed/failed recipe extraction."""
    with get_db() as conn:
        cursor = conn.cursor()
        recipe_json = json.dumps(recipe_data) if recipe_data else None
        cursor.execute('''
            INSERT INTO recipe_history
            (job_id, url, video_title, recipe_name, recipe_data, thumbnail_path,
             thumbnail_data, status, error_message, output_target)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (job_id, url, video_title, recipe_name, recipe_json, thumbnail_path,
              thumbnail_data, status, error_message, output_target))
        conn.commit()
        return cursor.lastrowid


def get_history(limit: int = 50, offset: int = 0,
                status_filter: Optional[str] = None,
                search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get recipe history with optional filtering."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = 'SELECT * FROM recipe_history WHERE 1=1'
        params = []
        
        if status_filter:
            query += ' AND status = ?'
            params.append(status_filter)
        
        if search:
            query += ' AND (recipe_name LIKE ? OR video_title LIKE ? OR url LIKE ?)'
            search_pattern = f'%{search}%'
            params.extend([search_pattern, search_pattern, search_pattern])
        
        query += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            # Parse recipe_data JSON if present
            if item.get('recipe_data'):
                try:
                    item['recipe_data'] = json.loads(item['recipe_data'])
                except json.JSONDecodeError:
                    pass
            results.append(item)
        return results


def get_history_entry(history_id: int) -> Optional[Dict[str, Any]]:
    """Get a single history entry by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM recipe_history WHERE id = ?', (history_id,))
        row = cursor.fetchone()
        if row:
            item = dict(row)
            # Parse recipe_data JSON if present
            if item.get('recipe_data'):
                try:
                    item['recipe_data'] = json.loads(item['recipe_data'])
                except json.JSONDecodeError:
                    pass
            return item
    return None


def get_history_count(status_filter: Optional[str] = None,
                      search: Optional[str] = None) -> int:
    """Get total count of history entries with optional filtering."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = 'SELECT COUNT(*) FROM recipe_history WHERE 1=1'
        params = []
        
        if status_filter:
            query += ' AND status = ?'
            params.append(status_filter)
        
        if search:
            query += ' AND (recipe_name LIKE ? OR video_title LIKE ? OR url LIKE ?)'
            search_pattern = f'%{search}%'
            params.extend([search_pattern, search_pattern, search_pattern])
        
        cursor.execute(query, params)
        return cursor.fetchone()[0]


def delete_history_entry(history_id: int) -> bool:
    """Delete a history entry."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM recipe_history WHERE id = ?', (history_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_history_entries_bulk(history_ids: List[int]) -> int:
    """Delete multiple history entries. Returns count of deleted entries."""
    if not history_ids:
        return 0
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = ','.join(['?' for _ in history_ids])
        cursor.execute(f'DELETE FROM recipe_history WHERE id IN ({placeholders})', history_ids)
        conn.commit()
        return cursor.rowcount


def get_combined_history_and_jobs(limit: int = 50, offset: int = 0,
                                   status_filter: Optional[str] = None,
                                   search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get combined view of recipe history and active/cancelled jobs.
    
    This provides a unified view showing:
    - Completed/failed recipes from recipe_history
    - In-progress jobs from recipe_jobs
    - Cancelled jobs from recipe_jobs
    """
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Build query for recipe_history
        # Exclude failed entries if there's a successful entry for the same URL
        history_query = '''
            SELECT
                'history' as source_type,
                rh.id,
                rh.job_id,
                rh.url,
                rh.video_title,
                rh.recipe_name,
                rh.recipe_data,
                rh.thumbnail_path,
                rh.thumbnail_data,
                rh.status,
                rh.error_message,
                rh.output_target,
                rh.created_at,
                NULL as progress,
                NULL as current_stage,
                NULL as stage_message,
                rh.created_at as updated_at
            FROM recipe_history rh
            WHERE NOT (
                rh.status = 'failed'
                AND EXISTS (
                    SELECT 1 FROM recipe_history rh2
                    WHERE rh2.url = rh.url AND rh2.status = 'success'
                )
            )
        '''
        history_params = []
        
        # Build query for recipe_jobs (only active jobs that don't have history entries)
        # Exclude completed/failed jobs as they should have history entries
        jobs_query = '''
            SELECT
                'job' as source_type,
                NULL as id,
                rj.id as job_id,
                rj.url,
                rj.video_title,
                NULL as recipe_name,
                NULL as recipe_data,
                NULL as thumbnail_path,
                NULL as thumbnail_data,
                rj.status,
                rj.error_message,
                NULL as output_target,
                rj.created_at,
                rj.progress,
                rj.current_stage,
                rj.stage_message,
                rj.updated_at
            FROM recipe_jobs rj
            LEFT JOIN recipe_history rh ON rj.id = rh.job_id
            WHERE rh.id IS NULL AND rj.status NOT IN ('completed', 'failed')
        '''
        jobs_params = []
        
        # Apply status filter
        if status_filter:
            if status_filter == 'success':
                history_query += ' AND rh.status = ?'
                history_params.append('success')
                jobs_query += ' AND 1=0'  # No jobs can be "success" without history
            elif status_filter == 'failed':
                history_query += ' AND rh.status = ?'
                history_params.append('failed')
                jobs_query += ' AND rj.status = ?'
                jobs_params.append('failed')
            elif status_filter == 'cancelled':
                history_query += ' AND 1=0'  # No history entries for cancelled
                jobs_query += ' AND rj.status = ?'
                jobs_params.append('cancelled')
            elif status_filter == 'pending':
                history_query += ' AND 1=0'
                jobs_query += ' AND rj.status IN (?, ?)'
                jobs_params.extend(['pending', 'queued'])
            elif status_filter == 'processing':
                history_query += ' AND 1=0'  # No history entries for processing
                jobs_query += ' AND rj.status NOT IN (?, ?, ?, ?)'
                jobs_params.extend(['completed', 'failed', 'cancelled', 'pending'])
        
        # Apply search filter
        if search:
            search_pattern = f'%{search}%'
            history_query += ' AND (rh.recipe_name LIKE ? OR rh.video_title LIKE ? OR rh.url LIKE ?)'
            history_params.extend([search_pattern, search_pattern, search_pattern])
            jobs_query += ' AND (rj.video_title LIKE ? OR rj.url LIKE ?)'
            jobs_params.extend([search_pattern, search_pattern])
        
        # Combine queries with UNION ALL
        combined_query = f'''
            SELECT * FROM (
                {history_query}
                UNION ALL
                {jobs_query}
            ) combined
            ORDER BY
                CASE
                    WHEN status IN ('pending', 'processing', 'info', 'download', 'transcribe',
                                    'visual', 'image', 'evaluate', 'preview', 'upload') THEN 0
                    WHEN status = 'cancelled' THEN 1
                    ELSE 2
                END,
                updated_at DESC
            LIMIT ? OFFSET ?
        '''
        
        all_params = history_params + jobs_params + [limit, offset]
        cursor.execute(combined_query, all_params)
        
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            # Parse recipe_data JSON if present
            if item.get('recipe_data'):
                try:
                    item['recipe_data'] = json.loads(item['recipe_data'])
                except json.JSONDecodeError:
                    pass
            results.append(item)
        return results


def get_combined_history_and_jobs_count(status_filter: Optional[str] = None,
                                         search: Optional[str] = None) -> int:
    """Get total count of combined history and jobs with optional filtering."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Count from recipe_history
        # Exclude failed entries if there's a successful entry for the same URL
        history_query = '''
            SELECT COUNT(*) FROM recipe_history rh
            WHERE NOT (
                rh.status = 'failed'
                AND EXISTS (
                    SELECT 1 FROM recipe_history rh2
                    WHERE rh2.url = rh.url AND rh2.status = 'success'
                )
            )
        '''
        history_params = []
        
        # Count from recipe_jobs (only active jobs that don't have history entries)
        # Exclude completed/failed jobs as they should have history entries
        jobs_query = '''
            SELECT COUNT(*) FROM recipe_jobs rj
            LEFT JOIN recipe_history rh ON rj.id = rh.job_id
            WHERE rh.id IS NULL AND rj.status NOT IN ('completed', 'failed')
        '''
        jobs_params = []
        
        # Apply status filter
        if status_filter:
            if status_filter == 'success':
                history_query += ' AND rh.status = ?'
                history_params.append('success')
                jobs_query = 'SELECT 0'  # No jobs can be "success" without history
                jobs_params = []
            elif status_filter == 'failed':
                history_query += ' AND rh.status = ?'
                history_params.append('failed')
                jobs_query += ' AND rj.status = ?'
                jobs_params.append('failed')
            elif status_filter == 'cancelled':
                history_query = 'SELECT 0'  # No history entries for cancelled
                history_params = []
                jobs_query += ' AND rj.status = ?'
                jobs_params.append('cancelled')
            elif status_filter == 'pending':
                history_query = 'SELECT 0'
                history_params = []
                jobs_query += ' AND rj.status IN (?, ?)'
                jobs_params.extend(['pending', 'queued'])
            elif status_filter == 'processing':
                history_query = 'SELECT 0'  # No history entries for processing
                history_params = []
                jobs_query += ' AND rj.status NOT IN (?, ?, ?, ?)'
                jobs_params.extend(['completed', 'failed', 'cancelled', 'pending'])
        
        # Apply search filter
        if search:
            search_pattern = f'%{search}%'
            if 'SELECT 0' not in history_query:
                history_query += ' AND (rh.recipe_name LIKE ? OR rh.video_title LIKE ? OR rh.url LIKE ?)'
                history_params.extend([search_pattern, search_pattern, search_pattern])
            if 'SELECT 0' not in jobs_query:
                jobs_query += ' AND (rj.video_title LIKE ? OR rj.url LIKE ?)'
                jobs_params.extend([search_pattern, search_pattern])
        
        # Execute history count
        cursor.execute(history_query, history_params)
        history_count = cursor.fetchone()[0]
        
        # Execute jobs count
        cursor.execute(jobs_query, jobs_params)
        jobs_count = cursor.fetchone()[0]
        
        return history_count + jobs_count


def delete_job_entry(job_id: str) -> bool:
    """Delete a job entry."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM recipe_jobs WHERE id = ?', (job_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_jobs_bulk(job_ids: List[str]) -> int:
    """Delete multiple job entries. Returns count of deleted entries."""
    if not job_ids:
        return 0
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = ','.join(['?' for _ in job_ids])
        cursor.execute(f'DELETE FROM recipe_jobs WHERE id IN ({placeholders})', job_ids)
        conn.commit()
        return cursor.rowcount


def cleanup_old_jobs(hours: int = 24) -> int:
    """Clean up jobs older than specified hours that are completed/failed/cancelled."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM recipe_jobs
            WHERE status IN ('completed', 'failed', 'cancelled')
            AND updated_at < datetime('now', ? || ' hours')
        ''', (f'-{hours}',))
        conn.commit()
        return cursor.rowcount


# ===== Pending Upload Functions =====

def create_pending_upload(upload_id: str, job_id: str, recipe_data: Dict,
                          image_path: Optional[str], image_candidates: List[str],
                          output_target: str, best_image_index: int = 0,
                          timeout_minutes: int = 5) -> bool:
    """Create a pending upload waiting for confirmation."""
    with get_db() as conn:
        cursor = conn.cursor()
        recipe_json = json.dumps(recipe_data)
        candidates_json = json.dumps(image_candidates) if image_candidates else None
        cursor.execute('''
            INSERT INTO pending_uploads
            (id, job_id, recipe_data, image_path, image_candidates, output_target,
             selected_image_index, best_image_index, status, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                    datetime('now', '+' || ? || ' minutes'))
        ''', (upload_id, job_id, recipe_json, image_path, candidates_json,
              output_target, best_image_index, best_image_index, timeout_minutes))
        conn.commit()
        return True


def get_pending_upload(upload_id: str) -> Optional[Dict[str, Any]]:
    """Get a pending upload by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM pending_uploads WHERE id = ?', (upload_id,))
        row = cursor.fetchone()
        if row:
            item = dict(row)
            # Parse JSON fields
            if item.get('recipe_data'):
                try:
                    item['recipe_data'] = json.loads(item['recipe_data'])
                except json.JSONDecodeError:
                    pass
            if item.get('image_candidates'):
                try:
                    item['image_candidates'] = json.loads(item['image_candidates'])
                except json.JSONDecodeError:
                    item['image_candidates'] = []
            return item
    return None


def get_pending_uploads() -> List[Dict[str, Any]]:
    """Get all pending uploads that haven't expired."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT pu.*, rj.url, rj.video_title
            FROM pending_uploads pu
            LEFT JOIN recipe_jobs rj ON pu.job_id = rj.id
            WHERE pu.status = 'pending'
            AND (pu.expires_at IS NULL OR pu.expires_at > datetime('now'))
            ORDER BY pu.created_at DESC
        ''')
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            # Parse JSON fields
            if item.get('recipe_data'):
                try:
                    item['recipe_data'] = json.loads(item['recipe_data'])
                except json.JSONDecodeError:
                    pass
            if item.get('image_candidates'):
                try:
                    item['image_candidates'] = json.loads(item['image_candidates'])
                except json.JSONDecodeError:
                    item['image_candidates'] = []
            results.append(item)
        return results


def confirm_pending_upload(upload_id: str, selected_image_index: Optional[int] = None) -> bool:
    """Mark a pending upload as confirmed."""
    with get_db() as conn:
        cursor = conn.cursor()
        if selected_image_index is not None:
            cursor.execute('''
                UPDATE pending_uploads
                SET status = 'confirmed', selected_image_index = ?
                WHERE id = ? AND status = 'pending'
            ''', (selected_image_index, upload_id))
        else:
            cursor.execute('''
                UPDATE pending_uploads
                SET status = 'confirmed'
                WHERE id = ? AND status = 'pending'
            ''', (upload_id,))
        conn.commit()
        return cursor.rowcount > 0


def cancel_pending_upload(upload_id: str) -> bool:
    """Mark a pending upload as cancelled."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE pending_uploads
            SET status = 'cancelled'
            WHERE id = ? AND status = 'pending'
        ''', (upload_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_pending_upload(upload_id: str) -> bool:
    """Delete a pending upload record."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM pending_uploads WHERE id = ?', (upload_id,))
        conn.commit()
        return cursor.rowcount > 0


def cleanup_expired_pending_uploads() -> int:
    """Clean up expired pending uploads."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE pending_uploads
            SET status = 'expired'
            WHERE status = 'pending'
            AND expires_at IS NOT NULL
            AND expires_at < datetime('now')
        ''')
        conn.commit()
        return cursor.rowcount


def save_push_subscription(username: str, endpoint: str, p256dh: str, auth_key: str) -> bool:
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO push_subscriptions (username, endpoint, p256dh, auth)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    username = excluded.username,
                    p256dh = excluded.p256dh,
                    auth = excluded.auth
            ''', (username, endpoint, p256dh, auth_key))
            conn.commit()
            return True
    except sqlite3.Error:
        return False


def get_push_subscriptions(username: str) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE username = ?',
            (username,),
        )
        return [dict(row) for row in cursor.fetchall()]


def delete_push_subscription(endpoint: str) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM push_subscriptions WHERE endpoint = ?', (endpoint,))
        conn.commit()
        return cursor.rowcount > 0


# Initialize database on module import
init_db()
