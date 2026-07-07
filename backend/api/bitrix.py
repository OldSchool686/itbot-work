import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from backend.services.bitrix_service import BitrixService, get_bitrix_service
from backend.utils.config import settings


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/bitrix", tags=["bitrix24"])


def _check_internal_api_key(x_internal_token: Optional[str] = Header(None)) -> None:
    """Verify the X-Internal-Token header matches configured API key."""
    if not settings.internal_api_key:
        return  # Disabled when no key is set (development mode)
    if x_internal_token != settings.internal_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")


class CreateDealRequest(BaseModel):
    full_name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=1)
    department: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    ticket_id: int


class CreateDealResponse(BaseModel):
    bitrix_deal_id: Optional[int] = None
    contact_id: Optional[int] = None
    status: str


@router.post("/deal", response_model=CreateDealResponse)
async def create_deal(
    req: CreateDealRequest,
    _internal: None = Depends(_check_internal_api_key),
    svc: BitrixService = Depends(get_bitrix_service),
):
    """Create a Bitrix24 deal for an IT support ticket.

    Orchestrates contact lookup/creation and deal creation in one call.
    Creates the deal in the NEW stage by default.
    Internal service-to-service endpoint — no authentication required.
    """
    # Step 1: Find or create contact
    contact_id = await svc.create_contact(req.full_name, req.phone)

    # Step 2: Build deal title from ticket data
    title = f"[{req.category}] {req.department}: {req.description[:80]}"

    # Step 3: Create deal in NEW stage
    deal_id = await svc.create_deal(
        title=title,
        stage_id=settings.bitrix24_stage_new,
        contact_id=contact_id,
        phone=req.phone,
        department=req.department,
        category=req.category,
        ticket_id=req.ticket_id,
        description=req.description,
        full_name=req.full_name,
    )

    if not deal_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Bitrix24 deal",
        )

    return CreateDealResponse(
        bitrix_deal_id=deal_id,
        contact_id=contact_id,
        status=settings.bitrix24_stage_new,
    )


class UpdateStageRequest(BaseModel):
    stage_id: str = Field(..., min_length=1)


@router.post("/deal/{deal_id}/stage")
async def update_deal_stage(
    deal_id: int,
    req: UpdateStageRequest,
    _internal: None = Depends(_check_internal_api_key),
    svc: BitrixService = Depends(get_bitrix_service),
):
    """Update the stage of an existing Bitrix24 deal."""
    # Verify deal exists before updating
    deal = await svc.get_deal_by_bot_id(deal_id)
    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deal {deal_id} not found",
        )

    try:
        await svc.update_stage(deal_id, req.stage_id)
    except Exception as e:
        logger.exception(f"Bitrix update_stage failed for deal {deal_id}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Ошибка операции Bitrix24 API. Попробуйте позже.",
        ) from e

    return {"deal_id": deal_id, "stage_id": req.stage_id}


class AddCommentRequest(BaseModel):
    message: str = Field(..., min_length=1)


@router.post("/deal/{deal_id}/comment")
async def add_deal_comment(
    deal_id: int,
    req: AddCommentRequest,
    _internal: None = Depends(_check_internal_api_key),
    svc: BitrixService = Depends(get_bitrix_service),
):
    """Add a comment to an existing Bitrix24 deal timeline."""
    try:
        await svc.add_comment(deal_id, req.message)
    except Exception as e:
        logger.exception(f"Bitrix add_comment failed for deal {deal_id}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Ошибка операции Bitrix24 API. Попробуйте позже.",
        ) from e

    return {"deal_id": deal_id, "comment_added": True}


@router.get("/deal/by-ticket/{ticket_id}")
async def get_deal_by_ticket(
    ticket_id: int,
    _internal: None = Depends(_check_internal_api_key),
    svc: BitrixService = Depends(get_bitrix_service),
):
    """Look up a Bitrix24 deal by the internal bot ticket ID."""
    deal = await svc.get_deal_by_bot_id(ticket_id)

    if not deal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No Bitrix24 deal found for ticket {ticket_id}",
        )

    return {"deal": deal}