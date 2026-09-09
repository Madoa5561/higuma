from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, TypedDict

import pytest
from higuma import Body, Higuma
from higuma.parameters import ResponseValidationError, coerce_response_model


@dataclass
class PublicUser:
    name: str


@dataclass
class PrivateUser(PublicUser):
    password: str


class UserEnvelope(TypedDict):
    users: list[PublicUser]


@dataclass
class Quantity:
    value: Annotated[int, Body(ge=1)]


@dataclass
class ComputedValue:
    value: int
    doubled: int = field(init=False)

    def __post_init__(self):
        self.doubled = self.value * 2


@dataclass(frozen=True)
class UserLabel:
    user: PublicUser
    label: str = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "label", self.user.name.upper())


def test_response_filters_subclass_fields_recursively_without_mutation():
    user = PrivateUser("bear", "private-password")
    assert coerce_response_model(user, PublicUser) == {"name": "bear"}
    assert coerce_response_model({"users": [user]}, UserEnvelope) == {"users": [{"name": "bear"}]}
    assert user.password == "private-password"
    assert coerce_response_model([user, None], list[PublicUser | None]) == [{"name": "bear"}, None]


def test_response_filters_nested_mapping_fields():
    assert coerce_response_model(
        {"users": [{"name": "bear", "password": "private"}], "secret": "hidden"},
        UserEnvelope,
    ) == {"users": [{"name": "bear"}]}


def test_response_revalidates_dataclass_instances():
    with pytest.raises(ResponseValidationError):
        coerce_response_model(PublicUser(123), PublicUser)
    with pytest.raises(ResponseValidationError):
        coerce_response_model(Quantity(0), Quantity)


def test_response_preserves_declared_computed_fields():
    assert coerce_response_model({"value": "3"}, ComputedValue) == {"value": 3, "doubled": 6}
    assert coerce_response_model({"user": {"name": "bear", "password": "hidden"}}, UserLabel) == {
        "user": {"name": "bear"},
        "label": "BEAR",
    }


def test_nested_constraints_and_float_overflow_return_422():
    app = Higuma(__name__, static_folder=None)

    @app.post("/quantity")
    def quantity(payload: Annotated[Quantity, Body()]):
        return {"value": payload.value}

    @app.post("/number")
    def number(payload: Annotated[float, Body()]):
        return {"value": payload}

    client = app.test_client()
    assert client.post("/quantity", json={"value": 0}).status_code == 422
    assert client.post("/quantity", json={"value": "2"}).json == {"value": 2}
    assert client.post("/quantity", json={"value": 2, "extra": 1}).status_code == 422
    assert client.post("/number", json=10**400).status_code == 422
    assert client.post("/number", json=3).json == {"value": 3.0}


def test_response_validation_at_route_boundary():
    app = Higuma(__name__, static_folder=None)

    @app.get("/user", response_model=PublicUser)
    def user():
        return PrivateUser("bear", "private-password")

    @app.get("/invalid", response_model=PublicUser)
    def invalid():
        return PrivateUser(123, "private-password")

    client = app.test_client()
    assert client.get("/user").json == {"name": "bear"}
    response = client.get("/invalid")
    assert response.status_code == 500
    assert "private-password" not in response.text
