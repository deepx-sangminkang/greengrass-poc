import logging
import uuid
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

from config import (
    STACK_NAME,
    INSTANCE_TYPE,
    COMBINED_TEMPLATE_PATH,
    TEMPLATE_BODY_MAX_BYTES,
    MARKETPLACE_SSM_PARAM,
    AMI_OPTIONS,
)

logger = logging.getLogger(__name__)

_session = boto3.Session()
_region = _session.region_name or "us-east-1"

_cf_client = _session.client("cloudformation")
_ec2_client = _session.client("ec2")
_s3_client = _session.client("s3")
_ssm_client = _session.client("ssm")
_logs_client = _session.client("logs")
_sts_client = _session.client("sts")

# 현재 사용 중인 스택 이름 (기존 스택 선택 시 변경)
_active_stack_name: str = STACK_NAME

# combined stack이면 참인 출력 키들 (하나라도 있으면 우리 스택)
_STACK_MARKER_OUTPUTS = ("ThingGroupName", "StateMachineArn", "ModelBucketName")


def _is_our_stack(outputs: dict) -> bool:
    return any(k in outputs for k in _STACK_MARKER_OUTPUTS)


def get_region() -> str:
    return _region


def get_active_stack_name() -> str:
    return _active_stack_name


def set_active_stack_name(name: str):
    global _active_stack_name
    _active_stack_name = name
    logger.info("Active stack changed to: %s", name)


def list_dx_compiler_stacks() -> list[dict]:
    """우리 앱이 배포한 combined CF 스택 목록을 반환."""
    stacks = []
    paginator = _cf_client.get_paginator("list_stacks")
    target_statuses = [
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
        "CREATE_IN_PROGRESS", "UPDATE_IN_PROGRESS", "DELETE_IN_PROGRESS",
    ]

    for page in paginator.paginate(StackStatusFilter=target_statuses):
        for s in page.get("StackSummaries", []):
            stack_name = s["StackName"]
            try:
                detail = _cf_client.describe_stacks(StackName=stack_name)
                stack = detail["Stacks"][0]
                outputs = {
                    o["OutputKey"]: o["OutputValue"]
                    for o in stack.get("Outputs", [])
                }
                if _is_our_stack(outputs):
                    stacks.append({
                        "name": stack_name,
                        "status": stack["StackStatus"],
                        "outputs": outputs,
                        "created": s.get("CreationTime", "").isoformat()
                        if hasattr(s.get("CreationTime", ""), "isoformat")
                        else str(s.get("CreationTime", "")),
                    })
            except ClientError:
                continue

    return stacks


def select_stack(stack_name: str) -> dict:
    """기존 스택을 선택하여 활성 스택으로 설정."""
    try:
        resp = _cf_client.describe_stacks(StackName=stack_name)
        stack = resp["Stacks"][0]
        outputs = {
            o["OutputKey"]: o["OutputValue"]
            for o in stack.get("Outputs", [])
        }
        if not _is_our_stack(outputs):
            raise ValueError(f"'{stack_name}'은 DX Marketplace 스택이 아닙니다.")

        set_active_stack_name(stack_name)
        return {
            "name": stack_name,
            "status": stack["StackStatus"],
            "outputs": outputs,
        }
    except ClientError as e:
        if "does not exist" in str(e):
            raise ValueError(f"스택 '{stack_name}'을 찾을 수 없습니다.")
        raise


def delete_stack(stack_name: str) -> dict:
    """CF 스택 삭제를 시작."""
    info = get_stack_status(stack_name)
    if not info:
        raise ValueError(f"스택 '{stack_name}'을 찾을 수 없습니다.")

    if "DELETE" in info["status"]:
        return {"action": "already_deleting", "status": info["status"]}

    logger.info("Deleting stack: %s", stack_name)
    _cf_client.delete_stack(StackName=stack_name)

    if _active_stack_name == stack_name:
        set_active_stack_name(STACK_NAME)

    return {"action": "deleting", "status": "DELETE_IN_PROGRESS"}


def check_marketplace_subscription() -> dict:
    """Marketplace 구독 여부를 SSM Parameter로 확인."""
    try:
        resp = _ssm_client.get_parameter(Name=MARKETPLACE_SSM_PARAM)
        ami_id = resp["Parameter"]["Value"]
        return {"subscribed": True, "ami_id": ami_id}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ParameterNotFound":
            return {"subscribed": False, "ami_id": None}
        raise


def detect_default_vpc() -> dict:
    """Default VPC와 첫 번째 Subnet을 자동 탐지."""
    vpcs = _ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    if not vpcs["Vpcs"]:
        raise RuntimeError("Default VPC를 찾을 수 없습니다. AWS 콘솔에서 VPC를 확인하세요.")

    vpc_id = vpcs["Vpcs"][0]["VpcId"]

    subnets = _ec2_client.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "default-for-az", "Values": ["true"]},
        ]
    )
    if not subnets["Subnets"]:
        raise RuntimeError(f"VPC {vpc_id}에서 기본 서브넷을 찾을 수 없습니다.")

    subnet_id = subnets["Subnets"][0]["SubnetId"]
    logger.info("Detected VPC=%s, Subnet=%s", vpc_id, subnet_id)
    return {"VpcId": vpc_id, "SubnetId": subnet_id}


