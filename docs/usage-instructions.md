# Usage Instructions — DEEPX Compiler + Greengrass Solution

AWS Marketplace listing usage instructions for the combined **AMI with
CloudFormation** product. These disclose everything the CloudFormation template
provisions in the buyer's account, the external dependencies it contacts, and how
to use the product self-service.

## Product overview

One CloudFormation stack delivers two capabilities:

1. **Compile** — turn ONNX models into DEEPX `.dxnn` files in your account using the
   DEEPX Compiler AMI (`dxcom`), driven by S3 uploads.
2. **Edge deploy** — provision AWS IoT Greengrass V2 so the DEEPX NPU runtime
   (`com.deepx.dx-runtime`) is deployed to your edge core devices.

> **Hardware:** Any DEEPX NPU hardware required to run compiled models on the edge
> must be obtained separately. It is not sold through AWS Marketplace and is not
> included in this listing's price.

## Prerequisites

- An active subscription to the DEEPX Compiler AMI (this product).
- A VPC and subnet with outbound internet access (or VPC endpoints for S3, SSM, EC2,
  Step Functions, and CloudWatch Logs) — used by the compile pipeline.
- For edge deploy: one or more AWS IoT Greengrass V2 core devices with DEEPX NPU
  hardware, and passwordless `sudo` for `ggc_user` on each device (the runtime
  installs a kernel driver/firmware).

## What the stack creates in your account

### Compile pipeline
- `AWS::S3::Bucket` (**ModelBucket**) — inputs (`*.onnx`/`*.json`) and outputs (`*.dxnn`).
- `AWS::Lambda::Function` (**TriggerFunction**) — pairs an uploaded onnx+json and starts the workflow.
- `AWS::StepFunctions::StateMachine` (**CompilerStateMachine**) — launches a compiler EC2
  instance from the DEEPX Compiler AMI, runs `dxcom` via SSM, uploads the `.dxnn`, and
  terminates the instance on both success and failure paths.
- `AWS::SSM::Document` (**CompilerDocument**), `AWS::EC2::SecurityGroup`
  (**CompilerSecurityGroup**, egress-443-only), and CloudWatch log groups.

### Edge deploy (Greengrass)
- `AWS::IoT::ThingGroup` (**GreengrassThingGroup**) — deployment target for core devices.
- `AWS::GreengrassV2::Deployment` — deploys `com.deepx.dx-runtime` + `aws.greengrass.Cli`.
- `Custom::ComponentPublish` (**DxRuntimeComponent**) via a Lambda that idempotently
  publishes the private `com.deepx.dx-runtime` component version.

### IAM roles created (purpose + scope)
| Role | Purpose | Scope |
|------|---------|-------|
| **TriggerLambdaRole** | run TriggerFunction | CloudWatch logs (managed basic-exec); `states:StartExecution` on this stack's state machine; `s3:ListBucket`/`GetObject` on ModelBucket only |
| **StepFunctionsRole** | run the workflow | `ec2:RunInstances`/`CreateTags`/`TerminateInstances` scoped to this account/region (terminate conditioned on the `Project=DX-Compiler-Automation` tag); `ssm:SendCommand` on CompilerDocument + instances; `iam:PassRole` only to EC2InstanceRole; scoped CloudWatch Logs |
| **EC2InstanceRole** (+profile) | compiler instance | `AmazonSSMManagedInstanceCore`; `s3:GetObject`/`PutObject`(AES256)/`ListBucket` on ModelBucket `*.onnx`/`*.json`/`*.dxnn` only; scoped execution-log writes |
| **GreengrassTokenExchangeRole** | edge core devices | CloudWatch Logs scoped to `/aws/greengrass/*` only |
| **ComponentPublishFunctionRole** | component publisher Lambda | CloudWatch logs; `greengrass:CreateComponentVersion`/`DescribeComponent`/`ListComponentVersions` scoped to the `com.deepx.dx-runtime` component only |

The template requires `CAPABILITY_IAM`. No long-term access keys are created or requested;
all compute uses IAM roles.

## External dependencies (contacted at deploy/run time)

- **Edge runtime artifacts:** each core device downloads four prebuilt artifacts over
  unauthenticated HTTPS from the public bucket `DxRuntimeArtifactBaseUrl` (default
  `https://deepx-public-bucket.s3.ap-northeast-2.amazonaws.com/dx-runtime`):
  `dxrt-driver-dkms_2.5.1-2_all.deb`, `libdxrt-bin_3.4.0_{amd64,arm64}.deb` (dx_rt prebuilt
  Debian package), `fw.bin`, and `dx_stream.tar.gz`. On first deployment it installs the NPU
  driver and dx_rt via `apt`, builds dx_stream from source, and flashes the firmware
  (`dxrt-cli -u fw.bin`), in the order driver → dx_rt → dx_fw → dx_stream. This requires
  outbound internet on the edge device (but no S3 credentials, since the bucket is public).
  Populate the bucket with `scripts/publish-dx-runtime-artifacts.sh`; DEEPX is responsible
  for the artifacts' availability.
- **Compiler AMI:** `dxcom` and the calibration dataset are pre-baked into the DEEPX
  Compiler AMI (no deploy-time download for compilation).

## How to use (self-service)

1. **Subscribe** to the product on AWS Marketplace and **Launch** the CloudFormation
   template (choose your region; provide VpcId, SubnetId, and a globally-unique
   ModelBucketName). The `ImageId` resolves automatically from your subscription.
2. **Compile:** upload a `model.onnx` and its `model.json` config to the same prefix in
   the stack's S3 bucket. The `.dxnn` appears in the same prefix when compilation finishes.
   Watch progress in Step Functions / CloudWatch Logs.
3. **Edge deploy:** register your DEEPX NPU devices as Greengrass core devices in the
   created thing group; they receive `com.deepx.dx-runtime` automatically.
4. Deliver the compiled `.dxnn` to the edge device and run it on the NPU.

No step requires seller approval; deployment and usage are fully self-service.
