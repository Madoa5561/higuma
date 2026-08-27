from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from higuma import Body, Depends, Header, Higuma, PathParam, QueryParam

app = Higuma(__name__)
app.config["OPENAPI_TITLE"] = "Typed higuma API"


@dataclass
class ItemInput:
    name: str
    price: float


@dataclass
class ItemOutput:
    id: UUID
    name: str
    price: float
    view: Literal["summary", "full"]


def selected_view(
    view: Annotated[Literal["summary", "full"], QueryParam()] = "summary",
) -> Literal["summary", "full"]:
    return view


@app.post(
    "/items/<uuid:item_id>",
    response_model=ItemOutput,
    status_code=201,
    tags=("items",),
)
def create_item(
    item_id: Annotated[UUID, PathParam(description="Client-generated item ID")],
    payload: Annotated[ItemInput, Body(description="Item to create")],
    view: Annotated[Literal["summary", "full"], Depends(selected_view)],
    trace_id: Annotated[str | None, Header("x-trace-id")] = None,
) -> ItemOutput:
    # Returning a dict is fine: response_model validates and filters it.
    return {
        "id": item_id,
        "name": payload.name,
        "price": payload.price,
        "view": view,
        "trace_id": trace_id,  # Removed from the response by response_model.
    }


if __name__ == "__main__":
    app.run()
