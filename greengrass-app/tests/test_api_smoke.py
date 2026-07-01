from fastapi.testclient import TestClient

import pytest

from backend import config
from backend.main import app


@pytest.fixture(autouse=True)
def _reset_active_stack(monkeypatch):
    monkeypatch.setattr("backend.main._active_stack_name", config.STACK_NAME, raising=False)


def test_session_endpoint_returns_csrf_token():
    response = TestClient(app).get("/api/session")

    assert response.status_code == 200
    assert response.json()["csrfToken"]


def test_default_component_version_matches_deployed_runtime_recipe():
    assert config.DEFAULT_COMPONENT_VERSION == "1.0.7"


def test_install_script_requires_csrf_token():
    response = TestClient(app).post(
        "/api/devices/install-script",
        json={
            "thing_name": "DeepxCore01",
            "token_exchange_role_name": "GreengrassTokenExchangeRole",
        },
    )

    assert response.status_code == 403


def test_cors_does_not_allow_untrusted_origin():
    response = TestClient(app).get(
        "/api/health",
        headers={"Origin": "https://evil.example.com"},
    )

    assert response.headers.get("access-control-allow-origin") != "*"


def test_setup_status_endpoint_exists(monkeypatch):
    monkeypatch.setattr("backend.main.create_client", lambda service_name: object())
    monkeypatch.setattr(
        "backend.main.get_stack_status",
        lambda cloudformation_client, stack_name: {
            "name": stack_name,
            "status": "CREATE_COMPLETE",
            "ready": True,
            "outputs": {},
        },
    )

    response = TestClient(app).get("/api/setup/status")

    assert response.status_code == 200


def test_setup_deploy_creates_cloudformation_stack_with_csrf(monkeypatch):
    captured = {}

    def fake_deploy_stack(**kwargs):
        captured.update(kwargs)
        return {
            "status": "succeeded",
            "action": "create",
            "stackStatus": "CREATE_COMPLETE",
            "outputs": {"ArtifactBucketName": "bucket-123"},
        }

    monkeypatch.setattr("backend.main.deploy_stack", fake_deploy_stack)
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/setup/deploy",
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["outputs"]["ArtifactBucketName"] == "bucket-123"
    assert captured["stack_name"] == config.STACK_NAME
    assert "AWSTemplateFormatVersion" in captured["template_body"]
    assert "KinesisVideoStreamName" not in captured["parameters"]


def test_setup_deploy_uses_custom_stack_name_and_activates_it(monkeypatch):
    captured = {}

    def fake_deploy_stack(**kwargs):
        captured.update(kwargs)
        return {"status": "succeeded", "action": "create", "outputs": {}}

    monkeypatch.setattr("backend.main.deploy_stack", fake_deploy_stack)
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/setup/deploy",
        headers={"X-CSRF-Token": token},
        json={"stack_name": "MyCustomStack02"},
    )

    assert response.status_code == 200
    assert response.json()["stackName"] == "MyCustomStack02"
    assert captured["stack_name"] == "MyCustomStack02"

    import backend.main as main_module

    assert main_module.get_active_stack_name() == "MyCustomStack02"


def test_setup_deploy_rejects_invalid_stack_name(monkeypatch):
    monkeypatch.setattr("backend.main.deploy_stack", lambda **_: pytest.fail("should not deploy"))
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/setup/deploy",
        headers={"X-CSRF-Token": token},
        json={"stack_name": "1-invalid name"},
    )

    assert response.status_code == 400


def test_setup_stacks_lists_existing_stacks(monkeypatch):
    monkeypatch.setattr("backend.main.create_client", lambda service_name: object())
    monkeypatch.setattr(
        "backend.main.list_stacks",
        lambda client: [
            {"name": "stack-a", "status": "CREATE_COMPLETE", "ready": True, "managed": True},
            {"name": "stack-b", "status": "CREATE_COMPLETE", "ready": True, "managed": False},
        ],
    )

    response = TestClient(app).get("/api/setup/stacks")

    assert response.status_code == 200
    body = response.json()
    assert body["activeStackName"] == config.STACK_NAME
    assert [stack["name"] for stack in body["stacks"]] == ["stack-a", "stack-b"]


