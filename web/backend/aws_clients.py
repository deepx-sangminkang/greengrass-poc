import boto3

from config import AWS_REGION


def create_client(service_name: str):
    return boto3.Session(region_name=AWS_REGION).client(service_name)


def get_aws_credentials() -> dict | None:
    credentials = boto3.Session(region_name=AWS_REGION).get_credentials()
    if credentials is None:
        return None
    frozen = credentials.get_frozen_credentials()
    if not frozen.access_key or not frozen.secret_key:
        return None
    resolved = {"access_key": frozen.access_key, "secret_key": frozen.secret_key}
    if frozen.token:
        resolved["session_token"] = frozen.token
    return resolved
