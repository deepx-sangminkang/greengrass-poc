from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from backend.setup_manager import (
    TEMPLATE_BODY_MAX_BYTES,
    deploy_stack,
    ensure_staging_bucket,
    get_stack_status,
    list_stacks,
)


def test_get_stack_status_returns_outputs():
    client = boto3.client(
        "cloudformation",
        region_name="ap-northeast-2",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    stubber = Stubber(client)
    stubber.add_response(
        "describe_stacks",
        {
            "Stacks": [
                {
                    "StackName": "DxRuntimeGreengrassWebStack",
                    "StackStatus": "CREATE_COMPLETE",
                    "CreationTime": datetime(2026, 6, 4, tzinfo=timezone.utc),
                    "Outputs": [
                        {"OutputKey": "ArtifactBucketName", "OutputValue": "bucket-a"},
                        {"OutputKey": "KinesisVideoStreamName", "OutputValue": "stream-a"},
                    ],
                }
            ]
        },
        {"StackName": "DxRuntimeGreengrassWebStack"},
    )

    with stubber:
        status = get_stack_status(client, "DxRuntimeGreengrassWebStack")

    assert status["ready"] is True
    assert status["outputs"]["ArtifactBucketName"] == "bucket-a"


class _FakeWaiter:
    def __init__(self, recorder, name):
        self._recorder = recorder
        self._name = name

    def wait(self, **kwargs):
        self._recorder.append(("wait", self._name))


class _FakeCfn:
    def __init__(self, describe_sequence, no_updates=False):
        self._describe_sequence = list(describe_sequence)
        self.calls = []
        self._no_updates = no_updates

    def describe_stacks(self, StackName):
        result = self._describe_sequence.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def create_stack(self, **kwargs):
        self.calls.append(("create_stack", kwargs))

    def update_stack(self, **kwargs):
        self.calls.append(("update_stack", kwargs))
        if self._no_updates:
            raise ClientError(
                {"Error": {"Code": "ValidationError", "Message": "No updates are to be performed."}},
                "UpdateStack",
            )

    def get_waiter(self, name):
        self.calls.append(("get_waiter", name))
        return _FakeWaiter(self.calls, name)


def _complete_stack(status):
    return {
        "Stacks": [
            {
                "StackName": "X",
                "StackStatus": status,
                "CreationTime": datetime(2026, 6, 4, tzinfo=timezone.utc),
                "Outputs": [{"OutputKey": "ArtifactBucketName", "OutputValue": "bucket-a"}],
            }
        ]
    }


def test_deploy_stack_creates_when_missing():
    not_found = ClientError(
        {"Error": {"Code": "ValidationError", "Message": "Stack with id X does not exist"}},
        "DescribeStacks",
    )
    cfn = _FakeCfn([not_found, _complete_stack("CREATE_COMPLETE")])

    result = deploy_stack(cfn, "X", "BODY", {"ProjectName": "p"})

    assert result["action"] == "create"
    assert result["status"] == "succeeded"
    assert result["outputs"]["ArtifactBucketName"] == "bucket-a"
    call_names = [name for name, _ in cfn.calls]
    assert "create_stack" in call_names
    assert ("get_waiter", "stack_create_complete") in cfn.calls
    create_kwargs = next(kw for name, kw in cfn.calls if name == "create_stack")
    assert create_kwargs["Capabilities"] == ["CAPABILITY_IAM"]
    assert create_kwargs["Parameters"] == [{"ParameterKey": "ProjectName", "ParameterValue": "p"}]


def test_deploy_stack_updates_existing():
    cfn = _FakeCfn([_complete_stack("UPDATE_COMPLETE"), _complete_stack("UPDATE_COMPLETE")])

    result = deploy_stack(cfn, "X", "BODY", {})

    assert result["action"] == "update"
    assert result["status"] == "succeeded"
    call_names = [name for name, _ in cfn.calls]
    assert "update_stack" in call_names
    assert ("get_waiter", "stack_update_complete") in cfn.calls


def test_deploy_stack_handles_no_updates():
    cfn = _FakeCfn([_complete_stack("UPDATE_COMPLETE"), _complete_stack("UPDATE_COMPLETE")], no_updates=True)

    result = deploy_stack(cfn, "X", "BODY", {})

    assert result["action"] == "none"
    assert result["status"] == "succeeded"
    assert result["outputs"]["ArtifactBucketName"] == "bucket-a"


class _FakeS3:
    def __init__(self, head_error=None):
        self.calls = []
        self._head_error = head_error

    def head_bucket(self, **kwargs):
        self.calls.append(("head_bucket", kwargs))
        if self._head_error is not None:
            raise self._head_error

    def create_bucket(self, **kwargs):
        self.calls.append(("create_bucket", kwargs))

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))


class _FakeSts:
    def __init__(self, account_id="123456789012"):
        self._account_id = account_id

    def get_caller_identity(self):
        return {"Account": self._account_id}


def _not_found_bucket_error():
    return ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}},
        "HeadBucket",
    )


