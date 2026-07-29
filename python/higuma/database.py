from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Generic, TypeVar

ModelT = TypeVar("ModelT", bound="Model")


class Field:
    sql_type = "TEXT"

    def __init__(
        self,
        *,
        primary_key: bool = False,
        nullable: bool = True,
        unique: bool = False,
        default: Any = None,
        index: bool = False,
    ) -> None:
        self.primary_key = primary_key
        self.nullable = False if primary_key else nullable
        self.unique = unique
        self.default = default
        self.index = index
        self.name = ""

    def __set_name__(self, owner: type[Model], name: str) -> None:
        self.name = name

    def __get__(self, instance: Model | None, owner: type[Model]) -> Any:
        if instance is None:
            return self
        return instance.__dict__.get(self.name, self.get_default())

    def __set__(self, instance: Model, value: Any) -> None:
        instance.__dict__[self.name] = value

    def get_default(self) -> Any:
        return self.default() if callable(self.default) else self.default

    def to_database(self, value: Any) -> Any:
        return value

    def from_database(self, value: Any) -> Any:
        return value

    def ddl(self) -> str:
        parts = [quote_identifier(self.name), self.sql_type]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if not self.nullable:
            parts.append("NOT NULL")
        if self.unique:
            parts.append("UNIQUE")
        return " ".join(parts)


