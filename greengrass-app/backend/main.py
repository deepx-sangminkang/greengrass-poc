import paramiko
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import Response

from backend import config
from backend.aws_clients import create_client, get_aws_credentials
from backend.device_manager import (
    build_install_script,
    list_core_devices,
    list_core_devices_in_group,
    list_installed_components,
    list_thing_groups,
)
from backend.setup_manager import deploy_stack, get_stack_status, list_stacks
from backend.validation import validate_iot_name, validate_stack_name

app = FastAPI(title="DX Runtime Greengrass Web POC")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

_active_stack_name = config.STACK_NAME


def get_active_stack_name() -> str:
    return _active_stack_name


def set_active_stack_name(stack_name: str) -> str:
    global _active_stack_name
    _active_stack_name = validate_stack_name(stack_name)
    return _active_stack_name


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


class DeployStackRequest(BaseModel):
    stack_name: str | None = None


class SelectStackRequest(BaseModel):
    stack_name: str


def aws_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=f"AWS 작업 실패: {error}")


def request_error(error: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


def require_csrf_token(x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token")) -> None:
    if x_csrf_token != config.CSRF_TOKEN:
        raise HTTPException(status_code=403, detail="CSRF token이 없거나 올바르지 않습니다.")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": "요청 형식이 올바르지 않습니다.",
            "errors": exc.errors(),
        },
    )


def get_stack_outputs() -> dict[str, str]:
    cloudformation = create_client("cloudformation")
    stack = get_stack_status(cloudformation, get_active_stack_name())
    if not stack["ready"]:
        raise HTTPException(status_code=503, detail="CloudFormation 스택이 준비되지 않았습니다.")
    return stack["outputs"]


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "project": config.PROJECT_NAME,
        "region": config.AWS_REGION,
    }


@app.get("/api/session")
async def session():
    return {"csrfToken": config.CSRF_TOKEN}


@app.get("/api/setup/status")
async def setup_status():
    try:
        cloudformation = create_client("cloudformation")
        stack = get_stack_status(cloudformation, get_active_stack_name())
        return {
            "region": config.AWS_REGION,
            "activeStackName": get_active_stack_name(),
            "stack": stack,
        }
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error


@app.get("/api/setup/stacks")
def list_setup_stacks():
    try:
        cloudformation = create_client("cloudformation")
        stacks = list_stacks(cloudformation)
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error
    return {"activeStackName": get_active_stack_name(), "stacks": stacks}


@app.post("/api/setup/select")
def select_setup_stack(request: SelectStackRequest, _: None = Depends(require_csrf_token)):
    try:
        stack_name = validate_stack_name(request.stack_name)
    except ValueError as error:
        raise request_error(error) from error

    try:
        cloudformation = create_client("cloudformation")
        stack = get_stack_status(cloudformation, stack_name)
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error

    if stack["status"] == "NOT_FOUND":
        raise HTTPException(status_code=404, detail=f"스택을 찾을 수 없습니다: {stack_name}")

    set_active_stack_name(stack_name)
    return {"activeStackName": stack_name, "stack": stack}


@app.post("/api/setup/deploy")
def deploy_setup_stack(request: DeployStackRequest | None = None, _: None = Depends(require_csrf_token)):
    requested_name = request.stack_name if request else None
    try:
        stack_name = validate_stack_name(requested_name) if requested_name else get_active_stack_name()
    except ValueError as error:
        raise request_error(error) from error

    template_path = config.INFRA_DIR / "template.yaml"
    try:
        template_body = template_path.read_text(encoding="utf-8")
    except OSError as error:
        raise HTTPException(
            status_code=503,
            detail={"status": "failed", "stderr": f"CloudFormation 템플릿을 읽을 수 없습니다: {error}"},
        ) from error

    parameters = {
        "ProjectName": config.PROJECT_NAME,
        "ThingGroupName": config.THING_GROUP_NAME,
    }

    try:
        result = deploy_stack(
            cloudformation_client=create_client("cloudformation"),
            stack_name=stack_name,
            template_body=template_body,
            parameters=parameters,
            s3_client=create_client("s3"),
            sts_client=create_client("sts"),
            region=config.AWS_REGION,
        )
    except (BotoCoreError, ClientError) as error:
        raise HTTPException(
            status_code=503,
            detail={"status": "failed", "stderr": f"CloudFormation 배포 실패: {error}"},
        ) from error

    if result["status"] != "succeeded":
        raise HTTPException(status_code=503, detail=result)

    set_active_stack_name(stack_name)
    result["stackName"] = stack_name
    return result


