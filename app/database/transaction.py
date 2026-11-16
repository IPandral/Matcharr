from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from .exceptions import TransactionConflict
from .rustydb import RustyDB, RustyDBConnection

T = TypeVar("T")


def run_transaction(
    database: RustyDB,
    operation: Callable[[RustyDBConnection], T],
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            with database.transaction() as connection:
                return operation(connection)
        except TransactionConflict:
            if attempt + 1 >= attempts:
                raise
            sleep(random.uniform(0.01, 0.04) * (attempt + 1))
    raise AssertionError("unreachable")

