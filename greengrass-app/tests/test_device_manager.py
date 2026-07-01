from backend.device_manager import build_install_script


def test_build_install_script_contains_greengrass_provision_command():
    script = build_install_script(
        region="ap-northeast-2",
        thing_name="DeepxCore01",
        thing_group_name="DeepxGreengrassCores",
        token_exchange_role_name="GreengrassTokenExchangeRole",
        component_default_user="ggc_user:ggc_group",
    )

    assert "--aws-region ap-northeast-2" in script
    assert "--thing-name DeepxCore01" in script
    assert "--thing-group-name DeepxGreengrassCores" in script
    assert "--provision true" in script
    assert "AWS_SECRET_ACCESS_KEY" not in script


def test_build_install_script_retries_provisioning_on_transient_failure():
    script = build_install_script(
        region="ap-northeast-2",
        thing_name="DeepxCore01",
        thing_group_name="DeepxGreengrassCores",
        token_exchange_role_name="GreengrassTokenExchangeRole",
    )

    assert "until sudo -E java" in script
    assert "retrying in" in script
    assert "sleep" in script


def test_build_install_script_retries_provisioning_with_credentials():
    script = build_install_script(
        region="ap-northeast-2",
        thing_name="DeepxCore01",
        thing_group_name="DeepxGreengrassCores",
        token_exchange_role_name="GreengrassTokenExchangeRole",
        aws_credentials={
            "access_key": "AKIATESTKEY",
            "secret_key": "secretvalue",
        },
    )

    assert "until java" in script
    assert "retrying in" in script


def test_build_install_script_rejects_shell_metacharacters():
    try:
        build_install_script(
            region="ap-northeast-2",
            thing_name="DeepxCore01; rm -rf /",
            thing_group_name="DeepxGreengrassCores",
            token_exchange_role_name="GreengrassTokenExchangeRole",
        )
    except ValueError as error:
        assert "thing_name" in str(error)
        assert "허용되지 않는 문자" in str(error)
    else:
        raise AssertionError("Expected unsafe thing_name to be rejected")


def test_build_install_script_injects_credentials_without_sudo_dash_e():
    script = build_install_script(
        region="ap-northeast-2",
        thing_name="DeepxCore01",
        thing_group_name="DeepxGreengrassCores",
        token_exchange_role_name="GreengrassTokenExchangeRole",
        aws_credentials={
            "access_key": "AKIATESTKEY",
            "secret_key": "secretvalue",
            "session_token": "sessiontoken",
        },
    )

    assert "sudo -E" not in script
    assert "sudo bash" in script
    assert "export AWS_ACCESS_KEY_ID=AKIATESTKEY" in script
    assert "export AWS_SECRET_ACCESS_KEY=secretvalue" in script
    assert "export AWS_SESSION_TOKEN=sessiontoken" in script
    assert "--provision true" in script


def test_build_install_script_omits_session_token_when_absent():
    script = build_install_script(
        region="ap-northeast-2",
        thing_name="DeepxCore01",
        thing_group_name="DeepxGreengrassCores",
        token_exchange_role_name="GreengrassTokenExchangeRole",
        aws_credentials={
            "access_key": "AKIATESTKEY",
            "secret_key": "secretvalue",
        },
    )

    assert "export AWS_ACCESS_KEY_ID=AKIATESTKEY" in script
    assert "AWS_SESSION_TOKEN" not in script


def test_build_install_script_uses_sudo_password_non_interactively():
    script = build_install_script(
        region="ap-northeast-2",
        thing_name="DeepxCore01",
        thing_group_name="DeepxGreengrassCores",
        token_exchange_role_name="GreengrassTokenExchangeRole",
        sudo_password="devicepass",
    )

    assert "export DX_SUDO_PASSWORD=devicepass" in script
    assert "sudo -S" in script
    assert '<<< "$DX_SUDO_PASSWORD"' in script
    assert "sudo -E java" not in script


def test_build_install_script_without_password_uses_plain_sudo():
    script = build_install_script(
        region="ap-northeast-2",
        thing_name="DeepxCore01",
        thing_group_name="DeepxGreengrassCores",
        token_exchange_role_name="GreengrassTokenExchangeRole",
    )

    assert "DX_SUDO_PASSWORD" not in script
    assert "sudo -S" not in script
    assert "sudo -E java" in script


