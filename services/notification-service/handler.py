import json
import os

import boto3
from botocore.exceptions import ClientError

ses = boto3.client("ses")
sns = boto3.client("sns")

SES_FROM = os.environ.get("SES_FROM_EMAIL", "no-reply@example.com")
SNS_SMS_TYPE = "Transactional"


def lambda_handler(event, context):
    results = []
    records = event.get("Records") or []
    for record in records:
        try:
            body_raw = record.get("body", "{}")
            if isinstance(body_raw, str):
                payload = json.loads(body_raw)
            else:
                payload = body_raw

            msg_type = payload.get("type", "generic")
            recipient = payload.get("recipient") or {}
            message = payload.get("message") or {}

            out = {"messageId": record.get("messageId"), "type": msg_type, "channels": []}

            email = recipient.get("email")
            if email:
                try:
                    _send_email(email, msg_type, message)
                    out["channels"].append({"channel": "email", "ok": True})
                except ClientError as e:
                    out["channels"].append(
                        {"channel": "email", "ok": False, "error": str(e)}
                    )

            phone = recipient.get("phone") or recipient.get("sms")
            if phone:
                try:
                    _send_sms(phone, message)
                    out["channels"].append({"channel": "sms", "ok": True})
                except ClientError as e:
                    out["channels"].append(
                        {"channel": "sms", "ok": False, "error": str(e)}
                    )

            if not out["channels"]:
                out["warning"] = "No email or phone on recipient"

            results.append(out)
        except json.JSONDecodeError as e:
            results.append(
                {
                    "messageId": record.get("messageId"),
                    "ok": False,
                    "error": f"Invalid JSON: {e}",
                }
            )
        except Exception as e:
            results.append(
                {
                    "messageId": record.get("messageId"),
                    "ok": False,
                    "error": str(e),
                }
            )

    return {"processed": len(results), "results": results}


def _send_email(to_address, msg_type, message):
    subject = message.get("subject") or f"FoodDelivery: {msg_type}"
    text = message.get("text") or json.dumps(message, default=str)
    html = message.get("html")
    dest = {"ToAddresses": [to_address]}
    body = {"Text": {"Data": text, "Charset": "UTF-8"}}
    if html:
        body["Html"] = {"Data": html, "Charset": "UTF-8"}
    ses.send_email(
        Source=SES_FROM,
        Destination=dest,
        Message={"Subject": {"Data": subject, "Charset": "UTF-8"}, "Body": body},
    )


def _send_sms(phone_number, message):
    text = message.get("text") or message.get("body") or json.dumps(message, default=str)
    sns.publish(
        PhoneNumber=phone_number,
        Message=text[:1400],
        MessageAttributes={
            "AWS.SNS.SMS.SMSType": {
                "DataType": "String",
                "StringValue": SNS_SMS_TYPE,
            }
        },
    )
