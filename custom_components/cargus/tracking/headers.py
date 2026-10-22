"""Per-request headers for the Cargus tracking endpoint."""

from __future__ import annotations

import base64
import os
import uuid

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_HEADER_TOKEN = "5F66C878478EBCB2F3967DF94BE57959"


def new_message() -> str:
    """Return a fresh 12-character message for one anonymous request."""
    return uuid.uuid4().hex[:12]


def build_request_headers(message: str) -> dict[str, str]:
    """Return the ``k``/``seed``/``s`` headers that carry ``message``.

    A pure function of the message and a fresh random IV, so a second source
    that sends a different message can reuse it. Raises ``ValueError`` when the
    headers cannot be built.
    """
    material = bytes.fromhex(_HEADER_TOKEN)
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(message.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(material), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(data) + encryptor.finalize()
    return {
        "k": base64.b64encode(ciphertext).decode(),
        "seed": iv.hex(),
        "s": "M",
        "Accept": "application/json",
    }
