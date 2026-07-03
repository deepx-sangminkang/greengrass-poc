import os
import secrets
from pathlib import Path

AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-northeast-2"))
PROJECT_NAME = os.getenv("DX_PROJECT_NAME", "dx-runtime-greengrass-web")
STACK_NAME = os.getenv("DX_STACK_NAME", "DXGreengrassStack")
THING_GROUP_NAME = os.getenv("DX_THING_GROUP_NAME", "")
DX_RUNTIME_COMPONENT = "com.deepx.dx-runtime"
TOKEN_EXCHANGE_SERVICE_COMPONENT = "aws.greengrass.TokenExchangeService"
TOKEN_EXCHANGE_SERVICE_VERSION = ">=2.0.0 <3.0.0"
DX_RUNTIME_RECIPE_VERSION = "1.0.8"
DEFAULT_COMPONENT_VERSION = os.getenv("DX_COMPONENT_VERSION", DX_RUNTIME_RECIPE_VERSION)
ALLOWED_ORIGINS = tuple(
    origin.strip()
    for origin in os.getenv(
        "DX_ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
)
CSRF_TOKEN = os.getenv("DX_CSRF_TOKEN") or secrets.token_urlsafe(32)

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
INFRA_DIR = REPO_ROOT / "infra"
SSH_KEY_DIR = Path(os.getenv("DX_SSH_KEY_DIR", "~/.ssh")).expanduser().resolve()