def _tag_name(tags: list | None) -> str:
    for t in tags or []:
        if t.get("Key") == "Name":
            return t.get("Value", "")
    return ""


def list_vpcs() -> list[dict]:
    resp = _ec2_client.describe_vpcs()
    return [
        {
            "id": v["VpcId"],
            "cidr": v.get("CidrBlock", ""),
            "isDefault": v.get("IsDefault", False),
            "name": _tag_name(v.get("Tags")),
        }
        for v in resp.get("Vpcs", [])
    ]


def list_subnets(vpc_id: str) -> list[dict]:
    resp = _ec2_client.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )
    return [
        {
            "id": s["SubnetId"],
            "cidr": s.get("CidrBlock", ""),
            "az": s.get("AvailabilityZone", ""),
            "name": _tag_name(s.get("Tags")),
        }
        for s in resp.get("Subnets", [])
    ]


def get_stack_status(stack_name: str | None = None) -> dict | None:
    """스택 상태를 조회. 스택이 없으면 None 반환."""
    name = stack_name or _active_stack_name
    try:
        resp = _cf_client.describe_stacks(StackName=name)
        stack = resp["Stacks"][0]
        return {
            "status": stack["StackStatus"],
            "outputs": {
                o["OutputKey"]: o["OutputValue"]
                for o in stack.get("Outputs", [])
            },
        }
    except ClientError as e:
        if "does not exist" in str(e):
            return None
        raise


def get_stack_events(max_events: int = 10) -> list[dict]:
    """최근 스택 이벤트를 반환."""
    try:
        resp = _cf_client.describe_stack_events(StackName=_active_stack_name)
        events = []
        for e in resp["StackEvents"][:max_events]:
            events.append({
                "resource": e.get("LogicalResourceId", ""),
                "status": e.get("ResourceStatus", ""),
                "reason": e.get("ResourceStatusReason", ""),
                "timestamp": e.get("Timestamp", "").isoformat() if hasattr(e.get("Timestamp", ""), "isoformat") else str(e.get("Timestamp", "")),
            })
        return events
    except ClientError:
        return []


def _ensure_staging_bucket(bucket: str) -> None:
    try:
        _s3_client.head_bucket(Bucket=bucket)
        return
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise

    try:
        if _region == "us-east-1":
            _s3_client.create_bucket(Bucket=bucket)
        else:
            _s3_client.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": _region},
            )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code != "BucketAlreadyOwnedByYou":
            raise


def _stage_template(stack_name: str, template_body: str) -> str:
    """template을 S3 staging 버킷에 올리고 virtual-hosted URL 반환."""
    account_id = _sts_client.get_caller_identity()["Account"]
    bucket = f"dx-web-cfn-staging-{account_id}-{_region}"
    _ensure_staging_bucket(bucket)
    key = f"templates/{stack_name}.yaml"
    _s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=template_body.encode("utf-8"),
        ContentType="application/yaml",
    )
    return f"https://{bucket}.s3.{_region}.amazonaws.com/{key}"


def _template_kwargs(stack_name: str) -> dict:
    """template 크기에 따라 TemplateBody 또는 TemplateURL(S3) 반환."""
    template_body = COMBINED_TEMPLATE_PATH.read_text(encoding="utf-8")
    if len(template_body.encode("utf-8")) > TEMPLATE_BODY_MAX_BYTES:
        return {"TemplateURL": _stage_template(stack_name, template_body)}
    return {"TemplateBody": template_body}


def validate_template(ami_type: str = "marketplace") -> dict:
    """combined template을 검증 (56KB > 51200 이므로 S3 TemplateURL 사용)."""
    if ami_type not in AMI_OPTIONS:
        return {"valid": False, "error": f"Unknown AMI type: {ami_type}"}
    try:
        kwargs = _template_kwargs(f"validate-{ami_type}")
        resp = _cf_client.validate_template(**kwargs)
        parameters = [
            {
                "key": p.get("ParameterKey"),
                "default": p.get("DefaultValue"),
                "noEcho": p.get("NoEcho", False),
            }
            for p in resp.get("Parameters", [])
        ]
        return {
            "valid": True,
            "parameters": parameters,
            "capabilities": resp.get("Capabilities", []),
        }
    except ClientError as e:
        return {"valid": False, "error": str(e)}


