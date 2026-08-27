import asyncio

from higuma import Higuma, request

app = Higuma(__name__)


@app.get("/articles/<int:article_id>")
async def article(article_id: int):
    await asyncio.sleep(0)
    return {
        "id": article_id,
        "preview": request.args.get("preview") == "1",
    }


@app.get("/measurements/<float:value>")
def measurement(value: float):
    return {"value": value}


@app.get("/assets/<path:resource>")
def asset_path(resource: str):
    return {"resource": resource}


@app.route("/resource", methods=("GET", "POST"))
def resource():
    return {"method": request.method}


if __name__ == "__main__":
    app.run()