def test_setup_select_activates_existing_stack(monkeypatch):
    monkeypatch.setattr("backend.main.create_client", lambda service_name: object())
    monkeypatch.setattr(
        "backend.main.get_stack_status",
        lambda cloudformation_client, stack_name: {
            "name": stack_name,
            "status": "CREATE_COMPLETE",
            "ready": True,
            "outputs": {},
        },
    )
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/setup/select",
        headers={"X-CSRF-Token": token},
        json={"stack_name": "PickedStack01"},
    )

    assert response.status_code == 200
    assert response.json()["activeStackName"] == "PickedStack01"

    import backend.main as main_module

    assert main_module.get_active_stack_name() == "PickedStack01"


def test_setup_select_returns_404_for_missing_stack(monkeypatch):
    monkeypatch.setattr("backend.main.create_client", lambda service_name: object())
    monkeypatch.setattr(
        "backend.main.get_stack_status",
        lambda cloudformation_client, stack_name: {
            "name": stack_name,
            "status": "NOT_FOUND",
            "ready": False,
            "outputs": {},
        },
    )
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/setup/select",
        headers={"X-CSRF-Token": token},
        json={"stack_name": "MissingStack01"},
    )

    assert response.status_code == 404


def test_setup_select_requires_csrf_token():
    response = TestClient(app).post(
        "/api/setup/select",
        json={"stack_name": "AnyStack01"},
    )

    assert response.status_code == 403


def test_setup_deploy_does_not_precreate_stream(monkeypatch):
    captured = {}

    def fake_deploy_stack(**kwargs):
        captured.update(kwargs)
        return {"status": "succeeded", "action": "update", "outputs": {}}

    monkeypatch.setattr("backend.main.deploy_stack", fake_deploy_stack)
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/setup/deploy",
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 200
    assert "CreateKinesisVideoStream" not in captured["parameters"]


def test_setup_deploy_requires_csrf_token():
    response = TestClient(app).post("/api/setup/deploy")

    assert response.status_code == 403


def test_setup_deploy_returns_error_output_on_failure(monkeypatch):
    def fake_deploy_stack(**_):
        return {"status": "failed", "action": "create", "stackStatus": "ROLLBACK_COMPLETE", "outputs": {}}

    monkeypatch.setattr("backend.main.deploy_stack", fake_deploy_stack)
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/setup/deploy",
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "failed"


def test_setup_deploy_surfaces_client_error(monkeypatch):
    from botocore.exceptions import ClientError

    def fake_deploy_stack(**_):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "not allowed"}},
            "CreateStack",
        )

    monkeypatch.setattr("backend.main.deploy_stack", fake_deploy_stack)
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/setup/deploy",
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 503
    assert "CloudFormation 배포 실패" in response.json()["detail"]["stderr"]


def test_install_script_endpoint_validates_payload():
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/devices/install-script",
        json={},
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 422


def test_install_script_uses_stack_thing_group_when_omitted(monkeypatch):
    monkeypatch.setattr(
        "backend.main.get_stack_outputs",
        lambda: {
            "ThingGroupName": "StackThingGroup",
            "TokenExchangeRoleName": "StackTokenRole",
        },
    )
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/devices/install-script",
        json={"thing_name": "DeepxCore01"},
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 200
    assert "--thing-group-name StackThingGroup" in response.json()["script"]
    assert "--tes-role-name StackTokenRole" in response.json()["script"]


def test_ssh_install_returns_korean_error_on_connection_failure(monkeypatch):
    import paramiko

    monkeypatch.setattr(
        "backend.main.get_stack_outputs",
        lambda: {
            "ThingGroupName": "StackThingGroup",
            "TokenExchangeRoleName": "StackTokenRole",
        },
    )

    def fail_ssh(**_):
        raise paramiko.SSHException("host key rejected")

    monkeypatch.setattr("backend.ssh_manager.run_script_over_ssh", fail_ssh)
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/devices/ssh-install",
        json={
            "thing_name": "DeepxCore01",
            "host": "192.0.2.10",
            "username": "ubuntu",
            "password": "secret",
        },
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 503
    assert "SSH 설치 실패" in response.json()["detail"]


