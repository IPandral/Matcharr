from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Sequence

import mysql.connector
from mysql.connector import Error as MySQLError

from .exceptions import DatabaseError, TransactionConflict

Parameters = Sequence[Any] | None


def _translate_error(error: Exception) -> DatabaseError:
    message = str(error)
    if "serialization conflict" in message.lower():
        return TransactionConflict(message)
    return DatabaseError(message)


@dataclass(frozen=True)
class RustyDBConfig:
    host: str
    port: int
    user: str
    password: str
    connect_timeout: int = 5


class RustyDBConnection:
    """One physical connection; required for transaction-scoped operations."""

    def __init__(self, connection: Any):
        self.connection = connection

    def execute(self, query: str, parameters: Parameters = None) -> int:
        cursor = self.connection.cursor(prepared=parameters is not None)
        try:
            cursor.execute(query, parameters or ())
            return max(cursor.rowcount, 0)
        except MySQLError as error:
            raise _translate_error(error) from error
        finally:
            cursor.close()

    def fetch_one(self, query: str, parameters: Parameters = None) -> dict | None:
        # RustyDB 0.3's prepared-result path can report numeric columns as
        # strings while returning their raw binary representation. Connector/C
        # then attempts UTF-8 decoding and raises for values such as 0x89.
        # Parameterized non-prepared cursors still escape values safely, while
        # using the wire text-result format that RustyDB handles correctly.
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, parameters or ())
            row = cursor.fetchone()
            if row is not None:
                cursor.fetchall()
            return row
        except MySQLError as error:
            raise _translate_error(error) from error
        finally:
            cursor.close()

    def fetch_all(self, query: str, parameters: Parameters = None) -> list[dict]:
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, parameters or ())
            return list(cursor.fetchall())
        except MySQLError as error:
            raise _translate_error(error) from error
        finally:
            cursor.close()


class RustyDB:
    """Small MySQL-wire abstraction used by all production repositories."""

    def __init__(
        self,
        config: RustyDBConfig,
        connector: Callable[..., Any] = mysql.connector.connect,
    ):
        self.config = config
        self.connector = connector

    def connect(self) -> Any:
        try:
            return self.connector(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                connection_timeout=self.config.connect_timeout,
                auth_plugin="mysql_native_password",
                use_pure=True,
            )
        except MySQLError as error:
            raise _translate_error(error) from error

    @contextmanager
    def connection(self) -> Iterator[RustyDBConnection]:
        connection = self.connect()
        try:
            yield RustyDBConnection(connection)
        finally:
            connection.close()

    def execute(self, query: str, parameters: Parameters = None) -> int:
        with self.connection() as connection:
            return connection.execute(query, parameters)

    def fetch_one(self, query: str, parameters: Parameters = None) -> dict | None:
        with self.connection() as connection:
            return connection.fetch_one(query, parameters)

    def fetch_all(self, query: str, parameters: Parameters = None) -> list[dict]:
        with self.connection() as connection:
            return connection.fetch_all(query, parameters)

    @contextmanager
    def transaction(self) -> Iterator[RustyDBConnection]:
        connection = self.connect()
        transactional = RustyDBConnection(connection)
        control = connection.cursor()
        try:
            control.execute("BEGIN")
            yield transactional
            control.execute("COMMIT")
        except Exception as error:
            try:
                control.execute("ROLLBACK")
            except Exception:
                pass
            if isinstance(error, DatabaseError):
                raise
            if isinstance(error, MySQLError):
                raise _translate_error(error) from error
            raise
        finally:
            control.close()
            connection.close()

    def ping(self) -> bool:
        with self.connection() as connection:
            row = connection.fetch_one("SELECT @@version")
            return bool(row and "RustyDB" in str(next(iter(row.values()))))
