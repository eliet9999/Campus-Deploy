import json
import secrets
import time
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from itsdangerous import URLSafeTimedSerializer, BadSignature

COOKIE = 'campus_session'
SESSION_SECONDS = 8 * 3600


def initialize_secrets(settings):
    settings.secrets.mkdir(parents=True, exist_ok=True)
    auth = settings.secrets / 'auth.json'
    if auth.exists():
        return False
    password = secrets.token_urlsafe(24)
    with auth.open('x', encoding='utf-8') as f:
        json.dump({'password_hash': PasswordHasher().hash(password), 'signing_key': secrets.token_urlsafe(48)}, f)
    with (settings.secrets / 'admin-password.txt').open('x', encoding='utf-8') as f:
        f.write(password + '\n')
    for file in settings.secrets.iterdir():
        file.chmod(0o600)
    return True


class Auth:
    def __init__(self, settings, db):
        self.settings, self.db = settings, db
        conf = json.loads((settings.secrets / 'auth.json').read_text('utf-8'))
        self.hasher = PasswordHasher()
        self.password_hash = conf['password_hash']
        self.signer = URLSafeTimedSerializer(conf['signing_key'], salt='campus-admin-v1')

    def session(self, cookie):
        if not cookie:
            return None
        try:
            sid = self.signer.loads(cookie, max_age=SESSION_SECONDS)
        except BadSignature:
            return None
        return self.db.one('SELECT * FROM sessions WHERE id=? AND expires>?', (sid, time.time()))

    def rate_allowed(self, client):
        now = time.time()
        with self.db.tx() as con:
            con.execute('DELETE FROM login_attempts WHERE started<?', (now - 300,))
            con.execute('DELETE FROM sessions WHERE expires<?', (now,))
            row = con.execute('SELECT * FROM login_attempts WHERE client=?', (client,)).fetchone()
            if row and row['count'] >= 10:
                return False
            con.execute('INSERT INTO login_attempts(client,started,count) VALUES(?,?,1) ON CONFLICT(client) DO UPDATE SET count=count+1', (client, now))
        return True

    def login(self, password):
        try:
            self.hasher.verify(self.password_hash, password)
        except (VerificationError, InvalidHashError):
            return None
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db.tx() as con:
            con.execute('INSERT INTO sessions VALUES(?,?,?)', (sid, csrf, time.time() + SESSION_SECONDS))
        return self.signer.dumps(sid), csrf
