"""Kitchen tables and their migrations.

Unlike ui/database.py, which re-applies idempotent ALTERs on every boot, the
kitchen schema is versioned: each migration runs once, in order, and the
applied version is recorded in ``kitchen_schema_version``. Add a migration by
appending to ``MIGRATIONS``; never edit one that has shipped.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from config import DATA_DIR, DB_FILE
from helpers import setup_logger

logger = setup_logger(__name__)

IMAGES_DIR = os.path.join(DATA_DIR, "images")

_init_lock = threading.Lock()
_initialized_for: str | None = None


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
    finally:
        conn.close()


_V1 = """
        CREATE TABLE recipes (
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            servings REAL,
            yield_text TEXT NOT NULL DEFAULT '',
            prep_time TEXT NOT NULL DEFAULT '',
            cook_time TEXT NOT NULL DEFAULT '',
            total_time TEXT NOT NULL DEFAULT '',
            total_minutes INTEGER,
            source_url TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT 'manual',
            language TEXT NOT NULL DEFAULT '',
            cuisine TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            history_id INTEGER UNIQUE,
            schema_json TEXT NOT NULL DEFAULT '{}',
            created_by TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX idx_recipes_source_url ON recipes(source_url);
        CREATE INDEX idx_recipes_name ON recipes(name COLLATE NOCASE);

        CREATE TABLE recipe_images (
            id TEXT PRIMARY KEY,
            recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX idx_recipe_images_recipe ON recipe_images(recipe_id, position);

        CREATE TABLE recipe_ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            section TEXT NOT NULL DEFAULT '',
            food TEXT NOT NULL,
            quantity TEXT NOT NULL DEFAULT '',
            quantity_value REAL,
            quantity_max REAL,
            unit TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            raw TEXT NOT NULL DEFAULT '',
            optional INTEGER NOT NULL DEFAULT 0,
            scalable INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX idx_recipe_ingredients_recipe ON recipe_ingredients(recipe_id, position);
        CREATE INDEX idx_recipe_ingredients_food ON recipe_ingredients(food COLLATE NOCASE);

        CREATE TABLE recipe_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            section TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL,
            timers TEXT NOT NULL DEFAULT '[]',
            ingredient_refs TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX idx_recipe_steps_recipe ON recipe_steps(recipe_id, position);

        CREATE TABLE recipe_nutrition (
            recipe_id TEXT PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
            per_serving TEXT NOT NULL,
            total TEXT NOT NULL,
            matched INTEGER NOT NULL DEFAULT 0,
            usable INTEGER NOT NULL DEFAULT 0,
            servings REAL,
            source TEXT NOT NULL DEFAULT 'auto',
            breakdown TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE TABLE tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE
        );
        CREATE TABLE recipe_tags (
            recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (recipe_id, tag_id)
        );

        CREATE TABLE collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_by TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE TABLE collection_recipes (
            collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (collection_id, recipe_id)
        );

        CREATE VIRTUAL TABLE recipes_fts USING fts5(
            recipe_id UNINDEXED, name, description, ingredients, tags,
            tokenize = 'unicode61 remove_diacritics 2'
        );

        CREATE TABLE user_recipe_meta (
            username TEXT NOT NULL,
            recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            favorite INTEGER NOT NULL DEFAULT 0,
            rating INTEGER,
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            PRIMARY KEY (username, recipe_id)
        );

        CREATE TABLE cook_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            cooked_on TEXT NOT NULL,
            servings REAL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX idx_cook_log_recipe ON cook_log(recipe_id, cooked_on);
        CREATE INDEX idx_cook_log_user ON cook_log(username, cooked_on);

        CREATE TABLE meal_plan_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_date TEXT NOT NULL,
            slot TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            recipe_id TEXT REFERENCES recipes(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT '',
            servings REAL,
            leftover_of INTEGER REFERENCES meal_plan_entries(id) ON DELETE SET NULL,
            note TEXT NOT NULL DEFAULT '',
            created_by TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX idx_meal_plan_date ON meal_plan_entries(plan_date, slot, position);

        CREATE TABLE shopping_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE TABLE shopping_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
            food TEXT NOT NULL,
            quantity_value REAL,
            unit TEXT NOT NULL DEFAULT '',
            aisle TEXT NOT NULL DEFAULT 'other',
            checked INTEGER NOT NULL DEFAULT 0,
            excluded INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            source_recipes TEXT NOT NULL DEFAULT '[]',
            manual INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX idx_shopping_items_list ON shopping_items(list_id, aisle, position);

        CREATE TABLE pantry_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            food TEXT NOT NULL,
            quantity_value REAL,
            unit TEXT NOT NULL DEFAULT '',
            staple INTEGER NOT NULL DEFAULT 0,
            expires_on TEXT,
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX idx_pantry_food ON pantry_items(food COLLATE NOCASE);

        CREATE TABLE aisle_overrides (
            food_key TEXT PRIMARY KEY,
            aisle TEXT NOT NULL
        );

        CREATE TABLE ics_tokens (
            username TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE TABLE share_links (
            token_hash TEXT PRIMARY KEY,
            recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            created_by TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX idx_share_links_recipe ON share_links(recipe_id);

        CREATE TABLE kitchen_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
"""

# Each entry is one SQL script. executescript() commits whatever is open
# before it starts, so the transaction has to live inside the script itself.
MIGRATIONS: list[tuple[int, str]] = [
    (1, _V1),
]


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kitchen_schema_version ("
        " version INTEGER PRIMARY KEY,"
        " applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))"
    )
    row = conn.execute("SELECT MAX(version) FROM kitchen_schema_version").fetchone()
    return int(row[0] or 0)


def migrate() -> int:
    """Apply pending migrations. Safe to call from several threads and boots."""
    global _initialized_for
    with _init_lock:
        if _initialized_for == DB_FILE:
            return len(MIGRATIONS)
        os.makedirs(IMAGES_DIR, exist_ok=True)
        with connect() as conn:
            version = current_version(conn)
            conn.commit()
            for target, script in MIGRATIONS:
                if target <= version:
                    continue
                logger.info("[Kitchen] applying schema migration %d", target)
                try:
                    conn.executescript(
                        "BEGIN;\n" + script
                        + f"\nINSERT INTO kitchen_schema_version(version) VALUES ({int(target)});"
                        + "\nCOMMIT;"
                    )
                except Exception:
                    if conn.in_transaction:
                        conn.rollback()
                    raise
                version = target
        _initialized_for = DB_FILE
        return version


def get_meta(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM kitchen_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO kitchen_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
