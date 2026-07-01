import json
import logging
import time
import uuid

import boto3
from botocore.exceptions import ClientError

from config import STACK_NAME, INSTANCE_TYPE, CF_TEMPLATE_PATH, CF_TEMPLATE_V2_PATH, MARKETPLACE_SSM_PARAM, AMI_OPTIONS

logger = logging.getLogger(__name__)

_session = boto3.Session()
_region = _session.region_name or "us-east-1"

_cf_client = _session.client("cloudformation")
_ec2_client = _session.client("ec2")
_s3_client = _session.client("s3")
_ssm_client = _session.client("ssm")
_logs_client = _session.client("logs")

# 현재 사용 중인 스택 이름 (기존 스택 선택 시 변경)
_active_stack_name: str = STACK_NAME


def get_region() -> str:
    return _region


def get_active_stack_name() -> str:
    return _active_stack_name


def set_active_stack_name(name: str):
    global _active_stack_name
    _active_stack_name = name
    logger.info("Active stack changed to: %s", name)


def list_dx_compiler_stacks() -> list[dict]:
    """DX Compiler용으로 배포된 기존 CF 스택 목록을 반환."""
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
                # DX Compiler 스택인지 확인 (StateMachineArn 또는 ModelBucketName 출력이 있는지)
                if "StateMachineArn" in outputs or "ModelBucketName" in outputs:
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
        if "StateMachineArn" not in outputs and "ModelBucketName" not in outputs:
            raise ValueError(f"'{stack_name}'은 DX Compiler 스택이 아닙니다.")

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
    """CF 스택 삭제를 시작. S3 버킷은 DeletionPolicy: Retain이므로 유지됨."""
    info = get_stack_status(stack_name)
    if not info:
        raise ValueError(f"스택 '{stack_name}'을 찾을 수 없습니다.")

    if "DELETE" in info["status"]:
        return {"action": "already_deleting", "status": info["status"]}

    logger.info("Deleting stack: %s", stack_name)
    _cf_client.delete_stack(StackName=stack_name)

    # 현재 활성 스택이 삭제된 경우 기본값으로 리셋
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


def _generate_bucket_name() -> str:
    short_id = uuid.uuid4().hex[:8]
    return f"dx-compiler-web-{short_id}"


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


def deploy_stack(ami_type: str = "marketplace") -> dict:
    """CF 스택을 새 이름으로 비동기 생성. 항상 새 스택을 만든다."""
    from datetime import datetime

    ami_option = AMI_OPTIONS.get(ami_type)
    if not ami_option:
        raise ValueError(f"Unknown AMI type: {ami_type}. Options: {list(AMI_OPTIONS.keys())}")

    timestamp = datetime.now().strftime("%y%m%d%H%M%S")
    new_stack_name = f"dx-compiler-web-{timestamp}"

    network = detect_default_vpc()
    bucket_name = _generate_bucket_name()

    # Marketplace uses v2 template with SSM parameter for AMI resolution
    template_body = CF_TEMPLATE_V2_PATH.read_text(encoding="utf-8")
    image_param = ami_option["ssm_param"]

    logger.info("Creating stack %s (bucket=%s, ami=%s/%s)...", new_stack_name, bucket_name, ami_type, image_param)
    _cf_client.create_stack(
        StackName=new_stack_name,
        TemplateBody=template_body,
        Parameters=[
            {"ParameterKey": "ImageId", "ParameterValue": image_param},
            {"ParameterKey": "ModelBucketName", "ParameterValue": bucket_name},
            {"ParameterKey": "InstanceType", "ParameterValue": INSTANCE_TYPE},
            {"ParameterKey": "VpcId", "ParameterValue": network["VpcId"]},
            {"ParameterKey": "SubnetId", "ParameterValue": network["SubnetId"]},
        ],
        Capabilities=["CAPABILITY_IAM"],
        Tags=[
            {"Key": "Project", "Value": "DX-Compiler-Web"},
            {"Key": "ManagedBy", "Value": "dx-compiler-web-app"},
            {"Key": "AmiType", "Value": ami_type},
        ],
    )

    set_active_stack_name(new_stack_name)
    return {"action": "creating", "status": "CREATE_IN_PROGRESS", "stack_name": new_stack_name, "bucket": bucket_name, "ami_type": ami_type}


def ensure_stack() -> dict:
    """스택이 없으면 생성하고 완료까지 대기."""
    result = deploy_stack()
    if result["action"] == "exists":
        return get_stack_status()
    if result["action"] in ("creating", "in_progress"):
        return _wait_for_stack()
    return get_stack_status()


def _wait_for_stack() -> dict:
    """스택 생성/업데이트 완료를 대기."""
    logger.info("Waiting for stack %s to complete...", _active_stack_name)
    waiter = _cf_client.get_waiter("stack_create_complete")
    try:
        waiter.wait(StackName=_active_stack_name, WaiterConfig={"Delay": 15, "MaxAttempts": 60})
    except Exception:
        info = get_stack_status()
        raise RuntimeError(
            f"Stack creation failed: {info['status'] if info else 'unknown'}"
        )

    info = get_stack_status(_active_stack_name)
    if not info:
        raise RuntimeError("Stack disappeared after creation")
    logger.info("Stack ready: %s", info["status"])
    return info


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

    # Output이 없으면 규칙 기반으로 추정
    return f"/dx-compiler/{_active_stack_name}/execution"


def get_compiler_logs(command_id: str, instance_id: str) -> list[dict]:
    """CloudWatch Logs에서 dx_com 실행 로그를 조회 (전체 페이지네이션)."""
    log_group = get_execution_log_group()
    if not log_group:
        return []

    # SSM CloudWatch 로그 스트림 형식: {CommandId}/{InstanceId}/{DocumentName}/stdout
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
                    # 멀티라인 메시지를 줄 단위로 분리
                    for line in msg.split("\n"):
                        all_events.append({
                            "timestamp": event["timestamp"],
                            "message": line,
                            "stream": stream_type,
                        })

                # 토큰이 이전과 같으면 마지막 페이지
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