@app.get("/api/thing-groups")
async def thing_groups():
    try:
        return {"thingGroups": list_thing_groups(create_client("iot"))}
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error


@app.get("/api/devices/cores")
async def core_devices(thing_group_arn: str | None = Query(default=None)):
    try:
        greengrass = create_client("greengrassv2")
        if thing_group_arn:
            iot = create_client("iot")
            devices = list_core_devices_in_group(greengrass, iot, thing_group_arn)
        else:
            devices = list_core_devices(greengrass)
        return {"devices": devices}
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error


@app.get("/api/devices/{thing_name}/components")
async def core_device_components(thing_name: str):
    try:
        validated_name = validate_iot_name("thing_name", thing_name)
    except ValueError as error:
        raise request_error(error) from error
    try:
        greengrass = create_client("greengrassv2")
        components = list_installed_components(greengrass, validated_name)
        return {"thingName": validated_name, "components": components}
    except (BotoCoreError, ClientError) as error:
        raise aws_error(error) from error


@app.post("/api/devices/install-script")
async def install_script(request: InstallScriptRequest, _: None = Depends(require_csrf_token)):
    try:
        stack_outputs = get_stack_outputs()
        token_exchange_role_name = request.token_exchange_role_name or stack_outputs.get("TokenExchangeRoleName")
        thing_group_name = request.thing_group_name or stack_outputs.get("ThingGroupName")
        if not token_exchange_role_name:
            raise HTTPException(status_code=503, detail="TokenExchangeRoleName stack output을 찾을 수 없습니다.")
        if not thing_group_name:
            raise HTTPException(status_code=503, detail="ThingGroupName stack output을 찾을 수 없습니다.")
        script = build_install_script(
            region=config.AWS_REGION,
            thing_name=request.thing_name,
            thing_group_name=thing_group_name,
            token_exchange_role_name=token_exchange_role_name,
        )
    except ValueError as error:
        raise request_error(error) from error
    return {"script": script}


def _prepare_install_script(request: SshInstallRequest) -> str:
    stack_outputs = get_stack_outputs()
    token_exchange_role_name = request.token_exchange_role_name or stack_outputs.get("TokenExchangeRoleName")
    thing_group_name = request.thing_group_name or stack_outputs.get("ThingGroupName")
    if not token_exchange_role_name:
        raise HTTPException(status_code=503, detail="TokenExchangeRoleName stack output을 찾을 수 없습니다.")
    if not thing_group_name:
        raise HTTPException(status_code=503, detail="ThingGroupName stack output을 찾을 수 없습니다.")
    try:
        return build_install_script(
            region=config.AWS_REGION,
            thing_name=request.thing_name,
            thing_group_name=thing_group_name,
            token_exchange_role_name=token_exchange_role_name,
            aws_credentials=get_aws_credentials(),
            sudo_password=request.password,
        )
    except ValueError as error:
        raise request_error(error) from error


@app.post("/api/devices/ssh-install")
async def ssh_install(request: SshInstallRequest, _: None = Depends(require_csrf_token)):
    from backend.ssh_manager import run_script_over_ssh

    script = _prepare_install_script(request)
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
async def ssh_install_stream(request: SshInstallRequest, _: None = Depends(require_csrf_token)):
    from backend.ssh_manager import stream_script_over_ssh

    script = _prepare_install_script(request)
    generator = stream_script_over_ssh(
        host=request.host,
        username=request.username,
        port=request.port,
        password=request.password,
        private_key_path=request.private_key_path,
        script=script,
    )
    return StreamingResponse(generator, media_type="text/plain; charset=utf-8")


class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", NoCacheStaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
