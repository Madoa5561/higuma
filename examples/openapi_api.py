from dataclasses import dataclass

from higuma import Higuma, request

app = Higuma(__name__)
app.config["OPENAPI_TITLE"] = "Higuma example API"
app.config["OPENAPI_DESCRIPTION"] = "Automatic OpenAPI 3.1 example."


@dataclass
class User:
    id: int
    name: str


@app.get("/users/<int:user_id>", tags=("users",), summary="Get one user")
def get_user(user_id: int) -> User:
    return {"id": user_id, "name": "Higuma"}


@app.post(
    "/users",
    tags=("users",),
    summary="Create a user",
    request_body=User,
    responses={"201": {"description": "User created"}},
)
def create_user():
    return request.json, 201


if __name__ == "__main__":
    app.run()
