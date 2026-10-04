import hashlib
import hmac
import secrets


def key_digest(key, pepper):
    return hmac.new(pepper.encode(), key.encode(), hashlib.sha256).hexdigest()


def issue_key(environment):
    return f"cb_{environment}_{secrets.token_urlsafe(32)}"


def sign_event(secret, timestamp, body):
    message = str(timestamp).encode() + b"." + body
    return "v1=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_event(secret, timestamp, body, signature, now, tolerance=300):
    return abs(now - timestamp) <= tolerance and hmac.compare_digest(
        sign_event(secret, timestamp, body), signature
    )
