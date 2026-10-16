# backend/auth: accounts and sign-in. Open sign-up, every account equal.
#
#   passwords.py  PBKDF2 password hashing (standard library only)
#   tokens.py     signed, expiring session tokens (HMAC-SHA256)
#   store.py      accounts and chat ownership (SQLite by default)
#   service.py    sign-up, sign-in, lockout after wrong passwords
#   deps.py       FastAPI dependency: who is asking
#   routes.py     /auth/* endpoints
