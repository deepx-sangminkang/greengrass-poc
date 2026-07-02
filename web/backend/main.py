"""Unified DX Marketplace web console (Deploy -> Compile -> Edge).

ponytail: no CSRF / no auth — this is a localhost dev tool that drives one
combined AWS Marketplace CloudFormation template. CORS is allow-all.
Run: uvicorn --app-dir web/backend main:app
"""
import logging
from pathlib import Path

import paramiko
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import aws_manager
import compiler
import config
from aws_clients import create_client, get_aws_credentials
from device_manager import (
    build_install_script,
    list_core_devices,
    list_core_devices_in_group,
    list_installed_components,
    list_thing_groups,
)
from ssh_manager import run_script_over_ssh, stream_script_over_ssh
from validation import validate_iot_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="DX Marketplace Web Console")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

READY_STATUSES = {"CREATE_COMPLETE", "UPDATE_COMPLETE"}


def aws_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=f"AWS 작업 실패: {error}")


def get_active_stack_outputs() -> dict:
    """활성 스택의 outputs dict. 스택이 없으면 빈 dict."""
    info = aws_manager.get_stack_status()
    return info.get("outputs", {}) if info else {}


def require_ready_stack() -> dict:
    """활성 스택이 READY가 아니면 503. outputs 반환."""
    info = aws_manager.get_stack_status()
    if not info or info["status"] not in READY_STATUSES:
        raise HTTPException(status_code=503, detail="활성 CloudFormation 스택이 준비되지 않았습니다.")
    return info["outputs"]


# ---------------------------------------------------------------- Health / Setup

@app.get("/api/health")
def health():
    try:
        info = aws_manager.get_stack_status()
    except (BotoCoreError, ClientError):
        info = None
    return {
        "status": "ok",
        "region": aws_manager.get_region(),
        "activeStackName": aws_manager.get_active_stack_name(),
        "stackStatus": info["status"] if info else "NOT_FOUND",
    }


@app.get("/api/setup/status")
def setup_status():
    try:
        sub = aws_manager.check_marketplace_subscription()
        info = aws_manager.get_stack_status()
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error

    status = info["status"] if info else "NOT_FOUND"
    return {
        "region": aws_manager.get_region(),
        "marketplace": {
            "subscribed": sub["subscribed"],
            "ami_id": sub["ami_id"],
            "subscribe_url": config.MARKETPLACE_URL,
        },
        "ami_options": [
            {"key": k, "label": v["label"], "requires_subscription": v["requires_subscription"]}
            for k, v in config.AMI_OPTIONS.items()
        ],
        "activeStackName": aws_manager.get_active_stack_name(),
        "stack": {
            "name": aws_manager.get_active_stack_name(),
            "status": status,
            "ready": status in READY_STATUSES,
            "outputs": info["outputs"] if info else {},
        },
    }


@app.get("/api/setup/stacks")
def list_setup_stacks():
    try:
        stacks = aws_manager.list_dx_compiler_stacks()
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error
    return {"stacks": stacks, "active": aws_manager.get_active_stack_name()}


class SelectStackRequest(BaseModel):
    stack_name: str


@app.post("/api/setup/stacks/select")
def select_stack(request: SelectStackRequest):
    try:
        stack = aws_manager.select_stack(request.stack_name)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error
    return {"action": "selected", "stack": stack}


@app.delete("/api/setup/stacks/{stack_name}")
def delete_setup_stack(stack_name: str):
    try:
        return aws_manager.delete_stack(stack_name)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error


@app.get("/api/setup/template")
def get_template():
    return PlainTextResponse(config.COMBINED_TEMPLATE_PATH.read_text(encoding="utf-8"))


@app.get("/api/setup/network/vpcs")
def network_vpcs():
    try:
        vpcs = aws_manager.list_vpcs()
        default = None
        try:
            default = aws_manager.detect_default_vpc()
        except RuntimeError:
            default = None
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error
    return {"vpcs": vpcs, "default": default}


@app.get("/api/setup/network/subnets")
def network_subnets(vpc_id: str = Query(...)):
    try:
        return {"subnets": aws_manager.list_subnets(vpc_id)}
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error


class ValidateRequest(BaseModel):
    ami_type: str | None = "marketplace"


@app.post("/api/setup/validate")
def validate_template(request: ValidateRequest | None = None):
    ami_type = (request.ami_type if request else None) or "marketplace"
    try:
        return aws_manager.validate_template(ami_type)
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error


class DeployRequest(BaseModel):
    vpc_id: str
    subnet_id: str
    model_bucket_name: str
    instance_type: str | None = None
    ami_type: str | None = "marketplace"
    stack_name: str | None = None
    thing_group_name: str | None = ""


