"""API Key 的本地加密（Windows DPAPI）。

设计原则：
- 只在 Windows 上用 DPAPI（`CryptProtectData` / `CryptUnprotectData`），
  密文与当前 Windows 用户绑定：复制到别的电脑或别的用户下无法解密；
- 非 Windows 平台或 DPAPI 调用失败时**回退明文**，保证应用仍能启动与使用
  （此时配置文件权限由 ConfigStore 收紧，安全性与 M1 一致）；
- 密文用 `dpapi:` 前缀标记，读取时不带前缀的值按旧版明文处理，平滑升级。

Java 对照：≈ 用 JNA 调 Win32 API 的 `LocalSecretCipher` 工具类；
本模块不依赖 PySide6，也不依赖 openai，可单独测试。
"""

from __future__ import annotations

import base64
import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

# 密文前缀：用于区分“DPAPI 密文”和“旧版明文 Key”。
DPAPI_PREFIX = "dpapi:"

# 不弹任何系统对话框（服务进程 / 无人值守场景）。
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretError(RuntimeError):
    """密钥加解密失败（通常是换了电脑/用户，或密文被截断）。"""


class _DataBlob(ctypes.Structure):
    # 用 ctypes 原生类型而不是 ctypes.wintypes，保证模块在非 Windows 上也能 import。
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    """把 Python bytes 包成 DATA_BLOB；同时返回缓冲区，防止被 GC 回收。"""
    if not data:
        raise SecretError("不能加密空内容")
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _from_blob(blob: _DataBlob) -> bytes:
    if not blob.pbData or blob.cbData == 0:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def _crypt32() -> ctypes.WinDLL:
    """延迟获取 crypt32，避免在非 Windows 上 import 就报错。"""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),  # pDataIn
        ctypes.c_wchar_p,  # szDataDescr
        ctypes.POINTER(_DataBlob),  # pOptionalEntropy
        ctypes.c_void_p,  # pvReserved
        ctypes.c_void_p,  # pPromptStruct
        ctypes.c_uint32,  # dwFlags
        ctypes.POINTER(_DataBlob),  # pDataOut
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(ctypes.c_wchar_p),  # ppszDataDescr
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    return crypt32


def _dpapi_protect(plaintext: bytes) -> bytes:
    crypt32 = _crypt32()
    blob_in, _buffer = _to_blob(plaintext)
    blob_out = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "Daily English API Key",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _from_blob(blob_out)
    finally:
        ctypes.WinDLL("kernel32").LocalFree(blob_out.pbData)


def _dpapi_unprotect(ciphertext: bytes) -> bytes:
    crypt32 = _crypt32()
    blob_in, _buffer = _to_blob(ciphertext)
    blob_out = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _from_blob(blob_out)
    finally:
        ctypes.WinDLL("kernel32").LocalFree(blob_out.pbData)


def is_encrypted(value: str) -> bool:
    """判断配置里的 Key 是否是本模块生成的 DPAPI 密文。"""
    return bool(value) and value.startswith(DPAPI_PREFIX)


def protect(plaintext: str) -> str:
    """加密 API Key；无法加密时回退明文（并写日志）。"""
    if not plaintext:
        return plaintext
    if sys.platform != "win32":
        return plaintext
    try:
        cipher = _dpapi_protect(plaintext.encode("utf-8"))
    except Exception:  # noqa: BLE001 - 加密失败不应阻塞保存配置
        logger.exception("DPAPI 加密失败，API Key 将以明文保存")
        return plaintext
    return DPAPI_PREFIX + base64.b64encode(cipher).decode("ascii")


def unprotect(stored: str) -> str:
    """解密 API Key；旧版明文原样返回，密文损坏抛 SecretError。"""
    if not stored or not is_encrypted(stored):
        return stored
    if sys.platform != "win32":
        raise SecretError("该 API Key 使用 Windows DPAPI 加密，当前系统无法解密。")
    try:
        cipher = base64.b64decode(stored[len(DPAPI_PREFIX) :], validate=True)
        return _dpapi_unprotect(cipher).decode("utf-8")
    except SecretError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一翻译成可操作的提示
        raise SecretError("API Key 解密失败：配置可能来自其他电脑或 Windows 用户。") from exc
