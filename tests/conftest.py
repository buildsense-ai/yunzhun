"""Test environment must be configured before app modules are imported."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

_TMP = Path(tempfile.mkdtemp(prefix="yunzhun-test-"))
os.environ["YUNZHUN_DB_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["YUNZHUN_API_KEY"] = "test-key"
os.environ["YUNZHUN_SYNC_ENABLED"] = "false"
os.environ["YUNZHUN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
