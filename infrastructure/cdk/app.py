#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.database_stack import DatabaseStack
from stacks.storage_stack import StorageStack
from stacks.auth_stack import AuthStack
from stacks.messaging_stack import MessagingStack
from stacks.api_stack import ApiStack
from stacks.compute_stack import ComputeStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1",
)

database_stack = DatabaseStack(app, "FoodDelivery-Database", env=env)
storage_stack = StorageStack(app, "FoodDelivery-Storage", env=env)
auth_stack = AuthStack(app, "FoodDelivery-Auth", env=env)
messaging_stack = MessagingStack(app, "FoodDelivery-Messaging", env=env)

compute_stack = ComputeStack(
    app, "FoodDelivery-Compute",
    database_stack=database_stack,
    storage_stack=storage_stack,
    auth_stack=auth_stack,
    messaging_stack=messaging_stack,
    env=env,
)

api_stack = ApiStack(
    app, "FoodDelivery-Api",
    compute_stack=compute_stack,
    auth_stack=auth_stack,
    env=env,
)

app.synth()
