"""Run a snippet against a freshly imported app, in its own interpreter.

AUTH_MODE is read once when `app` is imported, and it decides whether the OIDC
client gets registered, so one interpreter can only ever host one mode. Tests
that need a mode other than the one already imported have to fork.

Also keeps these tests from dictating import order for anything else that
imports the Flask app: whichever test module imports it first would otherwise
fix the mode for the whole run.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cleared so a developer's real SSO configuration cannot change what a test
# asserts, and so each run starts from the documented defaults.
_STRIPPED = (
    'AUTH_MODE', 'AUTH_LOCAL_USERNAME',
    'AUTHENTIK_CLIENT_ID', 'AUTHENTIK_CLIENT_SECRET',
)


def run(script: str, **env_overrides) -> subprocess.CompletedProcess:
    """Run `script` against a fresh app import and an isolated DATA_DIR.

    The script gets an `emit(**kwargs)` helper; call it once with everything the
    assertions need, since only the parent process can assert.
    """
    env = os.environ.copy()
    env['DATA_DIR'] = tempfile.mkdtemp()
    for key in _STRIPPED:
        env.pop(key, None)
    env.update(env_overrides)

    preamble = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {ROOT!r})
        sys.path.insert(0, {os.path.join(ROOT, 'ui')!r})

        def emit(**kw):
            print('__RESULT__' + json.dumps(kw))
    """)
    return subprocess.run(
        [sys.executable, '-c', preamble + textwrap.dedent(script)],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=180,
    )


def result(proc: subprocess.CompletedProcess) -> dict:
    """Parse what the script emitted, or fail with its full output."""
    for line in proc.stdout.splitlines():
        if line.startswith('__RESULT__'):
            return json.loads(line[len('__RESULT__'):])
    raise AssertionError(
        f'no result emitted (exit={proc.returncode})\n'
        f'--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}'
    )
