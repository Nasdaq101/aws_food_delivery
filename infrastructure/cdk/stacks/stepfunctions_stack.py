# ── step functions stack ──
# TODO: add step functions state machine for orchestrating the order lifecycle
#
# workflow steps:
#   1. validate order   → check cart not empty, restaurant exists
#   2. process payment  → invoke payment-service lambda
#   3. assign driver    → invoke delivery-service to find available driver
#   4. start tracking   → create delivery record, notify customer
#   5. monitor delivery → wait for driver status updates
#   6. complete order   → finalize payment, prompt for rating
#
# on payment failure:
#   - refund step → rollback payment
#   - notify customer → send failure notification via SQS → notification-service
