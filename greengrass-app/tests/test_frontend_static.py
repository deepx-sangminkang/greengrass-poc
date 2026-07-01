from fastapi.testclient import TestClient

from backend.config import FRONTEND_DIR
from backend.main import app


def test_root_serves_frontend():
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "DX Runtime Greengrass Web POC" in response.text


def test_frontend_assets_are_served_with_no_cache_headers():
    client = TestClient(app)

    for path in ("/", "/app.js", "/index.html"):
        response = client.get(path)

        assert response.status_code == 200
        assert "no-cache" in response.headers.get("cache-control", "")


def test_frontend_contains_core_device_selection():
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    index_html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    assert "selectCoreDevice" in app_js
    assert "selected-core-output" in index_html


def test_frontend_contains_manual_core_device_install_guide():
    index_html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    assert "Manual installation guide" in index_html
    assert "Generate install script" in index_html
    assert "run it on the core device terminal" in index_html
    assert "--provision true" in index_html


def test_frontend_explains_java_and_credential_prerequisites():
    index_html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    assert "Java" in index_html
    assert "default-jdk" in index_html
    assert "Configure AWS credentials in advance" in index_html
    assert "sudo -E" in index_html
    assert "Token Exchange Service" in index_html


def test_frontend_provides_copyable_manual_commands():
    index_html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="manual-install-commands"' in index_html
    assert 'onclick="copyManualCommands()"' in index_html
    assert "greengrass-nucleus-latest.zip" in index_html
    assert "systemctl status greengrass.service" in index_html
    assert "function copyManualCommands" in app_js
    assert "navigator.clipboard.writeText" in app_js


def test_frontend_contains_stack_deploy_button():
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    index_html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    assert "deploySetupStack" in app_js
    assert "requestJson('/setup/deploy'" in app_js
    assert 'onclick="deploySetupStack()"' in index_html
    assert "Create/Update stack" in index_html


def test_frontend_supports_stack_selection():
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    index_html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="stack-name"' in index_html
    assert 'id="stack-list"' in index_html
    assert 'onclick="loadStacks()"' in index_html
    assert 'onclick="selectStack()"' in index_html
    assert "function loadStacks" in app_js
    assert "function selectStack" in app_js
    assert "requestJson('/setup/stacks')" in app_js
    assert "requestJson('/setup/select'" in app_js





def test_frontend_component_step_shows_thing_group_device_status():
    index_html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="component-thing-group"' in index_html
    assert 'id="device-components"' in index_html
    assert 'onclick="loadThingGroups()"' in index_html
    assert 'onclick="loadDeviceComponents()"' in index_html
    assert "function loadThingGroups" in app_js
    assert "function loadDeviceComponents" in app_js
    assert "requestJson('/thing-groups')" in app_js
    assert "/components`" in app_js


def test_frontend_preserves_structured_error_details():
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert "formatRequestError" in app_js
    assert "JSON.stringify(data.detail" in app_js


def test_frontend_surfaces_non_detail_error_bodies():
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert "data.error" in app_js
    assert "data.message" in app_js


def test_frontend_retries_once_on_csrf_failure():
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert "csrfToken = null" in app_js
    assert "response.status === 403" in app_js


def test_frontend_contains_optional_ssh_port_field():
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    index_html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="ssh-port"' in index_html
    assert "SSH port" in index_html
    assert "document.getElementById('ssh-port').value" in app_js
    assert "port:" in app_js


def test_frontend_streams_ssh_install_output_live():
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert "/devices/ssh-install-stream" in app_js
    assert "response.body.getReader()" in app_js
    assert "TextDecoder" in app_js


