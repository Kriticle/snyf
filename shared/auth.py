from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import asdict

from shared.models import ApiKey, Customer, Session, utc_now
from shared.plans import plan_limits
from shared.storage import JsonStore


class AuthError(ValueError):
    pass


class AuthService:
    def __init__(self, store: JsonStore, session_ttl_seconds: int = 60 * 60 * 24 * 30) -> None:
        self.store = store
        self.session_ttl_seconds = session_ttl_seconds

    def signup(self, email: str, password: str) -> dict:
        normalized = normalize_email(email)
        validate_password(password)
        if self.store.get_customer_by_email(normalized):
            raise AuthError("Email already exists.")

        customer = Customer(
            customer_id=f"cust_{secrets.token_urlsafe(12)}",
            email=normalized,
            password_hash=hash_password(password),
            status="ACTIVE",
            plan="FREE",
        )
        self.store.upsert_customer(customer)
        session, refresh_token = self._create_session(customer.customer_id)
        api_key, raw_api_key = self.create_api_key(customer.customer_id)
        return {
            "customer": public_customer(customer),
            "session": public_session(session),
            "refresh_token": refresh_token,
            "api_key": raw_api_key,
            "api_key_id": api_key.api_key_id,
        }

    def login(self, email: str, password: str) -> dict:
        customer = self.store.get_customer_by_email(normalize_email(email))
        if customer is None or not verify_password(password, customer.password_hash):
            raise AuthError("Invalid email or password.")
        if customer.status != "ACTIVE":
            raise AuthError("Customer is not active.")
        session, refresh_token = self._create_session(customer.customer_id)
        return {
            "customer": public_customer(customer),
            "session": public_session(session),
            "refresh_token": refresh_token,
        }

    def create_api_key(self, customer_id: str) -> tuple[ApiKey, str]:
        customer = self.store.get_customer(customer_id)
        if customer is None:
            raise AuthError("Customer not found.")
        self._enforce_active_api_key_limit(customer)
        raw_api_key = f"fep_{secrets.token_urlsafe(32)}"
        api_key = ApiKey(
            api_key_id=f"key_{secrets.token_urlsafe(12)}",
            customer_id=customer_id,
            api_key_hash=sha256(raw_api_key),
            status="ACTIVE",
            api_key_value=raw_api_key,
        )
        self.store.upsert_api_key(api_key)
        return api_key, raw_api_key

    def _enforce_active_api_key_limit(self, customer: Customer) -> None:
        limit = plan_limits(customer.plan)["active_api_keys"]
        active_keys = [
            api_key
            for api_key in self.store.api_keys_for_customer(customer.customer_id)
            if api_key.status == "ACTIVE"
        ]
        if len(active_keys) < limit:
            return
        active_keys.sort(key=lambda api_key: api_key.created_at)
        for api_key in active_keys[: len(active_keys) - limit + 1]:
            api_key.status = "INACTIVE"
            self.store.upsert_api_key(api_key)

    def list_api_keys(self, refresh_token: str | None) -> list[dict]:
        customer = self.authenticate_session(refresh_token)
        return [public_api_key(api_key) for api_key in self.store.api_keys_for_customer(customer.customer_id)]

    def create_customer_api_key(self, refresh_token: str | None) -> dict:
        customer = self.authenticate_session(refresh_token)
        api_key, raw_api_key = self.create_api_key(customer.customer_id)
        payload = public_api_key(api_key)
        payload["api_key"] = raw_api_key
        return payload

    def authenticate_api_key(self, raw_api_key: str | None) -> Customer:
        if not raw_api_key:
            raise AuthError("Missing API key.")
        api_key = self.store.get_api_key_by_hash(sha256(raw_api_key))
        if api_key is None or api_key.status != "ACTIVE":
            raise AuthError("Invalid API key.")
        customer = self.store.get_customer(api_key.customer_id)
        if customer is None or customer.status != "ACTIVE":
            raise AuthError("Customer is not active.")
        api_key.last_used_at = utc_now()
        self.store.upsert_api_key(api_key)
        return customer

    def authenticate_session(self, refresh_token: str | None) -> Customer:
        if not refresh_token:
            raise AuthError("Missing session token.")
        session = self.store.get_session_by_refresh_hash(sha256(refresh_token))
        if session is None or session.status != "ACTIVE":
            raise AuthError("Invalid session token.")
        if session.expires_at < int(time.time()):
            raise AuthError("Session has expired.")
        customer = self.store.get_customer(session.customer_id)
        if customer is None or customer.status != "ACTIVE":
            raise AuthError("Customer is not active.")
        return customer

    def _create_session(self, customer_id: str) -> tuple[Session, str]:
        refresh_token = secrets.token_urlsafe(32)
        session = Session(
            session_id=f"sess_{secrets.token_urlsafe(12)}",
            customer_id=customer_id,
            refresh_token_hash=sha256(refresh_token),
            status="ACTIVE",
            expires_at=int(time.time()) + self.session_ttl_seconds,
        )
        self.store.upsert_session(session)
        return session, refresh_token


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
        raise AuthError("Invalid email.")
    return normalized


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return "{}${}".format(base64.b64encode(salt).decode("utf-8"), base64.b64encode(digest).decode("utf-8"))


def verify_password(password: str, stored_hash: str) -> bool:
    salt_text, digest_text = stored_hash.split("$", 1)
    salt = base64.b64decode(salt_text.encode("utf-8"))
    expected = base64.b64decode(digest_text.encode("utf-8"))
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return hmac.compare_digest(actual, expected)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def public_customer(customer: Customer) -> dict:
    data = asdict(customer)
    data.pop("password_hash", None)
    return data


def public_session(session: Session) -> dict:
    data = asdict(session)
    data.pop("refresh_token_hash", None)
    return data


def public_api_key(api_key: ApiKey) -> dict:
    return {
        "api_key_id": api_key.api_key_id,
        "api_key": api_key.api_key_value,
        "status": api_key.status,
        "created_at": api_key.created_at,
        "last_used_at": api_key.last_used_at,
    }
