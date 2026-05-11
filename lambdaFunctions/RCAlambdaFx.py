# File Name: RCAlambdaFx.py

import boto3
import json
import datetime
import re

# AWS Clients
logs_client = boto3.client('logs')
bedrock_client = boto3.client('bedrock-runtime')

# Bedrock Model
MODEL_ID = "anthropic.claude-3-sonnet-20240229-v1:0"

def extract_error_logs(events):
    """
    Extract only relevant error logs.
    """
    error_patterns = [
        "error",
        "exception",
        "task timed out",
        "accessdenied",
        "failed",
        "traceback"
    ]

    filtered_logs = []

    for event in events:
        message = event.get("message", "")

        if any(pattern.lower() in message.lower() for pattern in error_patterns):
            filtered_logs.append(message)

    return filtered_logs


def invoke_bedrock_llm(function_name, error_logs):
    """
    Send logs to Bedrock Claude model for RCA analysis.
    """

    combined_logs = "\n".join(error_logs)

    prompt = f"""
You are an AWS Cloud Root Cause Analysis Assistant.

Analyze the following AWS Lambda logs.

Lambda Function:
{function_name}

Logs:
{combined_logs}

Tasks:
1. Identify the root cause.
2. Explain the issue clearly.
3. Suggest a fix.
4. Mention AWS best practices if applicable.

Return response in this format:

Root Cause:
<root cause>

Recommendation:
<fix recommendation>
"""

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 700,
        "temperature": 0.2,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }

    response = bedrock_client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json"
    )

    response_body = json.loads(response['body'].read())

    return response_body['content'][0]['text']


def lambda_handler(event, context):

    print("Incoming Event:")
    print(json.dumps(event))

    try:

        # Bedrock Agent Event Structure
        actionGroup = event.get('actionGroup')
        apiPath = event.get('apiPath')
        httpMethod = event.get('httpMethod')

        requestBody = event.get('requestBody', {})

        responseBody = {
            "application/json": {
                "body": "No matching action found."
            }
        }

        # Handle RCA API
        if apiPath == '/analyzeLambdaError':

            properties = requestBody.get(
                'content',
                {}
            ).get(
                'application/json',
                {}
            ).get(
                'properties',
                []
            )

            function_name = None

            for prop in properties:
                if prop.get("name") == "function_name":
                    function_name = prop.get("value")

            if not function_name:

                responseBody = {
                    "application/json": {
                        "body": json.dumps({
                            "error": "function_name is missing"
                        })
                    }
                }

            else:

                log_group_name = f"/aws/lambda/{function_name}"

                now = int(datetime.datetime.utcnow().timestamp() * 1000)

                # Last 15 minutes
                start_time = now - (15 * 60 * 1000)

                try:

                    logs_response = logs_client.filter_log_events(
                        logGroupName=log_group_name,
                        startTime=start_time,
                        limit=100
                    )

                    events = logs_response.get("events", [])

                    error_logs = extract_error_logs(events)

                    if not error_logs:

                        result = {
                            "functionAnalyzed": function_name,
                            "rootCause": "No critical errors found in recent logs.",
                            "recommendation": "No action required."
                        }

                    else:

                        llm_output = invoke_bedrock_llm(
                            function_name,
                            error_logs
                        )

                        root_cause = ""
                        recommendation = ""

                        # Parse Claude response
                        if "Recommendation:" in llm_output:

                            parts = llm_output.split("Recommendation:")

                            root_cause = parts[0].replace(
                                "Root Cause:",
                                ""
                            ).strip()

                            recommendation = parts[1].strip()

                        else:
                            root_cause = llm_output
                            recommendation = "Review logs manually."

                        result = {
                            "functionAnalyzed": function_name,
                            "rootCause": root_cause,
                            "recommendation": recommendation
                        }

                    responseBody = {
                        "application/json": {
                            "body": json.dumps(result)
                        }
                    }

                except Exception as log_error:

                    responseBody = {
                        "application/json": {
                            "body": json.dumps({
                                "error": f"Failed fetching logs: {str(log_error)}"
                            })
                        }
                    }

        action_response = {
            'actionGroup': actionGroup,
            'apiPath': apiPath,
            'httpMethod': httpMethod,
            'httpStatusCode': 200,
            'responseBody': responseBody
        }

        final_response = {
            'response': action_response,
            'messageVersion': event.get('messageVersion', '1.0')
        }

        print("Final Response:")
        print(json.dumps(final_response))

        return final_response

    except Exception as e:

        print("Unhandled Exception:")
        print(str(e))

        return {
            'statusCode': 500,
            'body': json.dumps({
                "error": str(e)
            })
        }