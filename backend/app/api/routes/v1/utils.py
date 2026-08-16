from fastapi import APIRouter, Depends
from pydantic.networks import EmailStr

from app.api.deps import get_current_admin
from app.schemas.base import Message
from app.services.email.service import generate_test_email, send_email

# Prefix and tags are applied where this router is included (app/api/routes/v1).
router = APIRouter()


@router.post(
    "/test-email/",
    dependencies=[Depends(get_current_admin)],
    status_code=201,
)
def test_email(email_to: EmailStr) -> Message:
    """
    Test emails.
    """
    email_data = generate_test_email(email_to=email_to)
    send_email(
        email_to=email_to,
        subject=email_data.subject,
        html_content=email_data.html_content,
    )
    return Message(message="Test email sent")


@router.get("/health-check/")
async def health_check() -> bool:
    return True
