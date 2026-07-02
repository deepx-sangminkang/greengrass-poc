import re
import shlex


_REGION_RE = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")
_IOT_NAME_RE = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")
_IAM_ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9+=,.@_-]{1,64}$")
_COMPONENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_COMPONENT_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
_STACK_NAME_RE = re.compile(r"^[A-Za-z][-A-Za-z0-9]{0,127}$")
_S3_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_ARTIFACT_KEY_RE = re.compile(r"^[A-Za-z0-9_./+=,@-]{1,1024}$")
_DEFAULT_USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}:[A-Za-z0-9_][A-Za-z0-9_-]{0,31}$")


def _validate(field_name: str, value: str, pattern: re.Pattern[str]) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"{field_name} 값에 허용되지 않는 문자가 있습니다: {value}")
    return value


def validate_region(value: str) -> str:
    return _validate("region", value, _REGION_RE)


def validate_iot_name(field_name: str, value: str) -> str:
    return _validate(field_name, value, _IOT_NAME_RE)


def validate_iam_role_name(value: str) -> str:
    return _validate("token_exchange_role_name", value, _IAM_ROLE_NAME_RE)


def validate_component_name(value: str) -> str:
    return _validate("component_name", value, _COMPONENT_NAME_RE)


def validate_component_version(value: str) -> str:
    return _validate("version", value, _COMPONENT_VERSION_RE)


def validate_stack_name(value: str) -> str:
    return _validate("stack_name", value, _STACK_NAME_RE)


def validate_s3_bucket_name(value: str) -> str:
    return _validate("artifact_bucket", value, _S3_BUCKET_RE)


def validate_artifact_key(value: str) -> str:
    return _validate("artifact_key", value, _ARTIFACT_KEY_RE)


def validate_component_default_user(value: str) -> str:
    return _validate("component_default_user", value, _DEFAULT_USER_RE)


def shell_quote(value: str) -> str:
    return shlex.quote(value)
