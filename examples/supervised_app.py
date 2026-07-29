from higuma import Higuma

app = Higuma(__name__)


@app.get("/")
def index():
    return {"message": "served by a supervised higuma worker"}


if __name__ == "__main__":
    app.run(processes=4, app_ref="supervised_app:app")

# Recommended from the examples directory:
# higuma run supervised_app:app --processes 4
