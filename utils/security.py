import hashlib
import hmac
import secrets

import pandas as pd

from config import ADMIN_USERS


PASSWORD_HASH_PREFIX = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 260000


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"{PASSWORD_HASH_PREFIX}${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def is_password_hash(stored_password):
    return str(stored_password).startswith(f"{PASSWORD_HASH_PREFIX}$")


def verify_password(stored_password, provided_password):
    stored_password = "" if pd.isna(stored_password) else str(stored_password)
    provided_password = "" if provided_password is None else str(provided_password)

    if is_password_hash(stored_password):
        try:
            _, iterations, salt, expected_digest = stored_password.split("$", 3)
            actual_digest = hashlib.pbkdf2_hmac(
                "sha256",
                provided_password.encode("utf-8"),
                salt.encode("utf-8"),
                int(iterations),
            ).hex()
            return hmac.compare_digest(actual_digest, expected_digest)
        except Exception:
            return False

    return hmac.compare_digest(stored_password, provided_password)


def is_admin(user):
    username = str(user.get("Username", "")).strip()
    role = str(user.get("Role", user.get("role", ""))).strip().lower()
    is_admin_flag = str(user.get("Is_Admin", user.get("is_admin", ""))).strip().lower()
    return username in ADMIN_USERS or role == "admin" or is_admin_flag in {"true", "1", "yes"}

