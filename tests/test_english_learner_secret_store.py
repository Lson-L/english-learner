"""API Key 加密（Windows DPAPI）测试。

非 Windows 平台只会验证“明文 passthrough”；DPAPI 往返与损坏密文
在 Windows 上执行（本机开发环境即 Windows）。
"""

from __future__ import annotations

import sys

import pytest

from english_learner.services.secret_store import (
    DPAPI_PREFIX,
    SecretError,
    is_encrypted,
    protect,
    unprotect,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 只在 Windows 可用")


@windows_only
def test_dpapi_roundtrip() -> None:
    token = protect("sk-test-1234567890")

    assert token.startswith(DPAPI_PREFIX)
    assert "sk-test" not in token
    assert is_encrypted(token) is True
    assert unprotect(token) == "sk-test-1234567890"


@windows_only
def test_same_plaintext_produces_different_ciphertext() -> None:
    # DPAPI 每次加密都会带随机盐，密文不应相等（避免被直接比对猜测）。
    first = protect("sk-same-key")
    second = protect("sk-same-key")
    assert first != second
    assert unprotect(first) == unprotect(second) == "sk-same-key"


def test_plaintext_passthrough() -> None:
    assert is_encrypted("sk-old-plaintext") is False
    assert unprotect("sk-old-plaintext") == "sk-old-plaintext"
    assert unprotect("") == ""
    assert protect("") == ""


def test_corrupt_ciphertext_raises() -> None:
    with pytest.raises(SecretError):
        unprotect(DPAPI_PREFIX + "bm90LWEtcmVhbC1kcGFwaS1ibG9i")
