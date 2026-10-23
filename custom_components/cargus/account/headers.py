"""Per-request headers for signed-in MyCargus calls.

Two variants carry the account's phone number: the web portal's ``K`` header
and the app's ``k``/``seed``/``s`` set. The client starts with the web one and
switches when the backend refuses it.
"""

from __future__ import annotations

import random
import uuid

from ..tracking.headers import build_request_headers

VARIANT_WEB = "web"
VARIANT_APP = "app"
VARIANTS = (VARIANT_WEB, VARIANT_APP)

PORTAL_URL = "https://mycargus.cargus.ro"
# suite: allow browser user-agent (the web variant replays the MyCargus portal's own calls)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def normalize_phone(value: str) -> str:
    """Keep the digits and a leading plus, nothing else."""
    value = (value or "").strip()
    digits = "".join(char for char in value if char.isdigit())
    return f"+{digits}" if value.startswith("+") else digits


def phone_key(phone: str) -> str:
    """Return the portal's ``K`` header: the phone number mixed with noise."""
    had_plus = phone.startswith("+")
    phone = phone.removeprefix("+").ljust(7, "0")
    k0, k1, k2, k3 = (int(char) for char in str(random.randint(0, 999) * 9 + 1750)[:4])
    guid = uuid.uuid4().hex * 2
    secret = (
        f"{phone[0:1]}{k0}{phone[1:3]}{k1}{phone[3:5]}{k2}{phone[5:7]}{k3}{phone[7:]}"
    )
    secret = secret.ljust(20, "X") + ("Y" if had_plus else "X")
    mixed = "".join(
        guid[index * 2 : index * 2 + 2] + char for index, char in enumerate(secret)
    )
    mixed = mixed[:6] + str(k2) + mixed[6:8] + str(k3) + mixed[8:]
    return mixed[:k1] + str(k0) + mixed[k1:] + str(k1)


def build_account_headers(
    variant: str, phone: str, access_token: str
) -> dict[str, str]:
    """Return the headers for one signed-in request in the given variant."""
    if variant == VARIANT_APP:
        headers = build_request_headers(phone)
    else:
        headers = {
            "Accept": "application/json",
            "K": phone_key(phone),
            "Source": "1",
            "Origin": PORTAL_URL,
            "Referer": f"{PORTAL_URL}/",
            "User-Agent": _USER_AGENT,
        }
    headers["Authorization"] = f"Bearer {access_token}"
    return headers
