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


def test_loan_one_time_payment(client, tmp_path):
    path = tmp_path / 'data.json'
    data = json.loads(path.read_text())
    data['loans'] = [{'id': 'l1', 'name': 'L', 'balance': 10000.0, 'rate': 5.0, 'payment': 500.0}]
    path.write_text(json.dumps(data))
    for query in ('lump=2000', 'lump=20000', 'lump=1000&extra=100'):
        response = client.get('/loans/l1?' + query)
        assert response.status_code == 200, query
        assert 'Interest to pay' in response.get_data(as_text=True)


def test_item_frequency(client, tmp_path):
    base = {'name': 'Insurance', 'type': 'expense', 'amount': '1200', 'category': '', 'asset_id': ''}
    assert client.post('/items', data={**base, 'every': '12'}).status_code == 302
    assert client.post('/items', data={**base, 'every': '5'}).status_code == 302
    items = json.loads((tmp_path / 'data.json').read_text())['items']
    assert [i['every'] for i in items] == [12]
    page = client.get('/').get_data(as_text=True)
    assert '100.00' in page and 'yearly' in page
    assert client.get('/items').status_code == 200


def test_projection_uses_paid_month(client, tmp_path):
    base = {'name': 'Insurance', 'type': 'expense', 'amount': '1200', 'category': '', 'asset_id': '', 'every': '12'}
    assert client.post('/items', data={**base, 'start': '2026-13'}).status_code == 302
    assert client.post('/items', data={**base, 'start': '2020-03'}).status_code == 302
    items = json.loads((tmp_path / 'data.json').read_text())['items']
    assert [i['start'] for i in items] == ['2020-03']
    rows = njord_app.project(items, [], 24, today=njord_app.date(2026, 10, 15))
    paid = [r['label'] for r in rows if r['expenses']]
    assert paid == ['March 2027', 'March 2028']
    assert client.get('/projection').status_code == 200
    assert client.get('/projection?months=60').status_code == 200
