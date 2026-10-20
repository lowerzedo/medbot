"""Generate the Compose-only encryption key once; never print or replace an existing key."""

import base64
import os
import secrets
from pathlib import Path

directory = Path("/provider-secrets")
directory.mkdir(mode=0o700, exist_ok=True)
directory.chmod(0o700)
os.chown(directory, 10001, 10001)
path = directory / "encryption-key"
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
except FileExistsError:
    descriptor = None
if descriptor is not None:
    with os.fdopen(descriptor, "wb") as target:
        target.write(base64.urlsafe_b64encode(secrets.token_bytes(32)))
os.chown(path, 10001, 10001)
path.chmod(0o400)
