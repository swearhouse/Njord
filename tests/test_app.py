import json

import pytest

from njord import app as njord_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(njord_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(njord_app, "DATA_FILE", tmp_path / "data.json")
    monkeypatch.setattr(njord_app, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(njord_app, "SECRET_FILE", tmp_path / ".secret_key")
    client = njord_app.create_app().test_client()
    response = client.post("/login", data={"username": "admin", "password": "admin"})
    assert response.status_code == 302
    return client


def test_missing_data_file_is_created(client, tmp_path):
    assert json.loads((tmp_path / "data.json").read_text())["items"] == []


def test_login_required():
    anonymous = njord_app.create_app().test_client()
    assert anonymous.get("/").status_code == 302


@pytest.mark.parametrize("path", ["/", "/items", "/loans", "/payoff", "/options"])
def test_pages_render(client, path):
    assert client.get(path).status_code == 200


def test_download_data(client):
    response = client.get("/options/data")
    assert response.status_code == 200
    assert "currency" in json.loads(response.get_data(as_text=True))
    response.close()


def test_import_rejects_invalid_data():
    assert njord_app.validate_import_data({"currency": "", "items": []})
    assert njord_app.validate_import_data({"currency": "NOK", "items": []}) is None