def deploy_stack(
    vpc_id: str,
    subnet_id: str,
    model_bucket_name: str,
    instance_type: str = INSTANCE_TYPE,
    ami_type: str = "marketplace",
    stack_name: str | None = None,
    thing_group_name: str = "",
) -> dict:
    """combined CF 스택을 비동기 생성."""
    ami_option = AMI_OPTIONS.get(ami_type)
    if not ami_option:
        raise ValueError(f"Unknown AMI type: {ami_type}. Options: {list(AMI_OPTIONS.keys())}")

    if not stack_name:
        stack_name = f"dx-marketplace-web-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    image_param = ami_option["ssm_param"]
    parameters = [
        {"ParameterKey": "ImageId", "ParameterValue": image_param},
        {"ParameterKey": "ModelBucketName", "ParameterValue": model_bucket_name},
        {"ParameterKey": "InstanceType", "ParameterValue": instance_type},
        {"ParameterKey": "VpcId", "ParameterValue": vpc_id},
        {"ParameterKey": "SubnetId", "ParameterValue": subnet_id},
    ]
    if thing_group_name:
        parameters.append({"ParameterKey": "ThingGroupName", "ParameterValue": thing_group_name})

    logger.info("Creating stack %s (bucket=%s, ami=%s)...", stack_name, model_bucket_name, ami_type)
    _cf_client.create_stack(
        StackName=stack_name,
        Parameters=parameters,
        Capabilities=["CAPABILITY_IAM"],
        Tags=[
            {"Key": "Project", "Value": "DX-Marketplace-Web"},
            {"Key": "ManagedBy", "Value": "dx-marketplace-web-app"},
            {"Key": "AmiType", "Value": ami_type},
        ],
        **_template_kwargs(stack_name),
    )

    set_active_stack_name(stack_name)
    return {
        "action": "creating",
        "status": "CREATE_IN_PROGRESS",
        "stack_name": stack_name,
        "bucket": model_bucket_name,
        "ami_type": ami_type,
    }


def get_bucket_name() -> str:
    """현재 스택의 S3 버킷 이름을 반환."""
    info = get_stack_status(_active_stack_name)
    if not info:
        raise RuntimeError("Stack not found")

    outputs = info.get("outputs", {})
    if "ModelBucketName" in outputs:
        return outputs["ModelBucketName"]

    resources = _cf_client.list_stack_resources(StackName=_active_stack_name)
    for r in resources["StackResourceSummaries"]:
        if r["LogicalResourceId"] == "ModelBucket":
            return r["PhysicalResourceId"]

    raise RuntimeError("S3 bucket not found in stack resources")


def get_state_machine_arn() -> str:
    """현재 스택의 Step Functions State Machine ARN을 반환."""
    info = get_stack_status(_active_stack_name)
    if not info:
        raise RuntimeError("Stack not found")

    outputs = info.get("outputs", {})
    if "StateMachineArn" in outputs:
        return outputs["StateMachineArn"]

    resources = _cf_client.list_stack_resources(StackName=_active_stack_name)
    for r in resources["StackResourceSummaries"]:
        if r["LogicalResourceId"] == "CompilerStateMachine":
            return r["PhysicalResourceId"]

    raise RuntimeError("State Machine not found in stack resources")


def get_execution_log_group() -> str | None:
    """현재 스택의 dx_com 실행 로그 CloudWatch Log Group 이름을 반환."""
    info = get_stack_status(_active_stack_name)
    if not info:
        return None

    outputs = info.get("outputs", {})
    if "CompilerExecutionLogGroupName" in outputs:
        return outputs["CompilerExecutionLogGroupName"]

    return f"/dx-compiler/{_active_stack_name}/execution"


def get_compiler_logs(command_id: str, instance_id: str) -> list[dict]:
    """CloudWatch Logs에서 dx_com 실행 로그를 조회 (전체 페이지네이션)."""
    log_group = get_execution_log_group()
    if not log_group:
        return []

    log_streams = [
        f"{command_id}/{instance_id}/CompileModel/stdout",
        f"{command_id}/{instance_id}/CompileModel/stderr",
        f"{command_id}/{instance_id}/aws-runShellScript/stdout",
        f"{command_id}/{instance_id}/aws-runShellScript/stderr",
    ]

    all_events = []
    for stream_name in log_streams:
        stream_type = "stdout" if "stdout" in stream_name else "stderr"
        try:
            next_token = None
            while True:
                kwargs = {
                    "logGroupName": log_group,
                    "logStreamName": stream_name,
                    "startFromHead": True,
                    "limit": 1000,
                }
                if next_token:
                    kwargs["nextToken"] = next_token

                resp = _logs_client.get_log_events(**kwargs)
                events = resp.get("events", [])
                if not events:
                    break

                for event in events:
                    msg = event["message"].rstrip("\n")
                    for line in msg.split("\n"):
                        all_events.append({
                            "timestamp": event["timestamp"],
                            "message": line,
                            "stream": stream_type,
                        })

                new_token = resp.get("nextForwardToken")
                if new_token == next_token:
                    break
                next_token = new_token
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in ("ResourceNotFoundException",):
                logger.debug("Log stream not found: %s/%s", log_group, stream_name)
            continue

    all_events.sort(key=lambda e: e["timestamp"])
    return all_events
