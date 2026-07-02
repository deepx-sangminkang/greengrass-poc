from validation import (
    shell_quote,
    validate_component_default_user,
    validate_iam_role_name,
    validate_iot_name,
    validate_region,
)


def build_install_script(
    region: str,
    thing_name: str,
    thing_group_name: str,
    token_exchange_role_name: str,
    component_default_user: str = "ggc_user:ggc_group",
    aws_credentials: dict | None = None,
    sudo_password: str | None = None,
) -> str:
    quoted_region = shell_quote(validate_region(region))
    quoted_thing_name = shell_quote(validate_iot_name("thing_name", thing_name))
    quoted_thing_group_name = shell_quote(validate_iot_name("thing_group_name", thing_group_name))
    quoted_role_name = shell_quote(validate_iam_role_name(token_exchange_role_name))
    quoted_default_user = shell_quote(validate_component_default_user(component_default_user))

    greengrass_args = f"""-Droot="/greengrass/v2" -Dlog.store=FILE \\
  -jar ./GreengrassInstaller/lib/Greengrass.jar \\
  --aws-region {quoted_region} \\
  --thing-name {quoted_thing_name} \\
  --thing-group-name {quoted_thing_group_name} \\
  --tes-role-name {quoted_role_name} \\
  --component-default-user {quoted_default_user} \\
  --provision true \\
  --setup-system-service true \\
  --deploy-dev-tools true"""

    sudo_export = ""
    if sudo_password:
        sudo_export = f"export DX_SUDO_PASSWORD={shell_quote(sudo_password)}\n\n"

    def sudo_run(command: str) -> str:
        if sudo_password:
            return f'sudo -S -p "" {command} <<< "$DX_SUDO_PASSWORD"'
        return f"sudo {command}"

    if aws_credentials:
        provision_block = _credentialed_provision_block(greengrass_args, aws_credentials, sudo_run)
    else:
        provision_block = _with_retry(sudo_run(f"-E java {greengrass_args}"))

    status_command = sudo_run("systemctl status greengrass.service --no-pager")

    return f"""#!/usr/bin/env bash
set -euo pipefail

{sudo_export}if ! command -v java >/dev/null 2>&1; then
  echo "Java is required before installing AWS IoT Greengrass Core."
  exit 1
fi

curl -s https://d2s8p88vqu9w66.cloudfront.net/releases/greengrass-nucleus-latest.zip > greengrass-nucleus-latest.zip
rm -rf GreengrassInstaller
unzip -q greengrass-nucleus-latest.zip -d GreengrassInstaller

{provision_block}

{status_command}
"""


def _with_retry(command: str, attempts: int = 5, delay_seconds: int = 10) -> str:
    return f"""DX_PROVISION_ATTEMPT=0
until {command}; do
  DX_PROVISION_ATTEMPT=$((DX_PROVISION_ATTEMPT + 1))
  if [ "$DX_PROVISION_ATTEMPT" -ge {attempts} ]; then
    echo "Greengrass provisioning failed after {attempts} attempts (likely a transient network/firewall connection reset)." >&2
    exit 1
  fi
  echo "Provisioning attempt failed, retrying in {delay_seconds}s... ($DX_PROVISION_ATTEMPT/{attempts})" >&2
  sleep {delay_seconds}
done"""


def _credentialed_provision_block(greengrass_args: str, aws_credentials: dict, sudo_run) -> str:
    exports = [
        f"export AWS_ACCESS_KEY_ID={shell_quote(aws_credentials['access_key'])}",
        f"export AWS_SECRET_ACCESS_KEY={shell_quote(aws_credentials['secret_key'])}",
    ]
    session_token = aws_credentials.get("session_token")
    if session_token:
        exports.append(f"export AWS_SESSION_TOKEN={shell_quote(session_token)}")
    export_lines = "\n".join(exports)

    provision_with_retry = _with_retry(f"java {greengrass_args}")

    return f"""DX_PROVISION_SCRIPT="$(mktemp)"
cat > "$DX_PROVISION_SCRIPT" <<'GREENGRASS_PROVISION_EOF'
set -euo pipefail
{export_lines}
{provision_with_retry}
GREENGRASS_PROVISION_EOF
{sudo_run('bash "$DX_PROVISION_SCRIPT"')}
rm -f "$DX_PROVISION_SCRIPT\""""


def list_core_devices(greengrass_client, thing_group_arn: str | None = None) -> list[dict]:
    paginate_kwargs = {}
    if thing_group_arn:
        paginate_kwargs["thingGroupArn"] = thing_group_arn
    paginator = greengrass_client.get_paginator("list_core_devices")
    devices: list[dict] = []
    for page in paginator.paginate(**paginate_kwargs):
        for device in page.get("coreDevices", []):
            devices.append(
                {
                    "coreDeviceThingName": device.get("coreDeviceThingName"),
                    "status": device.get("status"),
                    "lastStatusUpdateTimestamp": str(device.get("lastStatusUpdateTimestamp", "")),
                }
            )
    return devices


def _thing_group_name_from_arn(thing_group_arn: str) -> str:
    return thing_group_arn.rsplit("/", 1)[-1]


def list_things_in_thing_group(iot_client, thing_group_name: str) -> set[str]:
    paginator = iot_client.get_paginator("list_things_in_thing_group")
    member_names: set[str] = set()
    for page in paginator.paginate(thingGroupName=thing_group_name):
        for thing_name in page.get("things", []):
            member_names.add(thing_name)
    return member_names


def list_core_devices_in_group(greengrass_client, iot_client, thing_group_arn: str) -> list[dict]:
    member_names = list_things_in_thing_group(
        iot_client, _thing_group_name_from_arn(thing_group_arn)
    )
    devices = list_core_devices(greengrass_client)
    return [
        device
        for device in devices
        if device.get("coreDeviceThingName") in member_names
    ]


def list_thing_groups(iot_client) -> list[dict]:
    paginator = iot_client.get_paginator("list_thing_groups")
    groups: list[dict] = []
    for page in paginator.paginate():
        for group in page.get("thingGroups", []):
            groups.append(
                {
                    "groupName": group.get("groupName"),
                    "groupArn": group.get("groupArn"),
                }
            )
    return groups


def list_installed_components(greengrass_client, core_device_thing_name: str) -> list[dict]:
    paginator = greengrass_client.get_paginator("list_installed_components")
    components: list[dict] = []
    for page in paginator.paginate(coreDeviceThingName=core_device_thing_name):
        for component in page.get("installedComponents", []):
            components.append(
                {
                    "componentName": component.get("componentName"),
                    "componentVersion": component.get("componentVersion"),
                    "lifecycleState": component.get("lifecycleState"),
                    "isRoot": component.get("isRoot", False),
                }
            )
    return components
