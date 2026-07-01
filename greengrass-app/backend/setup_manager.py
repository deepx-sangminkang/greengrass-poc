from botocore.exceptions import ClientError


READY_STACK_STATUSES = {
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
}

NO_UPDATES_MESSAGE = "No updates are to be performed"

# CloudFormation rejects an inline TemplateBody larger than this many bytes.
# Larger templates must be uploaded to S3 and referenced via TemplateURL.
TEMPLATE_BODY_MAX_BYTES = 51200

_BUCKET_MISSING_CODES = {"404", "NoSuchBucket", "NotFound"}

EXCLUDED_STACK_STATUSES = {
    "DELETE_COMPLETE",
    "DELETE_FAILED",
    "ROLLBACK_COMPLETE",
    "UPDATE_ROLLBACK_COMPLETE",
}

MANAGED_OUTPUT_KEYS = {
    "ThingGroupName",
    "TokenExchangeRoleName",
}


def list_stacks(cloudformation_client) -> list[dict]:
    paginator = cloudformation_client.get_paginator("describe_stacks")
    stacks: list[dict] = []
    for page in paginator.paginate():
        for stack in page["Stacks"]:
            status = stack["StackStatus"]
            if status in EXCLUDED_STACK_STATUSES:
                continue
            outputs = {
                output["OutputKey"]: output["OutputValue"]
                for output in stack.get("Outputs", [])
            }
            created = stack.get("CreationTime")
            updated = stack.get("LastUpdatedTime")
            stacks.append(
                {
                    "name": stack["StackName"],
                    "status": status,
                    "ready": status in READY_STACK_STATUSES,
                    "managed": MANAGED_OUTPUT_KEYS.issubset(outputs),
                    "createdAt": created.isoformat() if created else None,
                    "updatedAt": updated.isoformat() if updated else None,
                }
            )

    stacks.sort(
        key=lambda item: (
            item["managed"],
            item["updatedAt"] or item["createdAt"] or "",
        ),
        reverse=True,
    )
    return stacks


def get_stack_status(cloudformation_client, stack_name: str) -> dict:
    try:
        response = cloudformation_client.describe_stacks(StackName=stack_name)
    except ClientError as error:
        message = str(error)
        if "does not exist" in message:
            return {"name": stack_name, "status": "NOT_FOUND", "ready": False, "outputs": {}}
        raise

    stack = response["Stacks"][0]
    outputs = {
        output["OutputKey"]: output["OutputValue"]
        for output in stack.get("Outputs", [])
    }
    status = stack["StackStatus"]
    return {
        "name": stack_name,
        "status": status,
        "ready": status in READY_STACK_STATUSES,
        "outputs": outputs,
    }


def ensure_staging_bucket(s3_client, bucket: str, region: str) -> None:
    try:
        s3_client.head_bucket(Bucket=bucket)
        return
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code not in _BUCKET_MISSING_CODES:
            raise

    if region == "us-east-1":
        s3_client.create_bucket(Bucket=bucket)
    else:
        s3_client.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )


def _stage_template(
    s3_client, sts_client, region: str, stack_name: str, template_body: str
) -> str:
    account_id = sts_client.get_caller_identity()["Account"]
    bucket = f"dx-greengrass-cfn-staging-{account_id}-{region}"
    ensure_staging_bucket(s3_client, bucket, region)
    key = f"templates/{stack_name}.yaml"
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=template_body.encode("utf-8"),
        ContentType="application/yaml",
    )
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def deploy_stack(
    cloudformation_client,
    stack_name: str,
    template_body: str,
    parameters: dict[str, str],
    capabilities: tuple[str, ...] = ("CAPABILITY_IAM",),
    s3_client=None,
    sts_client=None,
    region: str | None = None,
) -> dict:
    existing = get_stack_status(cloudformation_client, stack_name)
    request_parameters = [
        {"ParameterKey": key, "ParameterValue": value}
        for key, value in parameters.items()
    ]

    creating = existing["status"] == "NOT_FOUND"
    api_call = cloudformation_client.create_stack if creating else cloudformation_client.update_stack
    waiter_name = "stack_create_complete" if creating else "stack_update_complete"
    action = "create" if creating else "update"

    template_kwargs: dict[str, str] = {"TemplateBody": template_body}
    if (
        len(template_body.encode("utf-8")) > TEMPLATE_BODY_MAX_BYTES
        and s3_client is not None
        and sts_client is not None
        and region is not None
    ):
        template_url = _stage_template(
            s3_client, sts_client, region, stack_name, template_body
        )
        template_kwargs = {"TemplateURL": template_url}

    try:
        api_call(
            StackName=stack_name,
            Parameters=request_parameters,
            Capabilities=list(capabilities),
            **template_kwargs,
        )
    except ClientError as error:
        if NO_UPDATES_MESSAGE in str(error):
            stack = get_stack_status(cloudformation_client, stack_name)
            return {
                "status": "succeeded",
                "action": "none",
                "message": "변경 사항이 없습니다.",
                "outputs": stack["outputs"],
            }
        raise

    cloudformation_client.get_waiter(waiter_name).wait(StackName=stack_name)
    stack = get_stack_status(cloudformation_client, stack_name)
    return {
        "status": "succeeded" if stack["ready"] else "failed",
        "action": action,
        "stackStatus": stack["status"],
        "outputs": stack["outputs"],
    }