def test_deploy_stack_uses_template_url_for_large_body():
    big_body = "A" * (TEMPLATE_BODY_MAX_BYTES + 1)
    not_found = ClientError(
        {"Error": {"Code": "ValidationError", "Message": "Stack with id X does not exist"}},
        "DescribeStacks",
    )
    cfn = _FakeCfn([not_found, _complete_stack("CREATE_COMPLETE")])
    s3 = _FakeS3(head_error=_not_found_bucket_error())

    result = deploy_stack(
        cfn,
        "X",
        big_body,
        {},
        s3_client=s3,
        sts_client=_FakeSts(),
        region="ap-northeast-2",
    )

    assert result["status"] == "succeeded"
    create_kwargs = next(kw for name, kw in cfn.calls if name == "create_stack")
    assert "TemplateBody" not in create_kwargs
    assert create_kwargs["TemplateURL"] == (
        "https://dx-greengrass-cfn-staging-123456789012-ap-northeast-2"
        ".s3.ap-northeast-2.amazonaws.com/templates/X.yaml"
    )
    assert any(name == "put_object" for name, _ in s3.calls)
    assert any(name == "create_bucket" for name, _ in s3.calls)


def test_deploy_stack_keeps_template_body_for_small_body():
    not_found = ClientError(
        {"Error": {"Code": "ValidationError", "Message": "Stack with id X does not exist"}},
        "DescribeStacks",
    )
    cfn = _FakeCfn([not_found, _complete_stack("CREATE_COMPLETE")])
    s3 = _FakeS3()

    deploy_stack(
        cfn,
        "X",
        "small",
        {},
        s3_client=s3,
        sts_client=_FakeSts(),
        region="ap-northeast-2",
    )

    create_kwargs = next(kw for name, kw in cfn.calls if name == "create_stack")
    assert create_kwargs["TemplateBody"] == "small"
    assert "TemplateURL" not in create_kwargs
    assert s3.calls == []


def test_ensure_staging_bucket_skips_create_when_present():
    s3 = _FakeS3()
    ensure_staging_bucket(s3, "staging-bucket", "ap-northeast-2")
    assert [name for name, _ in s3.calls] == ["head_bucket"]


def test_ensure_staging_bucket_uses_location_constraint_outside_us_east_1():
    s3 = _FakeS3(head_error=_not_found_bucket_error())
    ensure_staging_bucket(s3, "staging-bucket", "ap-northeast-2")
    create_kwargs = next(kw for name, kw in s3.calls if name == "create_bucket")
    assert create_kwargs["CreateBucketConfiguration"] == {
        "LocationConstraint": "ap-northeast-2"
    }


def test_ensure_staging_bucket_omits_location_constraint_for_us_east_1():
    s3 = _FakeS3(head_error=_not_found_bucket_error())
    ensure_staging_bucket(s3, "staging-bucket", "us-east-1")
    create_kwargs = next(kw for name, kw in s3.calls if name == "create_bucket")
    assert "CreateBucketConfiguration" not in create_kwargs


def test_list_stacks_marks_managed_and_skips_deleted():
    client = boto3.client(
        "cloudformation",
        region_name="ap-northeast-2",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    stubber = Stubber(client)
    stubber.add_response(
        "describe_stacks",
        {
            "Stacks": [
                {
                    "StackName": "managed-stack",
                    "StackStatus": "CREATE_COMPLETE",
                    "CreationTime": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "Outputs": [
                        {"OutputKey": "ThingGroupName", "OutputValue": "g"},
                        {"OutputKey": "TokenExchangeRoleName", "OutputValue": "r"},
                        {"OutputKey": "ThingGroupName", "OutputValue": "g"},
                        {"OutputKey": "TokenExchangeRoleName", "OutputValue": "r"},
                    ],
                },
                {
                    "StackName": "other-stack",
                    "StackStatus": "CREATE_COMPLETE",
                    "CreationTime": datetime(2026, 6, 2, tzinfo=timezone.utc),
                    "Outputs": [],
                },
                {
                    "StackName": "deleted-stack",
                    "StackStatus": "DELETE_COMPLETE",
                    "CreationTime": datetime(2026, 6, 3, tzinfo=timezone.utc),
                    "Outputs": [],
                },
                {
                    "StackName": "rolled-back-stack",
                    "StackStatus": "ROLLBACK_COMPLETE",
                    "CreationTime": datetime(2026, 6, 4, tzinfo=timezone.utc),
                    "Outputs": [],
                },
                {
                    "StackName": "delete-failed-stack",
                    "StackStatus": "DELETE_FAILED",
                    "CreationTime": datetime(2026, 6, 5, tzinfo=timezone.utc),
                    "Outputs": [],
                },
                {
                    "StackName": "update-rolled-back-stack",
                    "StackStatus": "UPDATE_ROLLBACK_COMPLETE",
                    "CreationTime": datetime(2026, 6, 6, tzinfo=timezone.utc),
                    "Outputs": [],
                },
            ]
        },
        {},
    )

    with stubber:
        stacks = list_stacks(client)

    names = [stack["name"] for stack in stacks]
    assert "deleted-stack" not in names
    assert "rolled-back-stack" not in names
    assert "delete-failed-stack" not in names
    assert "update-rolled-back-stack" not in names
    assert names[0] == "managed-stack"
    managed = next(stack for stack in stacks if stack["name"] == "managed-stack")
    other = next(stack for stack in stacks if stack["name"] == "other-stack")
    assert managed["managed"] is True
    assert other["managed"] is False
