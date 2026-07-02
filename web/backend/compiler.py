import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from config import MARKETPLACE_SSM_PARAM
from aws_manager import get_bucket_name, get_state_machine_arn, get_region, get_compiler_logs

logger = logging.getLogger(__name__)

_session = boto3.Session()
_s3_client = _session.client("s3")
_sfn_client = _session.client("stepfunctions")


class JobStatus(str, Enum):
    UPLOADING = "uploading"
    PENDING = "pending"       # S3 업로드 완료, Lambda 트리거 대기
    RUNNING = "running"       # Step Functions 실행 중
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class CompileJob:
    job_id: str
    original_filename: str
    s3_key: str
    config_filename: str = ""
    config_s3_key: str = ""
    status: JobStatus = JobStatus.UPLOADING
    sfn_execution_arn: Optional[str] = None
    output_s3_key: Optional[str] = None
    error_message: Optional[str] = None
    instance_id: Optional[str] = None
    command_id: Optional[str] = None
    logs: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def add_log(self, message: str):
        self.logs.append({"time": time.time(), "message": message})


_jobs: dict[str, CompileJob] = {}


def create_job(original_filename: str, config_filename: str = "") -> CompileJob:
    job_id = uuid.uuid4().hex[:12]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", original_filename).strip("-")
    s3_key = f"uploads/{job_id}/{safe_name}"

    config_s3_key = ""
    if config_filename:
        safe_config = re.sub(r"[^A-Za-z0-9._-]+", "-", config_filename).strip("-")
        config_s3_key = f"uploads/{job_id}/{safe_config}"

    job = CompileJob(
        job_id=job_id,
        original_filename=original_filename,
        s3_key=s3_key,
        config_filename=config_filename,
        config_s3_key=config_s3_key,
    )
    _jobs[job_id] = job
    return job


