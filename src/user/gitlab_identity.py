"""GitLab identity lookup is scoped to canonical server URL, never username."""
import hashlib
import secrets

from src.session_manager import db, db_lock
from src.user import db as user_db

DDL = """
CREATE TABLE IF NOT EXISTS gitlab_identities (
    identity_key VARCHAR(64) PRIMARY KEY,
    user_id INTEGER NOT NULL,
    base_url TEXT NOT NULL,
    gitlab_user_id TEXT NOT NULL
)
"""


def resolve_user(base_url: str, profile: dict) -> dict:
    remote_id = str(profile['id'])
    key = hashlib.sha256(f'{base_url}\n{remote_id}'.encode()).hexdigest()
    # DB uniqueness also protects multiple server processes. A racing creator
    # must retry through the established mapping; never bind by local username.
    with db_lock:
        row = db.execute('SELECT user_id FROM gitlab_identities WHERE identity_key=?', (key,)).fetchone()
        if row:
            user = user_db.get_user(row[0])
            if not user:
                raise RuntimeError('GitLab identity references a missing user')
            return user
        user = user_db.create_user('gl_' + secrets.token_hex(12), secrets.token_urlsafe(32), 0)
        if not user:
            raise RuntimeError('Could not create GitLab user')
        try:
            db.execute('INSERT INTO gitlab_identities (identity_key,user_id,base_url,gitlab_user_id) VALUES (?,?,?,?)',
                       (key, user['id'], base_url, remote_id))
            db.commit()
        except Exception:
            db.rollback()
            row = db.execute('SELECT user_id FROM gitlab_identities WHERE identity_key=?', (key,)).fetchone()
            if not row:
                raise
            user_db.delete_user(user['id'])
            return user_db.get_user(row[0])
    return user
