import json
import os
import boto3
from botocore.exceptions import ClientError

# we may not need to finish email verification flow, MFA, device tracking, refresh token rotation, etc.
# you can decide to implement it or not.

dynamodb = boto3.resource("dynamodb")
cognito = boto3.client("cognito-idp")

USERS_TABLE_NAME = os.environ.get("USERS_TABLE_NAME", "FoodDelivery-Users")
USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")

users_table = dynamodb.Table(USERS_TABLE_NAME)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def handle_signup(body: dict):
    # TODO: rate limiting, email verification flow, custom challenge, etc.
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    full_name = body.get("full_name") or ""
    role = body.get("role") or "customer"
    if not email or not password:
        return response(400, {"error": "BadRequest", "message": "email and password are required"})
    if not USER_POOL_ID or not CLIENT_ID:
        return response(500, {"error": "ConfigError", "message": "COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID must be set"})

    try:
        sign_up_resp = cognito.sign_up(
            ClientId=CLIENT_ID,
            Username=email,
            Password=password,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "name", "Value": full_name},
                {"Name": "custom:role", "Value": role},
            ],
        )
        sub = sign_up_resp.get("UserSub")
        if not sub:
            return response(502, {"error": "CognitoError", "message": "SignUp did not return UserSub"})

        try:
            users_table.put_item(
                Item={
                    "user_id": sub,
                    "email": email,
                    "full_name": full_name,
                    "role": role,
                },
                ConditionExpression="attribute_not_exists(user_id)",
            )
        except ClientError as de:
            if de.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return response(409, {"error": "Conflict", "message": "User profile already exists"})
            raise
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "UsernameExistsException":
            return response(409, {"error": "Conflict", "message": "User already exists"})
        if code == "InvalidPasswordException":
            return response(400, {"error": "BadRequest", "message": e.response["Error"].get("Message", "Invalid password")})
        return response(400, {"error": code, "message": str(e)})
    except Exception as e:
        # Cognito succeeded but Dynamo failed — TODO: compensating transaction / retry
        return response(500, {"error": "InternalError", "message": str(e)})

    return response(
        201,
        {
            "message": "User registered",
            "user_id": sub,
            # TODO: return confirmation required flag from Cognito response
            "user_confirmed": sign_up_resp.get("UserConfirmed", False),
        },
    )


def handle_login(body: dict):
    # TODO: MFA, device tracking, refresh token rotation
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not email or not password:
        return response(400, {"error": "BadRequest", "message": "email and password are required"})
    if not CLIENT_ID:
        return response(500, {"error": "ConfigError", "message": "COGNITO_CLIENT_ID must be set"})

    try:
        auth = cognito.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
        result = auth.get("AuthenticationResult") or {}
        return response(
            200,
            {
                "access_token": result.get("AccessToken"),
                "id_token": result.get("IdToken"),
                "refresh_token": result.get("RefreshToken"),
                "expires_in": result.get("ExpiresIn"),
                "token_type": result.get("TokenType"),
            },
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            return response(401, {"error": "Unauthorized", "message": "Invalid email or password"})
        if code == "UserNotConfirmedException":
            return response(403, {"error": "Forbidden", "message": "User email not confirmed"})
        return response(400, {"error": code, "message": str(e)})


def handle_verify(body: dict):
    """Confirm user email with verification code"""
    email = (body.get("email") or "").strip().lower()
    code = (body.get("code") or "").strip()

    if not email or not code:
        return response(400, {"error": "BadRequest", "message": "email and code are required"})
    if not CLIENT_ID:
        return response(500, {"error": "ConfigError", "message": "COGNITO_CLIENT_ID must be set"})

    try:
        cognito.confirm_sign_up(
            ClientId=CLIENT_ID,
            Username=email,
            ConfirmationCode=code
        )
        return response(200, {"message": "Email verified successfully"})
    except ClientError as e:
        code_err = e.response["Error"]["Code"]
        if code_err == "CodeMismatchException":
            return response(400, {"error": "BadRequest", "message": "Invalid verification code"})
        if code_err == "ExpiredCodeException":
            return response(400, {"error": "BadRequest", "message": "Verification code expired"})
        if code_err == "UserNotFoundException":
            return response(404, {"error": "NotFound", "message": "User not found"})
        return response(400, {"error": code_err, "message": str(e)})


def lambda_handler(event, context):
    try:
        http_method = event.get("httpMethod", "")
        path = (event.get("path") or "").rstrip("/") or "/"
        body = json.loads(event.get("body") or "{}")

        if http_method == "POST" and path == "/auth/signup":
            return handle_signup(body)
        if http_method == "POST" and path == "/auth/login":
            return handle_login(body)
        if http_method == "POST" and path == "/auth/verify":
            return handle_verify(body)

        return response(404, {"error": "NotFound", "message": "No route matched"})
    except json.JSONDecodeError:
        return response(400, {"error": "BadRequest", "message": "Invalid JSON body"})
    except ClientError as e:
        return response(502, {"error": "AWSError", "message": str(e)})
    except Exception as e:
        return response(500, {"error": "InternalError", "message": str(e)})
