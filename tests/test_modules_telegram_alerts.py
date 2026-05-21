"""Tests pour gpu_dashboard.modules.telegram_alerts.

Le module gère :
- Validation du format token (`<digits>:<35+chars>`) et chat ID (numérique)
- Vérification d'éligibilité (token + chat ID présents et bien formés)
- Envoi d'un message via l'API Telegram (urllib stdlib, pas de requests)
- Construction de payloads (chat_id, text, parse_mode)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import pytest

from gpu_dashboard.modules import telegram_alerts as tg


# ─────────────────────── validate_token_format ─────────────────────────────


class TestValidateTokenFormat:
    def test_valid_token(self):
        # Format Telegram réel : <bot_id>:<token>
        assert tg.validate_token_format("8289742567:AAHyAobsTKun7KBJ8ihwTToKqH6-I9wMmAg") is True

    def test_valid_short_bot_id(self):
        assert tg.validate_token_format("12345:AAHyAobsTKun7KBJ8ihwTToKqH6-I9wMm__") is True

    def test_missing_colon(self):
        assert tg.validate_token_format("12345AAHyAobsTKun7KBJ8ihwTToKqH6-I9wMm") is False

    def test_empty(self):
        assert tg.validate_token_format("") is False

    def test_none(self):
        assert tg.validate_token_format(None) is False

    def test_only_digits_before_colon(self):
        assert tg.validate_token_format("abc:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx") is False

    def test_token_too_short(self):
        assert tg.validate_token_format("12345:short") is False


# ─────────────────────── validate_chat_id_format ───────────────────────────


class TestValidateChatIdFormat:
    def test_positive_int_string(self):
        assert tg.validate_chat_id_format("8394766664") is True

    def test_negative_int_string_group_chat(self):
        # Les chats de groupe Telegram ont un ID négatif
        assert tg.validate_chat_id_format("-100123456789") is True

    def test_integer_directly(self):
        assert tg.validate_chat_id_format(8394766664) is True

    def test_alpha_invalid(self):
        assert tg.validate_chat_id_format("not-a-number") is False

    def test_empty_invalid(self):
        assert tg.validate_chat_id_format("") is False

    def test_none_invalid(self):
        assert tg.validate_chat_id_format(None) is False


# ──────────────────────────── can_enable ───────────────────────────────────


class TestCanEnable:
    def test_both_present_valid(self):
        ok, reason = tg.can_enable(
            token="8289742567:AAHyAobsTKun7KBJ8ihwTToKqH6-I9wMmAg",
            chat_id="8394766664",
        )
        assert ok is True

    def test_token_missing(self):
        ok, reason = tg.can_enable(token="", chat_id="123456")
        assert ok is False
        assert "token" in reason.lower()

    def test_chat_id_missing(self):
        ok, reason = tg.can_enable(
            token="8289742567:AAHyAobsTKun7KBJ8ihwTToKqH6-I9wMmAg",
            chat_id="",
        )
        assert ok is False
        assert "chat" in reason.lower()

    def test_token_malformed(self):
        ok, reason = tg.can_enable(token="garbage", chat_id="123456")
        assert ok is False
        assert "format" in reason.lower() or "token" in reason.lower()

    def test_chat_id_malformed(self):
        ok, reason = tg.can_enable(
            token="8289742567:AAHyAobsTKun7KBJ8ihwTToKqH6-I9wMmAg",
            chat_id="not-numeric",
        )
        assert ok is False


# ─────────────────────────── send_message ──────────────────────────────────


class _FakeResponse:
    def __init__(self, body: bytes, code: int = 200):
        self._body = body
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class TestSendMessage:
    def test_successful_send(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["data"] = req.data
            return _FakeResponse(json.dumps({"ok": True, "result": {"message_id": 42}}).encode())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        ok, result = tg.send_message(
            token="8289742567:AAHyAobsTKun7KBJ8ihwTToKqH6-I9wMmAg",
            chat_id="8394766664",
            text="hello",
        )
        assert ok is True
        # URL contient le token et l'endpoint
        assert "bot8289742567:" in captured["url"]
        assert "/sendMessage" in captured["url"]
        # Payload contient chat_id + text
        payload = captured["data"].decode()
        assert "chat_id=8394766664" in payload
        assert "text=hello" in payload

    def test_includes_parse_mode(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = req.data
            return _FakeResponse(json.dumps({"ok": True}).encode())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        tg.send_message(
            token="12345:ABC___________________________________",
            chat_id="999",
            text="*bold*",
            parse_mode="Markdown",
        )
        assert "parse_mode=Markdown" in captured["data"].decode()

    def test_api_error_returns_false(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            return _FakeResponse(
                json.dumps({"ok": False, "description": "chat not found"}).encode()
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        # chat_id numérique valide côté format mais inexistant côté API
        ok, msg = tg.send_message(
            token="12345:ABC___________________________________",
            chat_id="999999999",
            text="x",
        )
        assert ok is False
        assert "chat not found" in msg

    def test_network_error_returns_false(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        ok, msg = tg.send_message(
            token="12345:ABC___________________________________",
            chat_id="999",
            text="x",
        )
        assert ok is False
        assert "connection" in msg.lower() or "url" in msg.lower()

    def test_validates_before_sending(self, monkeypatch):
        """Avec un token mal formé, on n'envoie même pas la requête."""
        called = {"yes": False}

        def fake_urlopen(req, timeout=None):
            called["yes"] = True
            return _FakeResponse(b'{"ok":true}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        ok, msg = tg.send_message(token="bad", chat_id="999", text="x")
        assert ok is False
        assert called["yes"] is False  # network jamais touché