class Integer(Field):
    sql_type = "INTEGER"

    def __init__(self, *, autoincrement: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.autoincrement = autoincrement

    def ddl(self) -> str:
        if self.primary_key and self.autoincrement:
            return f"{quote_identifier(self.name)} INTEGER PRIMARY KEY AUTOINCREMENT"
        return super().ddl()

    def from_database(self, value: Any) -> int | None:
        return None if value is None else int(value)


class Float(Field):
    sql_type = "REAL"

    def from_database(self, value: Any) -> float | None:
        return None if value is None else float(value)


class String(Field):
    sql_type = "TEXT"

    def __init__(self, length: int | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.length = length

    def to_database(self, value: Any) -> str | None:
        if value is None:
            return None
        value = str(value)
        if self.length is not None and len(value) > self.length:
            raise ValueError(f"{self.name} exceeds maximum length {self.length}")
        return value


class Boolean(Field):
    sql_type = "INTEGER"

    def to_database(self, value: Any) -> int | None:
        return None if value is None else int(bool(value))

    def from_database(self, value: Any) -> bool | None:
        return None if value is None else bool(value)


class DateTime(Field):
    sql_type = "TEXT"

    def to_database(self, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"{self.name} must be a datetime")
        return value.isoformat()

    def from_database(self, value: Any) -> datetime | None:
        return None if value is None else datetime.fromisoformat(value)


class Date(Field):
    sql_type = "TEXT"

    def to_database(self, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, date):
            raise TypeError(f"{self.name} must be a date")
        return value.isoformat()

    def from_database(self, value: Any) -> date | None:
        return None if value is None else date.fromisoformat(value)


class Blob(Field):
    sql_type = "BLOB"

    def to_database(self, value: Any) -> bytes | None:
        return None if value is None else bytes(value)


class ModelMeta(type):
    def __new__(
        mcls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> ModelMeta:
        fields: dict[str, Field] = {}
        for base in bases:
            fields.update(getattr(base, "__fields__", {}))
        fields.update(
            {
                field_name: value
                for field_name, value in namespace.items()
                if isinstance(value, Field)
            }
        )
        cls = super().__new__(mcls, name, bases, namespace)
        cls.__fields__ = fields
        cls.__tablename__ = namespace.get("__tablename__", name.lower())
        primary_keys = [field for field in fields.values() if field.primary_key]
        if name != "Model" and len(primary_keys) > 1:
            raise TypeError("higuma ORM supports one primary key per model")
        cls.__primary_key__ = primary_keys[0] if primary_keys else None
        return cls


class Model(metaclass=ModelMeta):
    __fields__: dict[str, Field]
    __tablename__: str
    __primary_key__: Field | None

    def __init__(self, **values: Any) -> None:
        unknown = set(values) - set(self.__fields__)
        if unknown:
            raise TypeError(f"unknown model fields: {', '.join(sorted(unknown))}")
        for name, field in self.__fields__.items():
            setattr(self, name, values.get(name, field.get_default()))

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__fields__}

    def __repr__(self) -> str:
        values = ", ".join(f"{name}={getattr(self, name)!r}" for name in self.__fields__)
        return f"{type(self).__name__}({values})"


class Query(Generic[ModelT]):
    def __init__(
        self,
        session: Session,
        model: type[ModelT],
        *,
        where: tuple[str, ...] = (),
        params: tuple[Any, ...] = (),
        ordering: str | None = None,
        limit_value: int | None = None,
    ) -> None:
        self.session = session
        self.model = model
        self.where = where
        self.params = params
        self.ordering = ordering
        self.limit_value = limit_value

    def filter_by(self, **values: Any) -> Query[ModelT]:
        clauses = list(self.where)
        params = list(self.params)
        for name, value in values.items():
            field = _field_for(self.model, name)
            clauses.append(f"{quote_identifier(name)} = ?")
            params.append(field.to_database(value))
        return self._clone(where=tuple(clauses), params=tuple(params))

    def order_by(self, field: str, *, descending: bool = False) -> Query[ModelT]:
        _field_for(self.model, field)
        direction = "DESC" if descending else "ASC"
        return self._clone(ordering=f"{quote_identifier(field)} {direction}")

    def limit(self, value: int) -> Query[ModelT]:
        if value < 0:
            raise ValueError("limit must be non-negative")
        return self._clone(limit_value=value)

    def all(self) -> list[ModelT]:
        sql, params = self._select_sql()
        rows = self.session.execute(sql, params).fetchall()
        return [_row_to_model(self.model, row) for row in rows]

    def first(self) -> ModelT | None:
        rows = self.limit(1).all()
        return rows[0] if rows else None

    def get(self, primary_key: Any) -> ModelT | None:
        field = self.model.__primary_key__
        if field is None:
            raise TypeError(f"{self.model.__name__} does not define a primary key")
        return self.filter_by(**{field.name: primary_key}).first()

    def count(self) -> int:
        table = quote_identifier(self.model.__tablename__)
        where, params = self._where_sql()
        # All identifiers pass quote_identifier; all values use placeholders.
        row = self.session.execute(
            f"SELECT COUNT(*) AS count FROM {table}{where}",  # nosec B608
            params,
        ).fetchone()
        return int(row["count"])

    def delete(self) -> int:
        table = quote_identifier(self.model.__tablename__)
        where, params = self._where_sql()
        # All identifiers pass quote_identifier; all values use placeholders.
        cursor = self.session.execute(
            f"DELETE FROM {table}{where}",  # nosec B608
            params,
        )
        return cursor.rowcount

    def _select_sql(self) -> tuple[str, tuple[Any, ...]]:
        columns = ", ".join(quote_identifier(name) for name in self.model.__fields__)
        table = quote_identifier(self.model.__tablename__)
        where, params = self._where_sql()
        # Identifiers are validated above; predicates only contain placeholders.
        sql = f"SELECT {columns} FROM {table}{where}"  # nosec B608
        if self.ordering:
            sql += f" ORDER BY {self.ordering}"
        if self.limit_value is not None:
            sql += f" LIMIT {self.limit_value}"
        return sql, params

    def _where_sql(self) -> tuple[str, tuple[Any, ...]]:
        return (
            (" WHERE " + " AND ".join(self.where)) if self.where else "",
            self.params,
        )

    def _clone(self, **changes: Any) -> Query[ModelT]:
        values = {
            "where": self.where,
            "params": self.params,
            "ordering": self.ordering,
            "limit_value": self.limit_value,
            **changes,
        }
        return Query(self.session, self.model, **values)


class Session:
    def __init__(self, database: Database, connection: sqlite3.Connection) -> None:
        self.database = database
        self.connection = connection

    def execute(
        self,
        sql: str,
        params: Iterable[Any] | Mapping[str, Any] = (),
    ) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def add(self, instance: Model) -> Model:
        model = type(instance)
        fields = []
        values = []
        for name, field in model.__fields__.items():
            value = getattr(instance, name)
            if field.primary_key and isinstance(field, Integer) and value is None:
                continue
            if value is None and not field.nullable:
                raise ValueError(f"{model.__name__}.{name} cannot be null")
            fields.append(name)
            values.append(field.to_database(value))

        columns = ", ".join(quote_identifier(name) for name in fields)
        placeholders = ", ".join("?" for _ in fields)
        table = quote_identifier(model.__tablename__)
        # All identifiers pass quote_identifier; all values use placeholders.
        cursor = self.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",  # nosec B608
            values,
        )
        primary_key = model.__primary_key__
        if (
            primary_key is not None
            and isinstance(primary_key, Integer)
            and getattr(instance, primary_key.name) is None
        ):
            setattr(instance, primary_key.name, cursor.lastrowid)
        return instance

    def save(self, instance: Model) -> Model:
        model = type(instance)
        primary_key = model.__primary_key__
        if primary_key is None:
            raise TypeError(f"{model.__name__} does not define a primary key")
        primary_value = getattr(instance, primary_key.name)
        if primary_value is None:
            return self.add(instance)

        fields = [
            (name, field) for name, field in model.__fields__.items() if field is not primary_key
        ]
        assignments = ", ".join(f"{quote_identifier(name)} = ?" for name, _ in fields)
        values = [field.to_database(getattr(instance, name)) for name, field in fields]
        values.append(primary_key.to_database(primary_value))
        table = quote_identifier(model.__tablename__)
        # All identifiers pass quote_identifier; all values use placeholders.
        self.execute(
            f"UPDATE {table} SET {assignments} "  # nosec B608
            f"WHERE {quote_identifier(primary_key.name)} = ?",
            values,
        )
        return instance

    def delete(self, instance: Model) -> None:
        model = type(instance)
        primary_key = model.__primary_key__
        if primary_key is None:
            raise TypeError(f"{model.__name__} does not define a primary key")
        self.query(model).filter_by(
            **{primary_key.name: getattr(instance, primary_key.name)}
        ).delete()

    def query(self, model: type[ModelT]) -> Query[ModelT]:
        return Query(self, model)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()


class Database:
    def __init__(self, url: str = "sqlite:///higuma.db") -> None:
        self.url = url
        self.path = _sqlite_path(url)
        self._memory_lock = RLock()
        self._memory_connection: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self.path == ":memory:" and self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(
            self.path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        else:
            self._memory_connection = connection
        return connection

    @contextmanager
    def session(self) -> Iterator[Session]:
        if self.path == ":memory:":
            self._memory_lock.acquire()
        connection = self.connect()
        session = Session(self, connection)
        try:
            yield session
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            if self.path != ":memory:":
                connection.close()
            else:
                self._memory_lock.release()

    def create_all(self, *models: type[Model]) -> None:
        with self.session() as session:
            for model in models:
                if not model.__fields__:
                    raise TypeError(f"{model.__name__} does not define any fields")
                columns = ", ".join(field.ddl() for field in model.__fields__.values())
                table = quote_identifier(model.__tablename__)
                session.execute(f"CREATE TABLE IF NOT EXISTS {table} ({columns})")
                for name, field in model.__fields__.items():
                    if field.index:
                        index = quote_identifier(f"idx_{model.__tablename__}_{name}")
                        column = quote_identifier(name)
                        session.execute(
                            f"CREATE INDEX IF NOT EXISTS {index} ON {table} ({column})"
                        )

    def drop_all(self, *models: type[Model]) -> None:
        with self.session() as session:
            for model in reversed(models):
                session.execute(
                    f"DROP TABLE IF EXISTS {quote_identifier(model.__tablename__)}"
                )


def quote_identifier(value: str) -> str:
    if not value or not all(ch == "_" or ch.isalnum() for ch in value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return f'"{value}"'


def _field_for(model: type[Model], name: str) -> Field:
    try:
        return model.__fields__[name]
    except KeyError as exc:
        raise KeyError(f"unknown {model.__name__} field: {name}") from exc


def _row_to_model(model: type[ModelT], row: sqlite3.Row) -> ModelT:
    return model(
        **{name: field.from_database(row[name]) for name, field in model.__fields__.items()}
    )


def _sqlite_path(url: str) -> str:
    if url in {"sqlite://", "sqlite:///:memory:", ":memory:"}:
        return ":memory:"
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("built-in Database currently supports sqlite:/// URLs")
    path = url[len(prefix) :]
    if not path:
        raise ValueError("SQLite database path must not be empty")
    resolved = Path(path).expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return str(resolved)
