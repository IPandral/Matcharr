from contextlib import contextmanager

import pytest

from app.database.exceptions import TransactionConflict
from app.database.transaction import run_transaction


class ConflictingDB:
    def __init__(self, failures):
        self.failures = failures
        self.attempts = 0

    @contextmanager
    def transaction(self):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise TransactionConflict("Serialization conflict")
        yield object()


def test_transaction_conflicts_retry_complete_operation():
    database = ConflictingDB(2)
    sleeps = []
    result = run_transaction(database, lambda connection: "done", retries=3, sleep=sleeps.append)
    assert result == "done"
    assert database.attempts == 3
    assert len(sleeps) == 2


def test_transaction_conflict_retry_is_bounded():
    database = ConflictingDB(3)
    with pytest.raises(TransactionConflict):
        run_transaction(database, lambda connection: None, retries=3, sleep=lambda _: None)
    assert database.attempts == 3

