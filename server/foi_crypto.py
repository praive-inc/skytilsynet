#!/usr/bin/env python3
"""Optional field-level encryption at rest for FOI submissions (issue #114).

The free-text answer fields (`source`, `note`) can carry personal data a submitter
pasted from an innsyn answer — an official's name, an e-mail. When
`FOI_ENCRYPTION_KEY` is set we encrypt those two columns at rest with Fernet
(AES-128-CBC + HMAC-SHA256), so the SQLite file on the volume holds ciphertext,
not readable PII (GDPR Art. 32 / audit finding #114 MED).

Encryption is OPT-IN and keeps the stdlib-only default intact:
- No key configured  → fields are stored as before (dev/test, and the current
  prod default). `cryptography` is never imported, so it stays an optional dep.
- Key configured     → new writes are encrypted; and because every ciphertext
  carries the ``fernet:`` marker, legacy plaintext rows written before a key was
  set still read back untouched (mixed table during migration).

The key is a urlsafe-base64 32-byte Fernet key. Generate one with
``python3 -m server.foi_crypto keygen`` and put it ONLY in the service
environment — never in the DB or the repo. Lose it and the encrypted fields are
unrecoverable."""

import os
import sys

# Marker prefixing every ciphertext so decrypt() can tell an encrypted value from
# a legacy plaintext one and pass plaintext through unchanged. A real innsyn
# answer never starts with this token.
ENC_PREFIX = "fernet:"


class Cipher:
    """A thin wrapper over Fernet that only touches `source`/`note`. Encryption is
    the exception, not the rule (stdlib-first), so `cryptography` is imported lazily
    here — a service without a key never loads it."""

    def __init__(self, key):
        from cryptography.fernet import Fernet
        self._f = Fernet(key)

    def encrypt(self, text):
        """Plaintext → marked ciphertext. Empty/None passes through so we never
        store a token for a field the submitter left blank."""
        if not text:
            return text
        return ENC_PREFIX + self._f.encrypt(text.encode("utf-8")).decode("ascii")

    def decrypt(self, value):
        """Marked ciphertext → plaintext. Anything without the marker (a legacy
        plaintext row, or an empty value) is returned verbatim."""
        if not value or not value.startswith(ENC_PREFIX):
            return value
        return self._f.decrypt(value[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")


def cipher_from_env(env=None):
    """Build a Cipher from FOI_ENCRYPTION_KEY, or None when the key is unset. The
    server and the review CLI both call this so encryption is on/off by a single
    environment switch."""
    env = os.environ if env is None else env
    key = env.get("FOI_ENCRYPTION_KEY")
    if not key:
        return None
    return Cipher(key.encode("utf-8") if isinstance(key, str) else key)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "keygen":
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode("ascii"))
    else:
        sys.exit("usage: python3 -m server.foi_crypto keygen")
