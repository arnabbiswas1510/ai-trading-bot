import pytest

import research_storage as storage


@pytest.fixture(autouse=True)
def config(monkeypatch):
    for name in (
        "RESEARCH_STORAGE_BACKEND", "RESEARCH_DATABASE_URL", "RESEARCH_DATABASE_KEY",
        "SUPABASE_URL", "INTRADAY_SUPABASE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_legacy_uses_private_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("INTRADAY_SUPABASE_KEY", "private")
    assert storage.backend() == "supabase"
    assert storage.research_rest_url() == "https://example.supabase.co/rest/v1"
    assert storage.research_headers()["Authorization"] == "Bearer private"


def test_local_never_falls_back_to_cloud(monkeypatch):
    monkeypatch.setenv("RESEARCH_STORAGE_BACKEND", "local")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("INTRADAY_SUPABASE_KEY", "cloud-key")
    with pytest.raises(ValueError, match="RESEARCH_DATABASE_URL"):
        storage.research_url()
    with pytest.raises(ValueError, match="RESEARCH_DATABASE_KEY"):
        storage.research_key()


def test_local_origin_has_no_supabase_prefix(monkeypatch):
    monkeypatch.setenv("RESEARCH_STORAGE_BACKEND", "local")
    monkeypatch.setenv("RESEARCH_DATABASE_URL", "http://research-api:3000/")
    monkeypatch.setenv("RESEARCH_DATABASE_KEY", "local-key")
    assert storage.research_rest_url() == "http://research-api:3000"
    assert storage.research_key() == "local-key"


@pytest.mark.parametrize("url", [
    "https://example.supabase.co", "http://user:secret@host:3000",
    "file:///tmp/db", "https://host/rest/v1", "http://host/?secret=1",
])
def test_local_rejects_wrong_origins(monkeypatch, url):
    monkeypatch.setenv("RESEARCH_STORAGE_BACKEND", "local")
    monkeypatch.setenv("RESEARCH_DATABASE_URL", url)
    with pytest.raises(ValueError):
        storage.research_url()


def test_bad_backend_cannot_silently_select_supabase(monkeypatch):
    monkeypatch.setenv("RESEARCH_STORAGE_BACKEND", "filesystem")
    with pytest.raises(ValueError, match="RESEARCH_STORAGE_BACKEND"):
        storage.research_url()


def test_existing_trading_key_is_not_private_research_access(monkeypatch):
    monkeypatch.setenv("SUPABASE_KEY", "live")
    with pytest.raises(ValueError, match="INTRADAY_SUPABASE_KEY"):
        storage.research_key()


def test_local_client_uses_real_postgrest_api(monkeypatch):
    import httpx

    monkeypatch.setenv("RESEARCH_STORAGE_BACKEND", "local")
    monkeypatch.setenv("RESEARCH_DATABASE_URL", "http://research-api:3000")
    monkeypatch.setenv("RESEARCH_DATABASE_KEY", "local-key")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    client = storage.research_client(timeout=3)
    client.session.close()
    client.session = httpx.Client(
        base_url=storage.research_rest_url(), headers=storage.research_headers(),
        transport=httpx.MockTransport(handle),
    )
    try:
        client.table("intraday_shadow_events").select("id").execute()
        client.rpc("intraday_calibration_mutate", {
            "p_operation": "settings", "p_payload": {},
        }).execute()
    finally:
        client.session.close()
    assert [request.url.path for request in requests] == [
        "/intraday_shadow_events", "/rpc/intraday_calibration_mutate",
    ]
    assert all(request.headers["authorization"] == "Bearer local-key" for request in requests)
