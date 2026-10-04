"""Uploading a recipe whose name already exists in Mealie must not fail."""

from mealie import Mealie
from recipe_exporter import RecipeExporter


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    """Mimics Mealie when a recipe called "Suppe" already exists."""

    def __init__(self):
        self.put_body = None

    def post(self, url, json=None, headers=None, **kw):
        return _Resp(201, "suppe-1")

    def get(self, url, headers=None, **kw):
        if "/api/units" in url or "/api/foods" in url:
            return _Resp(200, {"items": []})
        return _Resp(200, {"id": "abc", "slug": "suppe-1", "name": "Suppe (1)"})

    def put(self, url, json=None, headers=None, **kw):
        self.put_body = json
        if json["name"] == "Suppe":
            return _Resp(400, {"detail": "Recipe already exists"})
        return _Resp(200, json)


def test_keeps_deduplicated_name_on_update():
    client = Mealie.__new__(Mealie)
    RecipeExporter.__init__(client, api_key="t", base_url="http://mealie", name="Mealie")
    client._session = _Session()

    result = client.create_recipe({"name": "Suppe", "recipeIngredients": [], "recipeInstructions": []})

    assert client._session.put_body["name"] == "Suppe (1)"
    assert result["name"] == "Suppe (1)"
