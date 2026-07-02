import os
from pathlib import Path

# --- Stack / instance defaults ---
STACK_NAME = os.getenv("DX_STACK_NAME", "dx-marketplace-web")
INSTANCE_TYPE = os.getenv("DX_INSTANCE_TYPE", "t3.xlarge")

# --- Region (used by aws_clients for the greengrass edge features) ---
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION")) or None

# --- Marketplace ---
MARKETPLACE_SSM_PARAM = (
    "/aws/service/marketplace/prod-ei6ws54bjw7to/dx-compiler-automation-2.3.0"
)
MARKETPLACE_URL = "https://aws.amazon.com/marketplace/pp/prodview-ev6ed5omu4ulo"

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

# --- Combined AWS Marketplace CloudFormation template (Deploy + Compile + Edge) ---
# web/backend/config.py -> parents[2] == repo root
COMBINED_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "infra" / "dx-compiler-greengrass-marketplace.yaml"
)

# --- Greengrass edge constants ---
DX_RUNTIME_COMPONENT = "com.deepx.dx-runtime"

# CloudFormation rejects an inline TemplateBody larger than this; larger
# templates must be uploaded to S3 and referenced via TemplateURL.
TEMPLATE_BODY_MAX_BYTES = 51200

# SSH key dir allowed for the edge SSH-install feature.
SSH_KEY_DIR = Path(os.getenv("DX_SSH_KEY_DIR", "~/.ssh")).expanduser().resolve()
