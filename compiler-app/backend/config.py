import os
from pathlib import Path

STACK_NAME = os.getenv("DX_STACK_NAME", "dx-compiler-web")
INSTANCE_TYPE = os.getenv("DX_INSTANCE_TYPE", "t3.xlarge")

MARKETPLACE_SSM_PARAM = (
    "/aws/service/marketplace/prod-ei6ws54bjw7to/dx-compiler-automation-2.3.0"
)
MARKETPLACE_URL = (
    "https://aws.amazon.com/marketplace/pp/prodview-ev6ed5omu4ulo"
)

AMI_OPTIONS = {
    "marketplace": {
        "label": "DEEPX Compiler Solution 2.3.0 (Marketplace, 권장)",
        "ssm_param": MARKETPLACE_SSM_PARAM,
        "requires_subscription": True,
    },
    "ubuntu2204": {
        "label": "Ubuntu 22.04 LTS",
        "ssm_param": "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id",
        "requires_subscription": False,
    },
}

CF_TEMPLATE_PATH = Path(__file__).parent.parent / "dx-compiler-marketplace.yaml"
CF_TEMPLATE_V2_PATH = Path(__file__).parent.parent / "dx-compiler-marketplace-v2.yaml"

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
