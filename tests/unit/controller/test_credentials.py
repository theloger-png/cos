"""Unit tests for controller/credentials.py — password generation and hashing."""

from __future__ import annotations

import re
import string

import pytest
from controller.credentials import generate_password, hash_password


class TestGeneratePassword:
    def test_default_length_is_16(self):
        pw = generate_password()
        assert len(pw) == 16

    def test_custom_length(self):
        for n in (8, 20, 32):
            assert len(generate_password(n)) == n

    def test_only_alphanumeric_chars(self):
        allowed = set(string.ascii_letters + string.digits)
        for _ in range(50):
            pw = generate_password(32)
            assert set(pw).issubset(allowed), f"non-alphanumeric chars in: {pw}"

    def test_passwords_are_unique(self):
        passwords = {generate_password() for _ in range(100)}
        # With a 62-char alphabet and length 16, collisions in 100 samples are
        # astronomically unlikely; any collision indicates a broken RNG.
        assert len(passwords) == 100


class TestHashPassword:
    def test_returns_sha512_crypt_format(self):
        h = hash_password("testpassword")
        # SHA-512 crypt hashes begin with $6$
        assert h.startswith("$6$"), f"unexpected hash prefix: {h!r}"

    def test_different_passwords_produce_different_hashes(self):
        h1 = hash_password("password1")
        h2 = hash_password("password2")
        assert h1 != h2

    def test_same_password_produces_different_hashes_due_to_salt(self):
        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        # Different salts → different hashes
        assert h1 != h2

    def test_hash_has_expected_structure(self):
        h = hash_password("anypassword")
        # Format: $6$<salt>$<hash> — at least 4 dollar-sign-separated parts
        parts = h.split("$")
        assert len(parts) >= 4
        assert parts[1] == "6"
