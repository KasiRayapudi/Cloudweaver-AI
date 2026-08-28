"""Placeholder handler. Replace with your application code."""

import json


def handler(event, context):
    print(json.dumps(event))
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"message": "hello from terraform"}),
    }
