# San Jose Food Delivery Platform

A serverless food delivery platform built on AWS with 17+ microservices, event-driven architecture, and Infrastructure as Code.

## Architecture

```
CloudFront + S3 (Frontend)
        │
  API Gateway (REST + WebSocket) + Cognito Auth
        │
  ┌─────┼──────────┐
  │     │          │
User  Order    Delivery
Domain Domain   Domain
  │     │          │
  └─────┼──────────┘
        │
   EventBridge ──► SQS / SNS
        │
  DynamoDB / S3 / ElastiCache
```

## Services

| Service | Description | Key AWS Services |
|---------|-------------|-----------------|
| User | Profile management | Lambda, DynamoDB |
| Auth | Signup / Login | Cognito, Lambda |
| Restaurant | Restaurant CRUD | Lambda, DynamoDB, S3 |
| Menu | Menu items management | Lambda, DynamoDB |
| Search | Search restaurants & dishes | Lambda, DynamoDB |
| Cart | Shopping cart | Lambda, DynamoDB |
| Order | Order lifecycle | Lambda, DynamoDB, SQS, EventBridge |
| Payment | Payment processing | Lambda, DynamoDB, EventBridge |
| Delivery | Driver assignment | Lambda, DynamoDB, EventBridge |
| Driver | Driver registration | Lambda, DynamoDB |
| Notification | Email / SMS | Lambda, SES, SNS, SQS |
| Rating | Reviews & ratings | Lambda, DynamoDB |
| Promotion | Coupons & discounts | Lambda, DynamoDB |
| Tracking | Real-time delivery tracking | API Gateway WebSocket, Lambda |
| Analytics | Business metrics | Lambda, DynamoDB, EventBridge |
| Geolocation | Distance & ETA calculation | Lambda, DynamoDB |
| Admin | Dashboard & management | Lambda, DynamoDB |

## Quick Start

```bash
# Deploy to AWS
cd infrastructure/cdk
pip install -r requirements.txt
cdk bootstrap && cdk deploy --all

# Run frontend locally
cd frontend
python -m http.server 8080
```
