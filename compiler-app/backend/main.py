import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from config import STACK_NAME, MARKETPLACE_URL, AMI_OPTIONS
from aws_manager import (
    check_marketplace_subscription,
    deploy_stack,
    get_stack_status,
    get_stack_events,
    get_bucket_name,
    get_region,
    list_dx_compiler_stacks,
    select_stack,
    get_active_stack_name,
    set_active_stack_name,
)
from compiler import (
    create_job,
    upload_model,
    get_job,
    refresh_job_status,
    download_result,
    get_job_links,
    get_compiler_output_logs,
    JobStatus,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting DX Compiler Web App (region=%s)...", get_region())
    yield


app = FastAPI(
    title="DX Compiler Web",
    description="ONNX to DXNN compiler web service",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Setup APIs ──────────────────────────────────────────

@app.get("/api/setup/status")
async def setup_status():
    """전체 설정 상태: Marketplace 구독 + 스택 상태."""
    subscription = check_marketplace_subscription()
    active_name = get_active_stack_name()
    stack = get_stack_status(active_name)

    stack_ready = stack is not None and stack["status"] in (
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"
    )

    ami_options = [
        {"key": k, "label": v["label"], "requires_subscription": v["requires_subscription"]}
        for k, v in AMI_OPTIONS.items()
    ]

    return {
        "region": get_region(),
        "marketplace": {
            "subscribed": subscription["subscribed"],
            "ami_id": subscription["ami_id"],
            "subscribe_url": MARKETPLACE_URL,
        },
        "ami_options": ami_options,
        "stack": {
            "name": active_name,
            "status": stack["status"] if stack else "NOT_FOUND",
            "ready": stack_ready,
            "outputs": stack.get("outputs", {}) if stack else {},
        },
    }


@app.get("/api/setup/stacks")
async def list_stacks():
    """기존 DX Compiler 스택 목록을 반환."""
    try:
        stacks = list_dx_compiler_stacks()
        return {"stacks": stacks, "active": get_active_stack_name()}
    except Exception as e:
        logger.error("Failed to list stacks: %s", e)
        raise HTTPException(500, f"스택 목록 조회 실패: {str(e)}")


@app.post("/api/setup/stacks/select")
async def select_existing_stack(body: dict):
    """기존 스택을 선택하여 사용."""
    stack_name = body.get("stack_name")
    if not stack_name:
        raise HTTPException(400, "stack_name은 필수입니다.")

    try:
        result = select_stack(stack_name)
        return {
            "action": "selected",
            "stack": result,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Failed to select stack: %s", e)
        raise HTTPException(500, f"스택 선택 실패: {str(e)}")


@app.delete("/api/setup/stacks/{stack_name}")
async def delete_existing_stack(stack_name: str):
    """기존 스택을 삭제."""
    from aws_manager import delete_stack
    try:
        result = delete_stack(stack_name)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Failed to delete stack: %s", e)
        raise HTTPException(500, f"스택 삭제 실패: {str(e)}")


@app.post("/api/setup/deploy")
async def setup_deploy(body: dict = {}):
    """CF 스택 배포 시작. ami_type으로 AMI 선택 가능."""
    ami_type = body.get("ami_type", "marketplace") if body else "marketplace"

    ami_option = AMI_OPTIONS.get(ami_type)
    if not ami_option:
        raise HTTPException(400, f"Unknown AMI type: {ami_type}")

    if ami_option.get("requires_subscription"):
        subscription = check_marketplace_subscription()
        if not subscription["subscribed"]:
            raise HTTPException(
                400,
                f"AWS Marketplace에서 먼저 구독해주세요: {MARKETPLACE_URL}"
            )

    try:
        result = deploy_stack(ami_type=ami_type)
    except Exception as e:
        logger.error("Stack deployment failed: %s", e)
        raise HTTPException(500, f"스택 배포 실패: {str(e)}")

    return result


@app.get("/api/setup/deploy/status")
async def setup_deploy_status():
    """스택 배포 진행 상태 + 최근 이벤트."""
    stack = get_stack_status()
    events = get_stack_events(max_events=15)

    stack_ready = stack is not None and stack["status"] in (
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"
    )

    return {
        "stack_status": stack["status"] if stack else "NOT_FOUND",
        "ready": stack_ready,
        "outputs": stack.get("outputs", {}) if stack else {},
        "events": events,
    }


@app.get("/api/setup/template")
async def get_template(ami_type: str = "marketplace"):
    """CloudFormation 템플릿 파일 내용을 반환."""
    from config import CF_TEMPLATE_V2_PATH
    template_path = CF_TEMPLATE_V2_PATH
    if not template_path.exists():
        raise HTTPException(404, "템플릿 파일을 찾을 수 없습니다.")
    return Response(content=template_path.read_text(encoding="utf-8"), media_type="text/plain")


# ── Compiler APIs ───────────────────────────────────────

def _require_stack_ready():
    stack = get_stack_status(get_active_stack_name())
    if not stack or stack["status"] not in (
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"
    ):
        raise HTTPException(503, "스택이 준비되지 않았습니다. Setup을 먼저 완료해주세요.")


@app.get("/api/health")
async def health():
    active_name = get_active_stack_name()
    stack = get_stack_status(active_name)
    stack_ready = stack is not None and stack["status"] in (
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"
    )
    return {
        "status": "ok" if stack_ready else "setup_required",
        "stack_name": active_name,
        "stack_status": stack["status"] if stack else "NOT_FOUND",
        "region": get_region(),
    }


@app.post("/api/compile")
async def compile_model(
    onnx_file: UploadFile = File(...),
    config_file: UploadFile = File(...),
):
    _require_stack_ready()

    if not onnx_file.filename or not onnx_file.filename.lower().endswith(".onnx"):
        raise HTTPException(400, "ONNX 파일(.onnx)이 필요합니다.")

    if not config_file.filename or not config_file.filename.lower().endswith(".json"):
        raise HTTPException(400, "JSON 설정 파일(.json)이 필요합니다.")

    onnx_content = await onnx_file.read()
    config_content = await config_file.read()

    if len(onnx_content) == 0:
        raise HTTPException(400, "ONNX 파일이 비어있습니다.")
    if len(config_content) == 0:
        raise HTTPException(400, "JSON 설정 파일이 비어있습니다.")

    MAX_SIZE = 500 * 1024 * 1024  # 500MB
    if len(onnx_content) > MAX_SIZE:
        raise HTTPException(400, "ONNX 파일 크기가 500MB를 초과합니다.")

    job = create_job(onnx_file.filename, config_file.filename)
    logger.info("Created job %s for files %s + %s", job.job_id, onnx_file.filename, config_file.filename)

    try:
        upload_model(job.job_id, onnx_content, config_content)
    except Exception as e:
        logger.error("Upload failed for job %s: %s", job.job_id, e)
        raise HTTPException(500, f"파일 업로드 실패: {str(e)}")

    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "filename": job.original_filename,
        "message": "컴파일이 시작되었습니다. 상태를 확인해주세요.",
    }


@app.get("/api/jobs/{job_id}/status")
async def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")

    job = refresh_job_status(job_id)
    links = get_job_links(job_id)
    compiler_logs = get_compiler_output_logs(job_id)

    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "filename": job.original_filename,
        "error": job.error_message,
        "has_output": job.output_s3_key is not None,
        "instance_id": job.instance_id,
        "links": links,
        "logs": job.logs,
        "compiler_logs": compiler_logs,
    }


@app.get("/api/jobs/{job_id}/download")
async def download(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")

    if job.status != JobStatus.SUCCEEDED:
        raise HTTPException(400, "컴파일이 아직 완료되지 않았습니다.")

    try:
        content, filename = download_result(job_id)
    except Exception as e:
        logger.error("Download failed for job %s: %s", job_id, e)
        raise HTTPException(500, f"파일 다운로드 실패: {str(e)}")

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# /compiler 경로도 index.html 반환 (SPA 라우팅)
@app.get("/compiler")
async def compiler_page():
    return FileResponse("../frontend/index.html")


# Serve frontend static files
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
