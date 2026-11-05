"""Upgrading a data volume left behind by the pre-rename app.

Each test forks via app_harness: `config` seeds the canonical database at
import time from whatever sits in DATA_DIR, so the files have to exist before
that first import.
"""

import os
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_harness import result, run as _run  # noqa: E402


def run(script: str):
    # Dedented separately: the prelude and each test body are indented
    # differently, and dedent only strips the indent common to everything.
    prelude, _, body = script.partition('#BODY\n')
    return _run(textwrap.dedent(prelude) + textwrap.dedent(body))

_LEGACY_SCHEMA = """
    import os, sqlite3

    def make_legacy(path, history_rows):
        conn = sqlite3.connect(path)
        conn.executescript('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            , oidc_email TEXT, display_name TEXT);
            CREATE TABLE recipe_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT,
                url TEXT NOT NULL, status TEXT NOT NULL);
        ''')
        conn.execute(
            "INSERT INTO users (username, password_hash, oidc_email, display_name)"
            " VALUES ('admin', 'deadbeef', 'a@example.com', 'Admin')")
        conn.executemany(
            "INSERT INTO recipe_history (url, status) VALUES (?, 'success')",
            [(f'https://example.com/{i}',) for i in range(history_rows)])
        conn.commit()
        conn.close()

    data_dir = os.environ['DATA_DIR']
#BODY
"""


class TestLegacySeeding(unittest.TestCase):

    def test_live_database_is_never_replaced_by_a_richer_legacy_file(self):
        proc = run(_LEGACY_SCHEMA + """
            make_legacy(os.path.join(data_dir, 'pick_a_recipe.db'), 50)
            live = sqlite3.connect(os.path.join(data_dir, 'pick-a-recipe.db'))
            live.execute('CREATE TABLE marker (x)')
            live.commit()
            live.close()

            import config
            conn = sqlite3.connect(config.DB_FILE)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            emit(seeded=config.migrate_legacy_database(), tables=sorted(tables))
        """)
        out = result(proc)
        self.assertFalse(out['seeded'])
        self.assertEqual(out['tables'], ['marker'])

    def test_missing_live_database_is_seeded_from_legacy(self):
        proc = run(_LEGACY_SCHEMA + """
            make_legacy(os.path.join(data_dir, 'pick_a_recipe.db'), 3)

            import config
            conn = sqlite3.connect(config.DB_FILE)
            emit(rows=conn.execute('SELECT COUNT(*) FROM recipe_history').fetchone()[0])
        """)
        self.assertEqual(result(proc)['rows'], 3)


class TestLegacyUsersTable(unittest.TestCase):

    def test_users_table_is_rebuilt_to_the_current_shape(self):
        proc = run(_LEGACY_SCHEMA + """
            make_legacy(os.path.join(data_dir, 'pick-a-recipe.db'), 0)

            import database
            database.init_db()
            database.init_db()  # a second boot must leave it alone
            user = database.get_user('admin')
            conn = sqlite3.connect(database.DB_FILE)
            notnull = {r[1]: r[3] for r in conn.execute('PRAGMA table_info(users)')}
            database.ensure_local_user('second', is_admin=False)
            emit(
                user={k: user[k] for k in ('id', 'is_admin', 'password_hash', 'email', 'name')},
                password_notnull=notnull['password_hash'],
                columns=sorted(notnull),
                second=database.get_user('second') is not None,
            )
        """)
        out = result(proc)
        self.assertEqual(out['user'], {
            'id': 1, 'is_admin': 1, 'password_hash': None,
            'email': 'a@example.com', 'name': 'Admin',
        })
        self.assertEqual(out['password_notnull'], 0)
        self.assertIn('oidc_sub', out['columns'])
        self.assertTrue(out['second'])

    def test_argon2_password_survives_the_rebuild(self):
        proc = run(_LEGACY_SCHEMA + """
            path = os.path.join(data_dir, 'pick-a-recipe.db')
            make_legacy(path, 0)
            import passwords
            stored = passwords.hash_password('a properly long passphrase')
            conn = sqlite3.connect(path)
            conn.execute("UPDATE users SET password_hash = ?", (stored,))
            conn.commit()
            conn.close()

            import database
            database.init_db()
            emit(ok=passwords.verify(database.get_user('admin')['password_hash'],
                                     'a properly long passphrase'))
        """)
        self.assertTrue(result(proc)['ok'])


if __name__ == '__main__':
    unittest.main()
