from pydantic import BaseModel, Field
from typing import Optional, List


class QuillAnalyzeRequest(BaseModel):
    address: str
    owner_info: Optional[str] = None
    listing_price: Optional[float] = None

    beds: Optional[int] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None

    arv_estimate: Optional[float] = None
    repair_estimate: Optional[float] = None
    rent_estimate: Optional[float] = None
    mortgage_estimate: Optional[float] = None

    photos: List[str] = Field(default_factory=list)
    notes: Optional[str] = None

    tax_info: Optional[str] = None
    permits: Optional[str] = None
    comps: Optional[str] = None


class QuillAnalyzeResponse(BaseModel):
    analyst: str = "Quill AI"
    decision: str  # BUY / PASS / NEGOTIATE
    max_offer: Optional[float] = None

    arv_explanation: str
    repair_estimate: Optional[float] = None

    risk_flags: List[str] = Field(default_factory=list)
    offer_letter: str
    questions_to_ask_agent: List[str] = Field(default_factory=list)