def upload_model(job_id: str, file_content: bytes, config_content: bytes = b"") -> CompileJob:
    job = _jobs.get(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    bucket = get_bucket_name()
    logger.info("Uploading %s to s3://%s/%s", job.original_filename, bucket, job.s3_key)
    job.add_log(f"S3 업로드 시작: s3://{bucket}/{job.s3_key}")

    _s3_client.put_object(
        Bucket=bucket,
        Key=job.s3_key,
        Body=file_content,
        ServerSideEncryption="AES256",
    )

    if config_content and job.config_s3_key:
        logger.info("Uploading %s to s3://%s/%s", job.config_filename, bucket, job.config_s3_key)
        job.add_log(f"JSON 설정 업로드: s3://{bucket}/{job.config_s3_key}")
        _s3_client.put_object(
            Bucket=bucket,
            Key=job.config_s3_key,
            Body=config_content,
            ServerSideEncryption="AES256",
        )

    job.status = JobStatus.PENDING
    job.add_log(f"S3 업로드 완료. Lambda 트리거 대기 중...")
    logger.info("Upload complete for job %s", job_id)
    return job


def get_job(job_id: str) -> Optional[CompileJob]:
    return _jobs.get(job_id)


def refresh_job_status(job_id: str) -> CompileJob:
    """Step Functions 실행 상태를 조회하여 job 상태를 업데이트."""
    job = _jobs.get(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
        return job

    # Step Functions 실행을 찾기
    if not job.sfn_execution_arn:
        job.sfn_execution_arn = _find_execution(job)
        if job.sfn_execution_arn:
            job.add_log(f"Step Functions 실행 감지: {job.sfn_execution_arn.split(':')[-1]}")

    if not job.sfn_execution_arn:
        if time.time() - job.created_at > 300:
            job.status = JobStatus.FAILED
            job.error_message = "Compilation was not triggered within 5 minutes"
            job.add_log("❌ 5분 내 컴파일이 트리거되지 않음")
        return job

    # 실행 상태 조회
    try:
        resp = _sfn_client.describe_execution(executionArn=job.sfn_execution_arn)
        sfn_status = resp["status"]

        # 실행 히스토리에서 EC2 인스턴스 ID 및 상태 변화 추출
        _sync_execution_history(job)

        # dx_com 실행 로그 가져오기 (CloudWatch Logs)
        if job.command_id and job.instance_id:
            _sync_compiler_logs(job)

        if sfn_status == "RUNNING":
            if job.status != JobStatus.RUNNING:
                job.status = JobStatus.RUNNING
        elif sfn_status == "SUCCEEDED":
            job.status = JobStatus.SUCCEEDED
            job.output_s3_key = _find_output_key(job)
            job.add_log(f"✅ 컴파일 완료! 출력: {job.output_s3_key}")
        elif sfn_status in ("FAILED", "TIMED_OUT", "ABORTED"):
            job.status = JobStatus.FAILED
            job.error_message = resp.get("cause", f"Step Functions execution {sfn_status}")
            job.add_log(f"❌ 컴파일 실패: {job.error_message}")
    except ClientError as e:
        logger.error("Error checking execution status: %s", e)

    return job


# 이미 로그에 기록한 이벤트 ID를 추적
_logged_events: dict[str, set] = {}
# 이미 로그에 기록한 CloudWatch 로그 타임스탬프를 추적
_logged_cw_timestamps: dict[str, set] = {}


def _sync_compiler_logs(job: CompileJob):
    """CloudWatch Logs에서 dx_com 실행 로그를 읽어 job 로그에 추가."""
    if not job.command_id or not job.instance_id:
        return

    if job.job_id not in _logged_cw_timestamps:
        _logged_cw_timestamps[job.job_id] = set()

    seen = _logged_cw_timestamps[job.job_id]

    try:
        cw_logs = get_compiler_logs(job.command_id, job.instance_id)
        for log_entry in cw_logs:
            key = (log_entry["timestamp"], log_entry["message"])
            if key in seen:
                continue
            seen.add(key)

            msg = log_entry["message"]
            stream_icon = "📝" if log_entry["stream"] == "stdout" else "⚠️"
            job.add_log(f"{stream_icon} {msg}")
    except Exception as e:
        logger.debug("Failed to fetch compiler logs: %s", e)


def _sync_execution_history(job: CompileJob):
    """Step Functions 히스토리를 읽어 정제된 이벤트를 로그에 추가."""
    import json

    if job.job_id not in _logged_events:
        _logged_events[job.job_id] = set()

    seen = _logged_events[job.job_id]

    try:
        resp = _sfn_client.get_execution_history(
            executionArn=job.sfn_execution_arn,
            maxResults=100,
            reverseOrder=False,
        )

        for event in resp.get("events", []):
            event_id = event["id"]
            if event_id in seen:
                continue
            seen.add(event_id)

            event_type = event["type"]
            timestamp = event.get("timestamp")
            ts_str = timestamp.strftime("%H:%M:%S") if timestamp else ""

            # ExecutionStarted
            if event_type == "ExecutionStarted":
                job.add_log(f"[{ts_str}] 🚀 실행 시작")
                continue

            # ExecutionSucceeded
            if event_type == "ExecutionSucceeded":
                job.add_log(f"[{ts_str}] 🎉 실행 성공")
                continue

            # State Entered 이벤트
            if event_type.endswith("StateEntered"):
                details = event.get("stateEnteredEventDetails", {})
                state_name = details.get("name", "")
                icon = _get_state_icon(state_name)
                job.add_log(f"[{ts_str}] {icon} {state_name} 진입")
                continue

            # TaskSucceeded - 리소스 정보 포함
            if event_type == "TaskSucceeded":
                details = event.get("taskSucceededEventDetails", {})
                resource = details.get("resourceType", "")
                output = details.get("output", "")

                # EC2 인스턴스 ID 추출
                if "InstanceId" in output or "Instances" in output:
                    try:
                        parsed = json.loads(output)
                        instances = parsed.get("Instances", [])
                        if instances:
                            instance_id = instances[0].get("InstanceId", "")
                            if instance_id and not job.instance_id:
                                job.instance_id = instance_id
                                job.add_log(f"[{ts_str}] ✅ Task 성공 → EC2: {instance_id}")
                                continue
                    except json.JSONDecodeError:
                        pass

                # SSM SendCommand의 CommandId 추출
                if "CommandId" in output and not job.command_id:
                    try:
                        parsed = json.loads(output)
                        cmd = parsed.get("Command", {})
                        cmd_id = cmd.get("CommandId", "")
                        if cmd_id:
                            job.command_id = cmd_id
                            job.add_log(f"[{ts_str}] ✅ SSM 명령 전송 (CommandId: {cmd_id[:12]}...)")
                            continue
                    except json.JSONDecodeError:
                        pass

                job.add_log(f"[{ts_str}] ✅ Task 성공" + (f" ({resource})" if resource else ""))
                continue

            # TaskFailed
            if event_type == "TaskFailed":
                details = event.get("taskFailedEventDetails", {})
                cause = details.get("cause", "")[:150]
                job.add_log(f"[{ts_str}] ❌ Task 실패: {cause}")
                continue

            # TaskScheduled - 어떤 AWS 서비스 호출인지
            if event_type == "TaskScheduled":
                details = event.get("taskScheduledEventDetails", {})
                resource = details.get("resourceType", "")
                resource_short = resource.replace("aws-sdk:", "") if resource else ""
                if resource_short:
                    job.add_log(f"[{ts_str}] 📡 API 호출: {resource_short}")
                continue

            # WaitStateExited - 대기 완료
            if event_type == "WaitStateExited":
                details = event.get("stateExitedEventDetails", {})
                state_name = details.get("name", "")
                job.add_log(f"[{ts_str}] ⏰ {state_name} 대기 완료")
                continue

            # SaveInstanceId PassStateExited - 인스턴스 ID 추출
            if event_type == "PassStateExited":
                details = event.get("stateExitedEventDetails", {})
                state_name = details.get("name", "")
                output = details.get("output", "")
                if state_name == "SaveInstanceId" and "InstanceId" in output:
                    try:
                        parsed = json.loads(output)
                        instance_id = parsed.get("InstanceId", "")
                        if instance_id and not job.instance_id:
                            job.instance_id = instance_id
                            job.add_log(f"[{ts_str}] 🖥️ 인스턴스 ID: {instance_id}")
                    except json.JSONDecodeError:
                        pass
                continue

    except ClientError as e:
        logger.error("Error fetching execution history: %s", e)


def _get_state_icon(state_name: str) -> str:
    icons = {
        "StartEC2Instance": "🖥️",
        "SaveInstanceId": "💾",
        "WaitForInstanceReady": "⏳",
        "RunCompilationCommand": "🔧",
        "WaitForCompletion": "⏳",
        "CheckCommandStatus": "🔍",
        "EvaluateCommandResult": "📊",
        "TerminateInstanceAfterSuccess": "🗑️",
        "TerminateInstanceAfterFailure": "🗑️",
        "WorkflowComplete": "🎉",
        "WorkflowFailed": "💥",
        "WorkflowFailedBeforeInstance": "💥",
    }
    return icons.get(state_name, "▶️")


def get_job_links(job_id: str) -> dict:
    """job에 관련된 AWS 콘솔 링크들을 반환."""
    job = _jobs.get(job_id)
    if not job:
        return {}

    bucket = get_bucket_name()
    region = get_region()
    links = {}

    # S3 input link
    links["s3_input"] = (
        f"https://s3.console.aws.amazon.com/s3/object/{bucket}?region={region}&prefix={job.s3_key}"
    )

    # S3 output link
    if job.output_s3_key:
        links["s3_output"] = (
            f"https://s3.console.aws.amazon.com/s3/object/{bucket}?region={region}&prefix={job.output_s3_key}"
        )

    # EC2 instance link
    if job.instance_id:
        links["ec2_instance"] = (
            f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#InstanceDetails:instanceId={job.instance_id}"
        )

    # Step Functions execution link
    if job.sfn_execution_arn:
        links["step_functions"] = (
            f"https://{region}.console.aws.amazon.com/states/home?region={region}#/v2/executions/details/{job.sfn_execution_arn}"
        )

    return links


def get_compiler_output_logs(job_id: str) -> list[dict]:
    """CloudWatch에서 dx_com 실행 로그를 별도로 반환 (컴파일러 로그 패널용)."""
    job = _jobs.get(job_id)
    if not job or not job.command_id or not job.instance_id:
        return []

    try:
        cw_logs = get_compiler_logs(job.command_id, job.instance_id)
        return [
            {
                "timestamp": log["timestamp"],
                "message": log["message"],
                "stream": log["stream"],
            }
            for log in cw_logs
        ]
    except Exception as e:
        logger.debug("Failed to fetch compiler output logs: %s", e)
        return []


def _find_execution(job: CompileJob) -> Optional[str]:
    """Step Functions에서 이 job에 해당하는 실행을 찾는다."""
    try:
        state_machine_arn = get_state_machine_arn()
        resp = _sfn_client.list_executions(
            stateMachineArn=state_machine_arn,
            maxResults=20,
        )
        for execution in resp["executions"]:
            try:
                detail = _sfn_client.describe_execution(
                    executionArn=execution["executionArn"]
                )
                input_data = detail.get("input", "{}")
                import json
                parsed = json.loads(input_data)
                if parsed.get("ModelKey") == job.s3_key:
                    return execution["executionArn"]
            except (ClientError, json.JSONDecodeError):
                continue
    except ClientError as e:
        logger.error("Error listing executions: %s", e)
    return None


def _find_output_key(job: CompileJob) -> Optional[str]:
    """컴파일된 .dxnn 파일의 S3 키를 찾는다."""
    bucket = get_bucket_name()
    model_name = os.path.splitext(os.path.basename(job.s3_key))[0]
    model_dir = os.path.dirname(job.s3_key)

    if model_dir == ".":
        expected_key = f"{model_name}_compiled.dxnn"
    else:
        expected_key = f"{model_dir}/{model_name}_compiled.dxnn"

    try:
        _s3_client.head_object(Bucket=bucket, Key=expected_key)
        return expected_key
    except ClientError:
        # prefix로 검색
        try:
            resp = _s3_client.list_objects_v2(
                Bucket=bucket, Prefix=model_dir, MaxKeys=50
            )
            for obj in resp.get("Contents", []):
                if obj["Key"].endswith(".dxnn"):
                    return obj["Key"]
        except ClientError:
            pass
    return None


def download_result(job_id: str) -> tuple[bytes, str]:
    """컴파일된 .dxnn 파일을 다운로드하여 (content, filename) 반환."""
    job = _jobs.get(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
    if job.status != JobStatus.SUCCEEDED:
        raise ValueError(f"Job {job_id} is not completed (status: {job.status})")
    if not job.output_s3_key:
        raise ValueError(f"Job {job_id} has no output file")

    bucket = get_bucket_name()
    resp = _s3_client.get_object(Bucket=bucket, Key=job.output_s3_key)
    content = resp["Body"].read()
    filename = os.path.basename(job.output_s3_key)
    return content, filename
