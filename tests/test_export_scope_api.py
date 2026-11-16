from arno.ledger.export import SECRET_TABLES


def test_export_scope_reports_the_actual_excluded_tables(authed_client):
    response = authed_client.get("/api/export/scope")

    assert response.status_code == 200
    assert response.json() == {"excluded_tables": sorted(SECRET_TABLES)}


def test_export_scope_requires_a_session(client):
    assert client.get("/api/export/scope").status_code == 401
