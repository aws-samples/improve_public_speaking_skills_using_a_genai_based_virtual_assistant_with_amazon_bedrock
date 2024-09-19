# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import aws_cdk as core
import aws_cdk.assertions as assertions
from infra.infra_stack import InfraStack

def test_s3_bucket_creation():
    app = core.App()
    stack = InfraStack(app, "PublicSpeakingMentorAIAssistant")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties("AWS::S3::Bucket", {
    })

def test_step_functions_state_machine_creation():
    app = core.App()
    stack = InfraStack(app, "PublicSpeakingMentorAIAssistant")
    template = assertions.Template.from_stack(stack)

    template.resource_count_is("AWS::StepFunctions::StateMachine", 1)

def test_sns_topic_creation():
    app = core.App()
    stack = InfraStack(app, "PublicSpeakingMentorAIAssistant")
    template = assertions.Template.from_stack(stack)

    template.resource_count_is("AWS::SNS::Topic", 1)

def test_eventbridge_rule_creation():
    app = core.App()
    stack = InfraStack(app, "PublicSpeakingMentorAIAssistant")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties("AWS::Events::Rule", {
        "EventPattern": {
            "source": ["aws.s3"],
            "detail-type": ["Object Created"],
            "detail": {
                "object": {
                    "key": [{"prefix": "raw-audio-files/"}]
                }
            }
        }
    })

def test_cognito_user_pool_creation():
    app = core.App()
    stack = InfraStack(app, "PublicSpeakingMentorAIAssistant")
    template = assertions.Template.from_stack(stack)

    template.resource_count_is("AWS::Cognito::UserPool", 1)
    template.resource_count_is("AWS::Cognito::UserPoolClient", 1)

def test_secrets_manager_secret_creation():
    app = core.App()
    stack = InfraStack(app, "PublicSpeakingMentorAIAssistant")
    template = assertions.Template.from_stack(stack)

    template.resource_count_is("AWS::SecretsManager::Secret", 1)

def test_ssm_parameter_creation():
    app = core.App()
    stack = InfraStack(app, "PublicSpeakingMentorAIAssistant")
    template = assertions.Template.from_stack(stack)

    template.resource_count_is("AWS::SSM::Parameter", 2)
    template.has_resource_properties("AWS::SSM::Parameter", {
        "Name": "/psmb/s3_bucket"
    })
    template.has_resource_properties("AWS::SSM::Parameter", {
        "Name": "/psmb/statemachine_arn"
    })