@app.post("/api/setup/deploy")
def deploy(request: DeployRequest):
    try:
        result = aws_manager.deploy_stack(
            vpc_id=request.vpc_id,
            subnet_id=request.subnet_id,
            model_bucket_name=request.model_bucket_name,
            instance_type=request.instance_type or config.INSTANCE_TYPE,
            ami_type=request.ami_type or "marketplace",
            stack_name=request.stack_name or None,
            thing_group_name=request.thing_group_name or "",
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error
    return result


@app.get("/api/setup/deploy/status")
def deploy_status():
    try:
        info = aws_manager.get_stack_status()
        events = aws_manager.get_stack_events()
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error
    status = info["status"] if info else "NOT_FOUND"
    return {
        "stack_status": status,
        "ready": status in READY_STATUSES,
        "outputs": info["outputs"] if info else {},
        "events": events,
    }


# ---------------------------------------------------------------- Compile

@app.post("/api/compile")
async def compile_model(
    onnx_file: UploadFile = File(...),
    config_file: UploadFile = File(...),
):
    require_ready_stack()
    onnx_bytes = await onnx_file.read()
    config_bytes = await config_file.read()
    job = compiler.create_job(onnx_file.filename, config_file.filename)
    try:
        compiler.upload_model(job.job_id, onnx_bytes, config_bytes)
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error
    return {
        "job_id": job.job_id,
        "status": job.status,
        "filename": job.original_filename,
        "message": "업로드 완료. 컴파일 트리거 대기 중.",
    }


@app.get("/api/jobs/{job_id}/status")
def job_status(job_id: str):
    job = compiler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    try:
        compiler.refresh_job_status(job_id)
    except (BotoCoreError, ClientError) as error:
        logger.warning("refresh_job_status failed: %s", error)
    return {
        "job_id": job.job_id,
        "status": job.status,
        "filename": job.original_filename,
        "error": job.error_message,
        "has_output": bool(job.output_s3_key),
        "instance_id": job.instance_id,
        "links": compiler.get_job_links(job_id),
        "logs": job.logs,
        "compiler_logs": compiler.get_compiler_output_logs(job_id),
    }


@app.get("/api/jobs/{job_id}/download")
def job_download(job_id: str):
    try:
        content, filename = compiler.download_result(job_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------- Edge

@app.get("/api/thing-groups")
def thing_groups():
    try:
        return {"thingGroups": list_thing_groups(create_client("iot"))}
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error


@app.get("/api/devices/cores")
def core_devices(thing_group_arn: str | None = Query(default=None)):
    try:
        greengrass = create_client("greengrassv2")
        if thing_group_arn:
            devices = list_core_devices_in_group(greengrass, create_client("iot"), thing_group_arn)
        else:
            devices = list_core_devices(greengrass)
        return {"devices": devices}
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error


@app.get("/api/devices/{thing_name}/components")
def device_components(thing_name: str):
    try:
        validated = validate_iot_name("thing_name", thing_name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    try:
        components = list_installed_components(create_client("greengrassv2"), validated)
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error
    return {"thingName": validated, "components": components}


class InstallScriptRequest(BaseModel):
    thing_name: str
    thing_group_name: str | None = None
    token_exchange_role_name: str | None = None


class SshInstallRequest(InstallScriptRequest):
    host: str
    username: str
    port: int = 22
    password: str | None = None
    private_key_path: str | None = None


def _resolve_edge_params(request: InstallScriptRequest) -> tuple[str, str]:
    outputs = require_ready_stack()
    thing_group_name = request.thing_group_name or outputs.get("ThingGroupName")
    token_exchange_role_name = request.token_exchange_role_name or outputs.get("TokenExchangeRoleName")
    if not thing_group_name:
        raise HTTPException(status_code=503, detail="ThingGroupName stack output을 찾을 수 없습니다.")
    if not token_exchange_role_name:
        raise HTTPException(status_code=503, detail="TokenExchangeRoleName stack output을 찾을 수 없습니다.")
    return thing_group_name, token_exchange_role_name


@app.post("/api/devices/install-script")
def install_script(request: InstallScriptRequest):
    thing_group_name, token_exchange_role_name = _resolve_edge_params(request)
    try:
        script = build_install_script(
            region=aws_manager.get_region(),
            thing_name=request.thing_name,
            thing_group_name=thing_group_name,
            token_exchange_role_name=token_exchange_role_name,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"script": script}


def _build_ssh_install_script(request: SshInstallRequest) -> str:
    thing_group_name, token_exchange_role_name = _resolve_edge_params(request)
    try:
        return build_install_script(
            region=aws_manager.get_region(),
            thing_name=request.thing_name,
            thing_group_name=thing_group_name,
            token_exchange_role_name=token_exchange_role_name,
            aws_credentials=get_aws_credentials(),
            sudo_password=request.password,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/devices/ssh-install")
def ssh_install(request: SshInstallRequest):
    script = _build_ssh_install_script(request)
    try:
        result = run_script_over_ssh(
            host=request.host,
            username=request.username,
            port=request.port,
            password=request.password,
            private_key_path=request.private_key_path,
            script=script,
        )
    except (OSError, paramiko.SSHException) as error:
        raise HTTPException(status_code=503, detail=f"SSH 설치 실패: {error}") from error
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result)
    return result


@app.post("/api/devices/ssh-install-stream")
def ssh_install_stream(request: SshInstallRequest):
    script = _build_ssh_install_script(request)
    generator = stream_script_over_ssh(
        host=request.host,
        username=request.username,
        port=request.port,
        password=request.password,
        private_key_path=request.private_key_path,
        script=script,
    )
    return StreamingResponse(generator, media_type="text/plain")


# ---------------------------------------------------------------- Static (mount LAST)
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
