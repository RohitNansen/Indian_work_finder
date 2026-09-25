"""Two household roles, signed sessions and persistent sign-in throttling."""
import hashlib
import hmac
import secrets
import time
from .config import settings
from .db import one, write


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()


def check_password(password, encoded):
    return bool(encoded) and hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)


def session_value(role='admin'):
    # Password changes invalidate candidate sessions.
    account = one('SELECT password_hash FROM candidate_account WHERE id=1')
    version = hashlib.sha256((account['password_hash'] if account else '').encode()).hexdigest()[:12] if role == 'candidate' else 'owner'
    payload = f'{int(time.time()) + 7 * 86400}.{role}.{version}'
    return payload + '.' + hmac.new(settings.app_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def session_role(request):
    try:
        value = request.cookies.get('work_session', '')
        payload, signature = value.rsplit('.', 1)
        expected = hmac.new(settings.app_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        parts = payload.split('.')
        if int(parts[0]) <= time.time():
            return None
        if len(parts) == 1:  # Existing owner session, issued by v1.
            return 'admin'
        if len(parts) != 3 or parts[1] not in {'admin', 'candidate'}:
            return None
        if parts[1] == 'candidate':
            account = one('SELECT password_hash FROM candidate_account WHERE id=1')
            if not account or parts[2] != hashlib.sha256(account['password_hash'].encode()).hexdigest()[:12]:
                return None
        return parts[1]
    except (ValueError, TypeError):
        return None


def login_allowed(ip):
    row = one('SELECT COUNT(*) AS n FROM login_attempts WHERE client=? AND attempted_at>?', (ip, time.time()-900))
    return row['n'] < 8


def record_failure(ip):
    write('INSERT INTO login_attempts(client,attempted_at) VALUES(?,?)', (ip, time.time()))
    write('DELETE FROM login_attempts WHERE attempted_at<?', (time.time()-86400,))
