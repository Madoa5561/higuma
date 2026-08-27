from higuma import Higuma, MethodView, View, request

app = Higuma(__name__)


class HealthView(View):
    methods = ("GET",)

    def dispatch_request(self):
        return {"status": "ok"}


class ItemsView(MethodView):
    def get(self):
        return {"items": []}

    def post(self):
        return {"item": request.json}, 201


app.add_url_rule("/health", endpoint="health", view_func=HealthView.as_view("health"))
app.add_url_rule("/items", endpoint="items", view_func=ItemsView.as_view("items"))


if __name__ == "__main__":
    app.run()
