# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from aws_cdk import (
    Duration,
    Stack,
    aws_s3 as s3,
    RemovalPolicy,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as _lambda,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_iam as iam,
    aws_sns as sns,
    aws_ssm as ssm,
    aws_sns_subscriptions as subscriptions,
    aws_bedrock as bedrock,
    aws_cognito as cognito,
    aws_secretsmanager as secretsmanager,
    SecretValue,
    CfnOutput
)

from constructs import Construct

from webapp.utils.config_file import Config

class InfraStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create an S3 bucket
        bucket = s3.Bucket(self, "PublicSpeakingMentorAIAssistantBucket",
                          event_bridge_enabled=True,
                          removal_policy=RemovalPolicy.DESTROY,  # Set the removal policy
                          auto_delete_objects=True  # Automatically delete objects when the bucket is deleted
        )

        # Create a Lambda function to handle Bedrock prompt generation & large payload sizes
        prepare_bedrock_prompts_lambda = _lambda.Function(self, "prepare_bdrock_prompts",
                                    description="Lambda function invoked from Step Functions to prepare Bedrock prompts for Public Speaking GenAI Assistant",
                                    runtime=_lambda.Runtime.PYTHON_3_12,
                                    handler="prepare_bedrock_prompts.lambda_handler",
                                    timeout=Duration.seconds(30),
                                    architecture=_lambda.Architecture.ARM_64,
                                    code=_lambda.Code.from_asset("./infra/lambda"))
        
        # Add inline policy to allow Lamnda to read/write to a specific S3 bucket
        prepare_bedrock_prompts_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject", "s3:GetObject"],
                resources=[bucket.bucket_arn, f"{bucket.bucket_arn}/*"]
            )
        )

        # Create an IAM role for the Step Functions state machine
        state_machine_role = iam.Role(self, "PublicSpeakingMentorAIAssistantStateMachineRole",
                                     assumed_by=iam.ServicePrincipal("states.amazonaws.com"))

        # Create an SNS topic
        topic = sns.Topic(self, "PublicSpeakingMentorAIAssistantTopic")

        # Create a customer managed IAM policy for CloudWatchLogsDeliveryFullAccess
        sfn_cloudwatch_logs_delivery_policy = iam.ManagedPolicy(self, "CloudWatchLogsDeliveryFullAccessPolicy",
                                                            managed_policy_name="CloudWatchLogsDeliveryFullAccess",
                                                            statements=[
                                                                iam.PolicyStatement(
                                                                    effect=iam.Effect.ALLOW,
                                                                    actions=[
                                                                        "logs:CreateLogDelivery",
                                                                        "logs:GetLogDelivery",
                                                                        "logs:UpdateLogDelivery",
                                                                        "logs:DeleteLogDelivery",
                                                                        "logs:ListLogDeliveries",
                                                                        "logs:PutResourcePolicy",
                                                                        "logs:DescribeResourcePolicies",
                                                                        "logs:DescribeLogGroups"
                                                                    ],
                                                                    resources=["*"]
                                                                )
                                                            ])

        # Attach the necessary IAM policies to the role
        state_machine_role.add_managed_policy(sfn_cloudwatch_logs_delivery_policy)

        # Define the Step Functions state machine
        start_transcription_task = tasks.CallAwsService(self, "StartTranscriptionJob",
                                                        service="transcribe",
                                                        action="startTranscriptionJob",
                                                        parameters={
                                                            "TranscriptionJobName": sfn.JsonPath.string_at("$$.Execution.Name"),
                                                            "Media": {
                                                                "MediaFileUri": sfn.JsonPath.format("s3://{}/{}", sfn.JsonPath.string_at("$.detail.bucket.name"), sfn.JsonPath.string_at("$.detail.object.key"))
                                                            },
                                                            "LanguageCode": "en-US",
                                                            "OutputBucketName": bucket.bucket_name,
                                                            "OutputKey": sfn.JsonPath.format("transcribed-text-files/{}-temp.json", sfn.JsonPath.string_at("$.detail.object.key"))
                                                        },
                                                        iam_resources=["*"],
                                                        # role=state_machine_role,
                                                        result_path="$.TranscriptionResult") 

        wait_for_transcription_task = sfn.Wait(self, "WaitForTranscriptionJobToComplete",
                                  time=sfn.WaitTime.duration(Duration.seconds(10)))
        
        get_transcription_task = tasks.CallAwsService(self, "GetTranscriptionJobStatus",
                                                        service="transcribe",
                                                        action="getTranscriptionJob",
                                                        parameters={
                                                            "TranscriptionJobName": sfn.JsonPath.string_at("$.TranscriptionResult.TranscriptionJob.TranscriptionJobName")
                                                        },
                                                        iam_resources=["*"],
                                                        result_path="$.TranscriptionResult")

        evaluate_transcription_task = sfn.Choice(self, "EvaluateTranscriptionJobStatus")
        transcription_failed = sfn.Fail(self, "TranscriptionFailed", error="TranscriptionFailed", cause="Transcription job failed")

        create_speech_feedback_bedrock_prompt_task = tasks.LambdaInvoke(self, "CreateBedrockPrompt-SpeechFeedback",
                                                        lambda_function=prepare_bedrock_prompts_lambda,
                                                        payload=sfn.TaskInput.from_json_path_at("$"),
                                                        result_path="$.feedback_response",
                                                        result_selector={
                                                            "s3uri.$": "$.Payload"
                                                        })
        
        create_speech_rewrite_bedrock_prompt_task = tasks.LambdaInvoke(self, "CreateBedrockPrompt-SpeechRewrite",
                                                        lambda_function=prepare_bedrock_prompts_lambda,
                                                        payload=sfn.TaskInput.from_json_path_at("$"),
                                                        result_path="$.rewrite_response",
                                                        result_selector={
                                                            "s3uri.$": "$.Payload"
                                                        })
        
        combine_llm_chaining_output_task = tasks.LambdaInvoke(self, "CombineLLMChainingOutput",
                                                        lambda_function=prepare_bedrock_prompts_lambda,
                                                        payload=sfn.TaskInput.from_json_path_at("$"),
                                                        output_path="$.Payload"
                                                        )
        
        model = bedrock.FoundationModel.from_foundation_model_id(self, "Model", bedrock.FoundationModelIdentifier.ANTHROPIC_CLAUDE_3_5_SONNET_20240620_V1_0)
        
        get_speech_feedback = tasks.BedrockInvokeModel(self, "GetSpeechFeedback",
                                                        model=model,
                                                        input=tasks.BedrockInvokeModelInputProps(
                                                            s3_input_uri=sfn.JsonPath.string_at("$.feedback_response.s3uri.input")
                                                        ),
                                                        output=tasks.BedrockInvokeModelOutputProps(
                                                            s3_output_uri=sfn.JsonPath.string_at("$.feedback_response.s3uri.output")
                                                        ),
                                                        content_type='application/json',
                                                        result_path="$.feedback_response.bedrock_response")
        
        get_speech_rewrite = tasks.BedrockInvokeModel(self, "GetSpeechRewrite",
                                                      model=model,
                                                      input=tasks.BedrockInvokeModelInputProps(
                                                            s3_input_uri=sfn.JsonPath.string_at("$.rewrite_response.s3uri.input")
                                                        ),
                                                        output=tasks.BedrockInvokeModelOutputProps(
                                                            s3_output_uri=sfn.JsonPath.string_at("$.rewrite_response.s3uri.output")
                                                        ),
                                                        content_type='application/json',
                                                        result_path="$.rewrite_response.bedrock_response"
                                                     )

        sns_publish = tasks.SnsPublish(self, "PublishToSNS",
                                      topic=topic,
                                      message=sfn.TaskInput.from_json_path_at("$"),
                                      result_path=sfn.JsonPath.DISCARD)

        # Create Stepfunctions Chain
        chain = start_transcription_task\
            .next(wait_for_transcription_task)\
            .next(get_transcription_task)\
            .next(evaluate_transcription_task
                .when(sfn.Condition.string_equals("$.TranscriptionResult.TranscriptionJob.TranscriptionJobStatus", "COMPLETED"),
                    create_speech_feedback_bedrock_prompt_task.next(get_speech_feedback
                        .next(create_speech_rewrite_bedrock_prompt_task)\
                        .next(get_speech_rewrite)\
                        .next(combine_llm_chaining_output_task)\
                        .next(sns_publish))
                        )
                .when(sfn.Condition.string_equals("$.TranscriptionResult.TranscriptionJob.TranscriptionJobStatus", "FAILED"), transcription_failed)
                .otherwise(wait_for_transcription_task))

        state_machine = sfn.StateMachine(self, "PublicSpeakingMentorAIAssistantStateMachine",
                                         role=state_machine_role,
                                         timeout=Duration.hours(2),
                                         definition_body=sfn.DefinitionBody.from_chainable(chain))

        # Create an EventBridge rule to trigger the Step Functions state machine
        rule = events.Rule(self, "PublicSpeakingMentorAIAssistantEventBridgeRule",
                           event_pattern=events.EventPattern(
                               source=["aws.s3"],
                               detail_type=["Object Created"],
                               detail={
                                   "bucket": {
                                       "name": [bucket.bucket_name]
                                   },
                                   "object": {
                                       "key": events.Match.prefix("raw-audio-files/")
                                   }
                               }
                           ))
        rule.add_target(targets.SfnStateMachine(state_machine))

        # Define prefix that will be used in some resource names
        prefix = Config.STACK_NAME

        # Create Cognito user pool
        user_pool = cognito.UserPool(self, f"{prefix}UserPool",
                                     removal_policy=RemovalPolicy.DESTROY,  # Set the removal policy
                                    #  sign_in_aliases=cognito.SignInAliases(username=False, email=True),
                                    #  auto_verify=cognito.AutoVerifiedAttrs(email=True),
                                    #  mfa=cognito.Mfa.OFF
                                    )

        # Create Cognito client
        user_pool_client = cognito.UserPoolClient(self, f"{prefix}UserPoolClient",
                                                  user_pool=user_pool,
                                                  generate_secret=True
                                                  )

        # Store Cognito parameters in a Secrets Manager secret
        secret = secretsmanager.Secret(self, f"{prefix}ParamCognitoSecret",
                                       secret_object_value={
                                           "pool_id": SecretValue.unsafe_plain_text(user_pool.user_pool_id),
                                           "app_client_id": SecretValue.unsafe_plain_text(user_pool_client.user_pool_client_id),
                                           "app_client_secret": user_pool_client.user_pool_client_secret
                                       },
                                       # This secret name should be identical
                                       # to the one defined in the Streamlit
                                       # container
                                       secret_name=Config.SECRETS_MANAGER_ID
                                    )
        ssm.StringParameter(self, f"{prefix}S3bucketParameter",
            allowed_pattern=".*",
            description="Parameters used by Public Speaking Mentor AI Assistant",
            parameter_name="/psmb/s3_bucket",
            string_value=bucket.bucket_name,
            tier=ssm.ParameterTier.STANDARD
        )
        
        ssm.StringParameter(self, f"{prefix}SFnParameter",
            allowed_pattern=".*",
            description="Parameters used by Public Speaking Mentor AI Assistant",
            parameter_name="/psmb/statemachine_arn",
            string_value=state_machine.state_machine_arn,
            tier=ssm.ParameterTier.STANDARD
        )
        
        # Output Cognito pool id
        CfnOutput(self, "CognitoPoolId",
                  value=user_pool.user_pool_id)

        # Output the IAM role ARN and S3 bucket name
        CfnOutput(self, "PublicSpeakingMentorAIAssistantStateMachineRoleArn",
                  value=state_machine_role.role_arn,
                  description="IAM role ARN used for the Public Speaking Mentor AI Assistant Step Functions state machine")
        CfnOutput(self, "PublicSpeakingMentorAIAssistantS3BucketName",
                  value=bucket.bucket_name,
                  description="Name of the S3 bucket used for the Public Speaking Mentor AI Assistant")
