from dataclasses import dataclass

from cryptography.fernet import Fernet


@dataclass(frozen=True)
class CipherBlob:
    """加密结果：(version, ciphertext)。DB 存两列或拼接。"""

    version: int
    ciphertext: str


class CryptoService:
    """Fernet 多版本：按 key_version 选对应 Fernet 解密（旧 key 解旧数据），用 current key 加密。"""

    def __init__(self, keys: dict[int, str], current_version: int):
        if current_version not in keys:
            raise ValueError(f'current_version {current_version} 不在 keys 中')
        self._keys = keys
        self._current = current_version
        self._fernets = {v: Fernet(k.encode()) for v, k in keys.items()}

    def encrypt(self, plaintext: str, version: int | None = None) -> CipherBlob:
        v = version or self._current
        if v not in self._fernets:
            raise ValueError(f'未知 key version: {v}')
        ct = self._fernets[v].encrypt(plaintext.encode()).decode()
        return CipherBlob(version=v, ciphertext=ct)

    def decrypt(self, blob: CipherBlob | tuple[int, str]) -> str:
        if isinstance(blob, tuple):
            v, ct = blob
        else:
            v, ct = blob.version, blob.ciphertext
        if v not in self._fernets:
            raise ValueError(f'无法解密：未知 key version {v}')
        return self._fernets[v].decrypt(ct.encode()).decode()

    def re_encrypt(self, blob: CipherBlob, to_version: int) -> CipherBlob:
        """轮换：旧 key 解密 → 新 key 加密。"""
        plaintext = self.decrypt(blob)
        return self.encrypt(plaintext, version=to_version)
