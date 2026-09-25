"""Create the household candidate once. Never print credentials into logs."""
import secrets
from pathlib import Path
from app.auth import password_hash
from app.config import settings
from app.db import init_db, one, utcnow, write


def main():
    init_db()
    if one('SELECT 1 FROM candidate_account WHERE id=1'):
        print('Candidate login already exists; no password changed.')
        return
    name=one('SELECT name FROM profile WHERE id=1')['name']
    if not name:
        raise SystemExit('Set the profile name first.')
    password=''.join(secrets.choice('23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz') for _ in range(6))
    write('INSERT INTO candidate_account VALUES(1,?,?)',(password_hash(password),utcnow()))
    target=settings.data_dir/'candidate-login.txt'
    with target.open('x') as f:
        target.chmod(0o600)
        f.write(f'Username: {name}\nPassword: {password}\n')
    print('Candidate login created. The owner can set a chosen six-character password from My profile.')


if __name__=='__main__':main()
