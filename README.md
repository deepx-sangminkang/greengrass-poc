# DEEPX Compiler + Greengrass Solution (AWS Marketplace)

One AWS Marketplace **AMI with CloudFormation** product that does two things from a
single subscription:

1. **Compile** — upload an ONNX model + JSON config to S3; a Step Functions pipeline
   launches the DEEPX Compiler AMI, runs `dxcom`, and writes the `.dxnn` back to S3.
2. **Edge deploy (Greengrass)** — provision AWS IoT Greengrass V2 infrastructure that
   deploys the DEEPX NPU runtime (`com.deepx.dx-runtime`) to edge core devices.

End-to-end: **ONNX → (cloud compile) → DXNN → (edge Greengrass) → runs on the NPU.**

## Delivery model — option B (one combined template)

The Marketplace deliverable is **one CloudFormation template** attached to the DEEPX
Compiler AMI (which is already listed as *AMI with CloudFormation*). The template
launches the AMI (the compile pipeline) **and** provisions the Greengrass infra in the
same stack. This is compliant because:

- The delivery template still launches the seller AMI (via the compile pipeline), so it
  is a genuine AMI-with-CloudFormation template.
- Marketplace has **no rule against one template doing two functions** or including
  IoT/Greengrass resources — the enforced rules are ImageId-must-be-a-parameter,
  least-privilege IAM, no default-open SSH/RDP, and launch-in-all-regions (verified
  against the AWS Marketplace Seller Guide, `cloudformation.html`).
- A standalone AMI-less Greengrass delivery template (option A) was a gray-area risk;
  combining into the AMI-launching template avoids that entirely.

Buyer flow: **Subscribe → Configure → Launch the template → one stack** gives both the
compile pipeline and the Greengrass edge-deploy infra.

## Layout

```
greengrass-poc/
  infra/
    dx-compiler-greengrass-marketplace.yaml   # THE combined delivery template (deliverable)
    sources/
      compiler-marketplace-v2.yaml            # source A: compiler pipeline (reference)
      greengrass-runtime.yaml                 # source B: greengrass runtime (reference)
  packer/            # DEEPX Compiler AMI build (Packer)
  imagebuilder/      # DEEPX Compiler AMI build (EC2 Image Builder)
  compiler-app/      # compiler dev web console (dev convenience — NOT the deliverable)
  greengrass-app/    # greengrass dev web console (dev convenience — NOT the deliverable)
```

The combined template is a text-level **union** of the two source templates (no logical-id,
parameter, or output collisions): **10 parameters, 19 resources, 8 outputs**.

- Compile side: `ModelBucket`, `TriggerFunction` (+role/permission/loggroup), `CompilerDocument`,
  `CompilerStateMachine` (+loggroups), `CompilerSecurityGroup`, `StepFunctionsRole`,
  `EC2InstanceRole`/`Profile`. Params: `ImageId`, `ModelBucketName`, `InstanceType`, `VpcId`, `SubnetId`.
- Greengrass side: `GreengrassThingGroup`, `GreengrassTokenExchangeRole`,
  `ComponentPublishFunction` (+role), `DxRuntimeComponent`, `GreengrassDeployment`.
  Params: `ProjectName`, `ThingGroupName`, `DxRuntimeComponentVersion`, `DxRuntimeArtifactBaseUrl`,
  `CliComponentVersion`.

## Validate

```bash
cfn-lint infra/dx-compiler-greengrass-marketplace.yaml
# validate-template body limit is 51200 bytes; this template is larger, so validate via S3:
aws s3 cp infra/dx-compiler-greengrass-marketplace.yaml s3://<bucket>/combined.yaml
aws cloudformation validate-template \
  --template-url https://<bucket>.s3.<region>.amazonaws.com/combined.yaml
```

Current status: `cfn-lint` clean, `validate-template` passes (10 params, CAPABILITY_IAM).

## TODO (before Marketplace submission)

- Merge `compiler-app/` + `greengrass-app/` into one dev console (two tabs). Dev-only; not
  the Marketplace deliverable.
- Per-template 1100x700 architecture diagram (required for the delivery template).
- Usage instructions disclosing every IAM role the template creates, the IoT/Greengrass
  resources, and the deploy-time download of the DX-Runtime artifacts from the public
  `deepx-public-bucket/dx-runtime/` (external dependency — must be disclosed and self-service;
  populate the bucket with `scripts/publish-dx-runtime-artifacts.sh`).
- Least-privilege pass on the token-exchange, StepFunctions, and EC2 instance roles.
- Confirm the runtime `ec2:runInstances` (Step Functions) launch pattern + this combined
  shape with AWS Marketplace seller-ops.
