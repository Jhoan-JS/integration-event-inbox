from datetime import UTC
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class EnrollmentData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: Annotated[str, Field(pattern=r"^STU-DEMO-[0-9]{4}$", max_length=13)]
    course_id: Annotated[str, Field(pattern=r"^CRS-DEMO-[0-9]{4}$", max_length=13)]
    term: Annotated[str, Field(pattern=r"^[0-9]{4}-(SPRING|SUMMER|FALL)$", max_length=11)]


class IncomingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: Literal["enrollment.created"]
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    occurred_at: AwareDatetime
    data: EnrollmentData

    @field_validator("occurred_at", mode="before")
    @classmethod
    def require_iso_timestamp(cls, value):
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("An ISO 8601 timestamp string with a timezone is required")
        return value

    @field_validator("occurred_at")
    @classmethod
    def normalize_timestamp(cls, value):
        try:
            return value.astimezone(UTC)
        except OverflowError as exc:
            raise ValueError("Timestamp is outside the representable UTC range") from exc


class EventReceipt(BaseModel):
    event_id: UUID
    source: Literal["synthetic-registry"] = "synthetic-registry"
    status: Literal["received"] = "received"
    received_at: AwareDatetime
    event: IncomingEvent


class ValidationIssue(BaseModel):
    location: list[str | int]
    type: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    issues: list[ValidationIssue] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorDetail
