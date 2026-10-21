"""Public request and response schemas."""

from apiflask import Schema, fields


class PaymentInput(Schema):
    payment_method_id = fields.UUID(required=True)


class CartInput(Schema):
    cart_id = fields.UUID(required=True)


class KeyInput(Schema):
    idempotency_key = fields.UUID(required=True, data_key="Idempotency-Key")


class PaymentOutput(Schema):
    id = fields.UUID(required=True)
    cart_id = fields.UUID(required=True)
    payment_method_id = fields.UUID(required=True)
    amount = fields.String(required=True)
    currency = fields.String(required=True)
    status = fields.String(required=True)
    failure_code = fields.String(required=True, allow_none=True)


class ErrorDetail(Schema):
    code = fields.String(required=True)
    message = fields.String(required=True)


class ErrorOutput(Schema):
    error = fields.Nested(ErrorDetail, required=True)
