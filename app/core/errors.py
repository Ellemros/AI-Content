"""Domain errors mapped to HTTP responses by the API."""


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DuplicateTopicError(AppError):
    status_code = 409

    def __init__(self, title: str, topic_id: str, exact: bool) -> None:
        kind = "duplicate" if exact else "near-duplicate"
        super().__init__(f"Rejected {kind} of an existing topic ({topic_id}): {title}")
        self.topic_id = topic_id
        self.exact = exact


class SpendCapExceeded(AppError):
    status_code = 402

    def __init__(self, spent: object, cap: object, estimate: object) -> None:
        super().__init__(
            "Monthly spend cap reached. "
            f"Spent ${float(spent):.2f} of ${float(cap):.2f}. "
            f"This call is estimated at ${float(estimate):.4f}. "
            "Raise MONTHLY_SPEND_CAP_USD, or leave the provider key empty to use the local fallback."
        )
        self.spent = spent
        self.cap = cap
        self.estimate = estimate


class NotApprovedError(AppError):
    status_code = 409


class UploadDisabledError(AppError):
    status_code = 400


class NotFoundError(AppError):
    status_code = 404


class QualityGateError(AppError):
    status_code = 422