def test_ssh_install_forwards_custom_port(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "backend.main.get_stack_outputs",
        lambda: {
            "ThingGroupName": "StackThingGroup",
            "TokenExchangeRoleName": "StackTokenRole",
        },
    )

    def fake_ssh(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "exitCode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr("backend.ssh_manager.run_script_over_ssh", fake_ssh)
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/devices/ssh-install",
        json={
            "thing_name": "DeepxCore01",
            "host": "192.0.2.10",
            "username": "ubuntu",
            "password": "secret",
            "port": 2222,
        },
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 200
    assert captured["port"] == 2222


def test_ssh_install_forwards_aws_credentials_into_script(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "backend.main.get_stack_outputs",
        lambda: {
            "ThingGroupName": "StackThingGroup",
            "TokenExchangeRoleName": "StackTokenRole",
        },
    )
    monkeypatch.setattr(
        "backend.main.get_aws_credentials",
        lambda: {
            "access_key": "AKIATESTKEY",
            "secret_key": "secretvalue",
            "session_token": "sessiontoken",
        },
    )

    def fake_ssh(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "exitCode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr("backend.ssh_manager.run_script_over_ssh", fake_ssh)
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/devices/ssh-install",
        json={
            "thing_name": "DeepxCore01",
            "host": "192.0.2.10",
            "username": "ubuntu",
            "password": "secret",
        },
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 200
    assert "export AWS_ACCESS_KEY_ID=AKIATESTKEY" in captured["script"]
    assert "sudo -E" not in captured["script"]


def test_ssh_install_stream_returns_live_output(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "backend.main.get_stack_outputs",
        lambda: {
            "ThingGroupName": "StackThingGroup",
            "TokenExchangeRoleName": "StackTokenRole",
        },
    )
    monkeypatch.setattr("backend.main.get_aws_credentials", lambda: None)

    def fake_stream(**kwargs):
        captured.update(kwargs)
        yield "step 1\n"
        yield "step 2\n"
        yield "[exitCode=0]\n"

    monkeypatch.setattr("backend.ssh_manager.stream_script_over_ssh", fake_stream)
    token = TestClient(app).get("/api/session").json()["csrfToken"]

    response = TestClient(app).post(
        "/api/devices/ssh-install-stream",
        json={
            "thing_name": "DeepxCore01",
            "host": "192.0.2.10",
            "username": "ubuntu",
            "password": "secret",
            "port": 16022,
        },
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "step 1" in response.text
    assert "step 2" in response.text
    assert "[exitCode=0]" in response.text
    assert captured["port"] == 16022


def test_thing_groups_endpoint_returns_groups(monkeypatch):
    monkeypatch.setattr("backend.main.create_client", lambda service_name: object())
    monkeypatch.setattr(
        "backend.main.list_thing_groups",
        lambda client: [{"groupName": "g1", "groupArn": "arn:g1"}],
    )

    response = TestClient(app).get("/api/thing-groups")

    assert response.status_code == 200
    assert response.json()["thingGroups"][0]["groupName"] == "g1"


def test_core_devices_endpoint_filters_by_thing_group_membership(monkeypatch):
    captured = {}

    def fake_list_core_devices_in_group(greengrass, iot, thing_group_arn):
        captured["thing_group_arn"] = thing_group_arn
        return [{"coreDeviceThingName": "DeepxCore01"}]

    monkeypatch.setattr("backend.main.create_client", lambda service_name: object())
    monkeypatch.setattr(
        "backend.main.list_core_devices_in_group", fake_list_core_devices_in_group
    )

    response = TestClient(app).get("/api/devices/cores", params={"thing_group_arn": "arn:g1"})

    assert response.status_code == 200
    assert captured["thing_group_arn"] == "arn:g1"
    assert response.json()["devices"][0]["coreDeviceThingName"] == "DeepxCore01"


def test_core_device_components_endpoint(monkeypatch):
    monkeypatch.setattr("backend.main.create_client", lambda service_name: object())
    monkeypatch.setattr(
        "backend.main.list_installed_components",
        lambda client, thing_name: [{"componentName": "com.deepx.dx_stream"}],
    )

    response = TestClient(app).get("/api/devices/DeepxCore01/components")

    assert response.status_code == 200
    assert response.json()["thingName"] == "DeepxCore01"
    assert response.json()["components"][0]["componentName"] == "com.deepx.dx_stream"


def test_core_device_components_rejects_invalid_thing_name(monkeypatch):
    monkeypatch.setattr("backend.main.create_client", lambda service_name: object())

    response = TestClient(app).get("/api/devices/bad%20name%3Brm/components")

    assert response.status_code == 400
