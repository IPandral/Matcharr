class DatabaseError(RuntimeError):
    pass


class TransactionConflict(DatabaseError):
    pass

