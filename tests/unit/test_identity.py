import pytest
from argon2.exceptions import VerifyMismatchError

from fincore.application.identity import password_hasher


def test_password_hash_is_not_plaintext_and_can_be_verified() -> None:
    password = "uma-senha-local-segura-123"

    password_hash = password_hasher.hash(password)

    assert password_hash != password
    assert password_hasher.verify(password_hash, password)


def test_password_hash_rejects_a_different_password() -> None:
    password_hash = password_hasher.hash("senha-original-com-12")

    with pytest.raises(VerifyMismatchError):
        password_hasher.verify(password_hash, "outra-senha-com-12")
