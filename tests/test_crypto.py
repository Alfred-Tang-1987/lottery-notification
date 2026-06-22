import pytest
from cryptography.fernet import Fernet
from app.infrastructure.crypto import CryptoService


@pytest.fixture
def crypto():
    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()
    return CryptoService({1: k1, 2: k2}, current_version=2)


def test_encrypt_decrypt_roundtrip(crypto):
    ct = crypto.encrypt("secret-webhook", version=2)
    assert ct.version == 2
    assert crypto.decrypt(ct) == "secret-webhook"


def test_decrypt_old_version_after_rotation(crypto):
    # 用 V1 加密的旧数据，轮换到 V2 后仍可解
    ct = crypto.encrypt("legacy", version=1)
    assert crypto.decrypt(ct) == "legacy"


def test_decrypt_tuple_form(crypto):
    blob = crypto.encrypt("x", version=2)
    # 模拟 DB 存的 (version, ciphertext) 元组
    assert crypto.decrypt((blob.version, blob.ciphertext)) == "x"


def test_reencrypt_upgrades_version(crypto):
    old = crypto.encrypt("data", version=1)
    new = crypto.re_encrypt(old, to_version=2)
    assert new.version == 2
    assert crypto.decrypt(new) == "data"
