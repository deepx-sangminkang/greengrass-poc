import json

import yaml

from backend import config


class _CfnLoader(yaml.SafeLoader):
    pass


def _cfn_multi_constructor(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node, deep=True)}
    return {tag_suffix: loader.construct_mapping(node, deep=True)}


_CfnLoader.add_multi_constructor("!", _cfn_multi_constructor)


def _load_template() -> dict:
    template_text = config.INFRA_DIR.joinpath("template.yaml").read_text(encoding="utf-8")
    return yaml.load(template_text, Loader=_CfnLoader)


def _template_text() -> str:
    return config.INFRA_DIR.joinpath("template.yaml").read_text(encoding="utf-8")


def _resources_of_type(template: dict, resource_type: str) -> list[dict]:
    return [
        resource
        for resource in template["Resources"].values()
        if resource["Type"] == resource_type
    ]


def _as_actions(action) -> list[str]:
    return action if isinstance(action, list) else [action]


def _role_actions(role: dict) -> list[str]:
    return [
        action
        for policy in role["Properties"].get("Policies", [])
        for statement in policy["PolicyDocument"]["Statement"]
        for action in _as_actions(statement["Action"])
    ]


# The template is scoped to installing the com.deepx.dx-runtime component only.
# dx_stream/run_model/parse_model/device_health and their S3/EventBridge/KVS
# trigger plumbing were removed for the AMI+CloudFormation Marketplace listing.


def test_template_is_scoped_to_dx_runtime_only():
    template = _load_template()

    # No S3 artifact bucket, no KVS, no EventBridge rules, no trigger Lambdas.
    assert _resources_of_type(template, "AWS::S3::Bucket") == []
    assert _resources_of_type(template, "AWS::KinesisVideo::Stream") == []
    assert _resources_of_type(template, "AWS::Events::Rule") == []
    assert _resources_of_type(template, "AWS::Lambda::Permission") == []

    assert len(_resources_of_type(template, "AWS::IoT::ThingGroup")) == 1
    # Only the token-exchange role and the component-publisher role remain.
    assert len(_resources_of_type(template, "AWS::IAM::Role")) == 2
    # Only the component-publisher Lambda remains.
    assert len(_resources_of_type(template, "AWS::Lambda::Function")) == 1
    assert len(_resources_of_type(template, "AWS::GreengrassV2::Deployment")) == 1

    assert set(template["Resources"]) == {
        "GreengrassThingGroup",
        "GreengrassTokenExchangeRole",
        "ComponentPublishFunctionRole",
        "ComponentPublishFunction",
        "DxRuntimeComponent",
        "GreengrassDeployment",
    }


def test_outputs_reference_only_surviving_resources():
    template = _load_template()
    assert set(template["Outputs"]) == {
        "ProjectName",
        "ThingGroupName",
        "TokenExchangeRoleName",
        "GreengrassDeploymentId",
    }


def test_parameters_are_trimmed_to_scope():
    template = _load_template()
    assert set(template["Parameters"]) == {
        "ProjectName",
        "ThingGroupName",
        "DxRuntimeComponentVersion",
        "DxRuntimeGitRef",
        "CliComponentVersion",
    }


def test_template_does_not_precreate_stream():
    template = _load_template()
    assert "ShouldCreateStream" not in template.get("Conditions", {})
    assert "KinesisVideoStreamName" not in template["Parameters"]
    assert _resources_of_type(template, "AWS::KinesisVideo::Stream") == []


def test_template_thing_group_name_is_stack_unique_by_default():
    template = _load_template()

    assert template["Parameters"]["ThingGroupName"]["Default"] == ""
    assert "HasThingGroupName" in template["Conditions"]
    thing_group = _resources_of_type(template, "AWS::IoT::ThingGroup")[0]
    name = thing_group["Properties"]["ThingGroupName"]
    assert "If" in name
    condition, when_set, when_empty = name["If"]
    assert condition == "HasThingGroupName"
    assert when_set == {"Ref": "ThingGroupName"}
    assert "${AWS::StackName}" in when_empty["Sub"]


def test_template_is_self_contained_without_cdk_assets():
    template_text = _template_text()
    assert "cdk-bootstrap" not in template_text
    assert "BootstrapVersion" not in template_text
    assert json.dumps(_load_template())


def test_token_exchange_role_is_logs_only():
    template = _load_template()
    role = template["Resources"]["GreengrassTokenExchangeRole"]
    actions = _role_actions(role)

    assert actions, "token-exchange role should still grant logging"
    # Every removed component's permission (S3 artifact read, KVS, device shadow)
    # is gone; only CloudWatch logging remains.
    assert all(action.startswith("logs:") for action in actions)
    for gone in ("s3:", "kinesisvideo:", "iot:UpdateThingShadow", "iot:GetThingShadow"):
        assert not any(action.startswith(gone) or action == gone for action in actions)


def test_dx_runtime_recipe_keeps_dxstream_runtime_and_drops_samples():
    template = _load_template()
    recipe = template["Resources"]["DxRuntimeComponent"]["Properties"]["Recipe"]

    # dx_rt / driver / firmware / dx_stream runtime targets stay.
    assert "--target=dx_rt_npu_linux_driver" in recipe
    assert "--target=dx_rt" in recipe
    assert "--target=dx_fw" in recipe
    assert "--target=dx_stream" in recipe

    # Sample model/video staging is removed (it fed the removed apps).
    assert "setup_sample_models.sh" not in recipe
    assert "setup_sample_videos.sh" not in recipe
    assert "dance-group.mov" not in recipe
    assert "yolo26n.dxnn" not in recipe
    assert "NEED_SAMPLES" not in recipe


def test_deployment_targets_only_dx_runtime_and_cli():
    template = _load_template()
    deployment = _resources_of_type(template, "AWS::GreengrassV2::Deployment")[0]

    assert set(deployment["DependsOn"]) == {"DxRuntimeComponent"}
    components = deployment["Properties"]["Components"]
    assert set(components) == {"com.deepx.dx-runtime", "aws.greengrass.Cli"}
    assert components["com.deepx.dx-runtime"]["ComponentVersion"] == {
        "Ref": "DxRuntimeComponentVersion"
    }
    # LogManager (which only uploaded the removed apps' result logs) is gone.
    assert "aws.greengrass.LogManager" not in components


def test_template_publishes_dx_runtime_component_idempotently():
    template = _load_template()
    resources = template["Resources"]

    publishers = {
        name: resource
        for name, resource in resources.items()
        if resource["Type"] == "Custom::ComponentPublish"
    }
    assert set(publishers) == {"DxRuntimeComponent"}

    props = publishers["DxRuntimeComponent"]["Properties"]
    assert props["ServiceToken"] == {"GetAtt": "ComponentPublishFunction.Arn"}
    assert props["ComponentName"] == "com.deepx.dx-runtime"
    assert "{{ version }}" in props["Recipe"]
    assert props["Substitutions"]["version"] is not None

    publisher_role = resources["ComponentPublishFunctionRole"]
    assert "greengrass:CreateComponentVersion" in _role_actions(publisher_role)
