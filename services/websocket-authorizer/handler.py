"""
WebSocket Authorizer Lambda

Simplified authorizer that validates Cognito JWT tokens.
Uses jose library for better Lambda compatibility.
"""

import os
import json
import base64
import requests
from jose import jwt, jwk
from jose.utils import base64url_decode

# Environment variables
COGNITO_USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
COGNITO_REGION = os.environ["COGNITO_REGION"]

# JWKS URL for Cognito
JWKS_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"

# Cache for JWKS
_jwks_cache = None


def lambda_handler(event, context):
    """
    Validate JWT token from WebSocket connection request.

    Event structure:
    {
        'queryStringParameters': {'token': '<JWT_TOKEN>'},
        'methodArn': 'arn:aws:execute-api:...'
    }
    """
    print(f"Authorizer received event: {json.dumps(event, default=str)}")

    try:
        # Extract token from query string
        token = event.get("queryStringParameters", {}).get("token")

        if not token:
            print("No token provided")
            return generate_policy("user", "Deny", event["methodArn"])

        # Validate token
        decoded_token = validate_token(token)

        # Extract user info from token
        user_id = decoded_token.get("sub")
        user_email = decoded_token.get("email", "")
        user_role = decoded_token.get("custom:role", "customer")

        print(f"Token validated for user: {user_id} ({user_email})")

        # Generate policy with user context
        policy = generate_policy(user_id, "Allow", event["methodArn"], {
            "user_id": user_id,
            "email": user_email,
            "role": user_role
        })

        return policy

    except jwt.ExpiredSignatureError:
        print("Token expired")
        return generate_policy("user", "Deny", event["methodArn"])
    except jwt.JWTError as e:
        print(f"Invalid token: {str(e)}")
        return generate_policy("user", "Deny", event["methodArn"])
    except Exception as e:
        print(f"Authorization error: {str(e)}")
        return generate_policy("user", "Deny", event["methodArn"])


def get_jwks():
    """Fetch and cache JWKS from Cognito."""
    global _jwks_cache
    if _jwks_cache is None:
        response = requests.get(JWKS_URL)
        _jwks_cache = response.json()
    return _jwks_cache


def validate_token(token):
    """
    Validate JWT token using Cognito JWKS.

    Returns decoded token if valid, raises exception if invalid.
    """
    try:
        # Get the key ID from the token header
        headers = jwt.get_unverified_headers(token)
        kid = headers['kid']

        # Get the public key from JWKS
        jwks = get_jwks()
        key = None
        for jwk_key in jwks['keys']:
            if jwk_key['kid'] == kid:
                key = jwk_key
                break

        if not key:
            raise jwt.JWTError('Public key not found in JWKS')

        # Decode and validate the token
        decoded_token = jwt.decode(
            token,
            key,
            algorithms=['RS256'],
            options={'verify_aud': False}  # Cognito tokens may not have aud claim
        )

        return decoded_token

    except Exception as e:
        print(f"Token validation failed: {str(e)}")
        raise


def generate_policy(principal_id, effect, resource, context=None):
    """
    Generate IAM policy for API Gateway.

    Args:
        principal_id: User identifier
        effect: "Allow" or "Deny"
        resource: ARN of the resource
        context: Optional context to pass to Lambda
    """
    policy = {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{
                "Action": "execute-api:Invoke",
                "Effect": effect,
                "Resource": resource
            }]
        }
    }

    # Add context if provided
    if context:
        policy["context"] = context

    return policy
