import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, salt, expected_digest = password_hash.split("$", 2)
    except ValueError:
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()
    return hmac.compare_digest(actual_digest, expected_digest)
