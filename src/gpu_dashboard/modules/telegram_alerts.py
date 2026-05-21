"""Module Telegram Alerts — envoi de notifications push via l'API Bot Telegram.

L'utilisateur déclare son token + chat_id dans `secrets.env` (chmod 600). Le module
expose can_enable() pour que l'install script propose le module uniquement si la
config est présente, et send_message() pour envoyer (utilisé par oculink_watchdog
+ tests utilisateur via /api/alerts-test).

Pure stdlib (urllib) — pas de dépendance `requests`.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Tuple, Union


NAME = "telegram_alerts"

# Token Telegram : <bot_id_numérique>:<35+ caractères alphanumériques + _ -+>
_TOKEN_RX = re.compile(r"^\d+:[A-Za-z0-9_\-]{35,}$")


# ─────────────────────────── validation formats ────────────────────────────


def validate_token_format(token) -> bool:
    """Vérifie que `token` ressemble à un token bot Telegram valide.

    Format officiel : `<bot_id>:<35+ chars alphanumériques _-+`.
    """
    if not token or not isinstance(token, str):
        return False
    return bool(_TOKEN_RX.match(token))


def validate_chat_id_format(chat_id) -> bool:
    """Vérifie que `chat_id` est numérique (int ou string castable en int).

    Les chats de groupe Telegram ont un ID négatif (ex : -100xxx), c'est OK.
    """
    if chat_id is None:
        return False
    if isinstance(chat_id, int):
        return True
    if isinstance(chat_id, str):
        s = chat_id.strip()
        if not s:
            return False
        if s.startswith("-"):
            s = s[1:]
        return s.isdigit()
    return False


# ──────────────────────────── can_enable ───────────────────────────────────


def can_enable(token: str, chat_id: Union[str, int]) -> Tuple[bool, str]:
    """Le module peut-il être activé ? Vérifie présence + format des secrets."""
    if not token:
        return False, "Telegram token missing (TG_TOKEN in secrets.env)"
    if not chat_id:
        return False, "Telegram chat_id missing (TG_CHAT in secrets.env)"
    if not validate_token_format(token):
        return False, "invalid token format (expected: <bot_id>:<token_35+chars>)"
    if not validate_chat_id_format(chat_id):
        return False, "invalid chat_id format (expected: numeric, e.g. 123456789)"
    return True, "OK"


# ───────────────────────────── send_message ────────────────────────────────


def send_message(
    token: str,
    chat_id: Union[str, int],
    text: str,
    parse_mode: str = "Markdown",
    timeout: float = 8.0,
) -> Tuple[bool, str]:
    """Envoie un message via l'API Telegram. Retourne (ok, message_d_erreur_ou_id).

    - Valide les formats avant tout appel réseau (économie si secrets pourris)
    - Pure stdlib (urllib), pas de `requests`
    - Timeout court par défaut (8s) — ne bloque pas le watchdog
    """
    if not validate_token_format(token):
        return False, "invalid token format"
    if not validate_chat_id_format(chat_id):
        return False, "invalid chat_id format"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": parse_mode,
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
        return False, f"network error: {e}"

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False, f"non-JSON response: {body[:200]}"

    if data.get("ok"):
        msg_id = data.get("result", {}).get("message_id", "?")
        return True, str(msg_id)
    return False, str(data.get("description", body[:200]))
