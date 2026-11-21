import os

import pytest

from app.database.rustydb import RustyDB, RustyDBConfig


@pytest.fixture
def database():
    if os.getenv("MATCHARR_RUSTYDB_INTEGRATION") != "1":
        pytest.skip("Set MATCHARR_RUSTYDB_INTEGRATION=1 to test a real RustyDB server.")
    return RustyDB(
        RustyDBConfig(
            host=os.getenv("RUSTYDB_HOST", "127.0.0.1"),
            port=int(os.getenv("RUSTYDB_WIRE_PORT", "3307")),
            user=os.getenv("RUSTYDB_USERNAME", "matcharr"),
            password=os.getenv("RUSTYDB_PASSWORD", ""),
            tls_ca=os.environ["RUSTYDB_TLS_CA"],
        )
    )

