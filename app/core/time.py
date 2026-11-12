from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def month_key(moment: datetime | None = None) -> str:
    moment = moment or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m")
