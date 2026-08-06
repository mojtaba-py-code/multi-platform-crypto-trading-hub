"""The key-generation helper is the documented first step of setup, so it is
tested: a bad key here surfaces as a boot failure much later.
"""

from __future__ import annotations

from app.scripts.gen_keys import main
from app.security.crypto import SecretCipher


def test_gen_keys_prints_usable_secrets(capsys):
    main()
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines() if "=" in line)

    master = lines["MASTER_ENCRYPTION_KEY"]
    jwt_secret = lines["JWT_SECRET_KEY"]

    # The printed master key must actually work as a Fernet key...
    cipher = SecretCipher([master])
    assert cipher.decrypt(cipher.encrypt("secret")) == "secret"
    # ...and the JWT secret must clear the production length floor.
    assert len(jwt_secret) >= 32
