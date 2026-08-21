import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import settings
from app.core.security import (
    ALGORITHM,
    TokenType,
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    hash_refresh_token,
    verify_password,
)


class TestPasswordHashing:
    def test_get_password_hash_differs_from_plain(self):
        password = "mysecretpassword"
        hashed_password = get_password_hash(password)
        assert hashed_password != password
        assert hashed_password.startswith("$argon2id$")

    def test_get_password_hash_uses_unique_salts(self):
        password = "mysecretpassword"
        assert get_password_hash(password) != get_password_hash(password)

    def test_verify_password_correct(self):
        hashed_password = get_password_hash("mysecretpassword")
        verified, updated_hash = verify_password("mysecretpassword", hashed_password)
        assert verified is True
        assert updated_hash is None

    def test_verify_password_incorrect(self):
        hashed_password = get_password_hash("mysecretpassword")
        verified, updated_hash = verify_password("wrongpassword", hashed_password)
        assert verified is False
        assert updated_hash is None

    def test_verify_password_rehashes_outdated_hash(self):
        """Argon2 is preferred over bcrypt, so a legacy bcrypt hash upgrades on login."""
        bcrypt_hash = BcryptHasher().hash("mysecretpassword")

        verified, updated_hash = verify_password("mysecretpassword", bcrypt_hash)

        assert verified is True
        assert updated_hash is not None
        assert updated_hash.startswith("$argon2id$")


class TestTokenCreation:
    def _decode_raw(self, token: str) -> dict:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])

    def test_create_access_token_claims(self):
        subject = uuid.uuid4()
        token = create_access_token(subject)

        claims = self._decode_raw(token)

        assert claims["sub"] == str(subject)
        assert claims["type"] == TokenType.ACCESS.value
        assert "jti" in claims
        assert "sid" not in claims
        assert claims["exp"] > claims["iat"]

    def test_create_access_token_with_session_id_includes_sid(self):
        subject = uuid.uuid4()
        session_id = uuid.uuid4()
        token = create_access_token(subject, session_id=session_id)

        claims = self._decode_raw(token)

        assert claims["sid"] == str(session_id)

    def test_create_refresh_token_includes_sid_and_type(self):
        subject = uuid.uuid4()
        session_id = uuid.uuid4()
        token = create_refresh_token(subject, session_id=session_id)

        claims = self._decode_raw(token)

        assert claims["sub"] == str(subject)
        assert claims["sid"] == str(session_id)
        assert claims["type"] == TokenType.REFRESH.value

    def test_create_password_reset_token_has_no_sid(self):
        subject = uuid.uuid4()
        token = create_password_reset_token(subject)

        claims = self._decode_raw(token)

        assert claims["type"] == TokenType.PASSWORD_RESET.value
        assert "sid" not in claims


class TestDecodeToken:
    def test_decode_token_returns_claims_for_matching_type(self):
        subject = uuid.uuid4()
        token = create_access_token(subject)

        claims = decode_token(token, expected_type=TokenType.ACCESS)

        assert claims is not None
        assert claims["sub"] == str(subject)

    def test_decode_token_returns_none_for_mismatched_type(self):
        token = create_access_token(uuid.uuid4())

        assert decode_token(token, expected_type=TokenType.REFRESH) is None

    def test_decode_token_returns_none_for_tampered_signature(self):
        token = create_access_token(uuid.uuid4())
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")

        assert decode_token(tampered, expected_type=TokenType.ACCESS) is None

    def test_decode_token_returns_none_for_malformed_token(self):
        assert decode_token("not-a-jwt-token", expected_type=TokenType.ACCESS) is None

    def test_decode_token_returns_none_for_expired_token(self):
        now = datetime.now(UTC)
        claims = {
            "sub": str(uuid.uuid4()),
            "exp": now - timedelta(minutes=5),
            "iat": now - timedelta(minutes=10),
            "nbf": now - timedelta(minutes=10),
            "type": TokenType.ACCESS.value,
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(claims, settings.SECRET_KEY, algorithm=ALGORITHM)

        assert decode_token(token, expected_type=TokenType.ACCESS) is None


class TestHashRefreshToken:
    def test_hash_refresh_token_is_deterministic(self):
        token = "some-refresh-token"
        assert hash_refresh_token(token) == hash_refresh_token(token)

    def test_hash_refresh_token_differs_for_different_tokens(self):
        assert hash_refresh_token("token-a") != hash_refresh_token("token-b")

    def test_hash_refresh_token_matches_sha256(self):
        token = "some-refresh-token"
        assert hash_refresh_token(token) == hashlib.sha256(token.encode()).hexdigest()