def test_build_install_script_credentialed_with_sudo_password():
    script = build_install_script(
        region="ap-northeast-2",
        thing_name="DeepxCore01",
        thing_group_name="DeepxGreengrassCores",
        token_exchange_role_name="GreengrassTokenExchangeRole",
        aws_credentials={
            "access_key": "AKIATESTKEY",
            "secret_key": "secretvalue",
        },
        sudo_password="devicepass",
    )

    assert "export DX_SUDO_PASSWORD=devicepass" in script
    assert "sudo -S" in script
    assert '<<< "$DX_SUDO_PASSWORD"' in script
    assert "export AWS_ACCESS_KEY_ID=AKIATESTKEY" in script


class _FakePaginator:
    def __init__(self, pages, captured):
        self._pages = pages
        self._captured = captured

    def paginate(self, **kwargs):
        self._captured.update(kwargs)
        return list(self._pages)


class _FakeClient:
    def __init__(self, pages):
        self._pages = pages
        self.captured = {}

    def get_paginator(self, name):
        return _FakePaginator(self._pages, self.captured)


def test_list_core_devices_filters_by_thing_group_arn():
    from backend.device_manager import list_core_devices

    client = _FakeClient(
        [{"coreDevices": [{"coreDeviceThingName": "DeepxCore01", "status": "HEALTHY"}]}]
    )

    devices = list_core_devices(client, thing_group_arn="arn:aws:iot:...:thinggroup/g")

    assert devices[0]["coreDeviceThingName"] == "DeepxCore01"
    assert client.captured["thingGroupArn"].endswith("thinggroup/g")


def test_list_core_devices_without_group_does_not_filter():
    from backend.device_manager import list_core_devices

    client = _FakeClient([{"coreDevices": []}])

    list_core_devices(client)

    assert "thingGroupArn" not in client.captured


def test_list_thing_groups_returns_names_and_arns():
    from backend.device_manager import list_thing_groups

    client = _FakeClient([{"thingGroups": [{"groupName": "g1", "groupArn": "arn:g1"}]}])

    groups = list_thing_groups(client)

    assert groups == [{"groupName": "g1", "groupArn": "arn:g1"}]


def test_list_installed_components_passes_thing_name():
    from backend.device_manager import list_installed_components

    client = _FakeClient(
        [
            {
                "installedComponents": [
                    {
                        "componentName": "com.deepx.dx_stream",
                        "componentVersion": "1.0.6",
                        "lifecycleState": "RUNNING",
                        "isRoot": True,
                    }
                ]
            }
        ]
    )

    components = list_installed_components(client, "DeepxCore01")

    assert components[0]["componentName"] == "com.deepx.dx_stream"
    assert components[0]["lifecycleState"] == "RUNNING"
    assert client.captured["coreDeviceThingName"] == "DeepxCore01"


class _FakePaginatorByName:
    def __init__(self, pages_by_name, captured_by_name, name):
        self._pages_by_name = pages_by_name
        self._captured_by_name = captured_by_name
        self._name = name

    def paginate(self, **kwargs):
        self._captured_by_name[self._name] = kwargs
        return list(self._pages_by_name.get(self._name, []))


class _FakeMultiClient:
    def __init__(self, pages_by_name):
        self._pages_by_name = pages_by_name
        self.captured = {}

    def get_paginator(self, name):
        return _FakePaginatorByName(self._pages_by_name, self.captured, name)


def test_list_things_in_thing_group_returns_member_names():
    from backend.device_manager import list_things_in_thing_group

    client = _FakeMultiClient(
        {"list_things_in_thing_group": [{"things": ["Test3Core01", "Test3Core02"]}]}
    )

    members = list_things_in_thing_group(client, "DxGroup-cores")

    assert members == {"Test3Core01", "Test3Core02"}
    assert client.captured["list_things_in_thing_group"]["thingGroupName"] == "DxGroup-cores"


def test_list_core_devices_in_group_filters_by_membership_not_deployment():
    from backend.device_manager import list_core_devices_in_group

    greengrass = _FakeMultiClient(
        {
            "list_core_devices": [
                {
                    "coreDevices": [
                        {"coreDeviceThingName": "Test3Core01", "status": "HEALTHY"},
                        {"coreDeviceThingName": "OtherCore", "status": "HEALTHY"},
                    ]
                }
            ]
        }
    )
    iot = _FakeMultiClient(
        {"list_things_in_thing_group": [{"things": ["Test3Core01"]}]}
    )

    devices = list_core_devices_in_group(
        greengrass, iot, "arn:aws:iot:ap-northeast-2:1:thinggroup/DxGroup-cores"
    )

    assert [d["coreDeviceThingName"] for d in devices] == ["Test3Core01"]
    assert "thingGroupArn" not in greengrass.captured.get("list_core_devices", {})
    assert iot.captured["list_things_in_thing_group"]["thingGroupName"] == "DxGroup-cores"
