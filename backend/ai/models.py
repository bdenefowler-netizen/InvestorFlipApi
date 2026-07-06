from pydantic import BaseModel
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
    photos_notes: Optional[str] = None
    tax_info: Optional[str] = None
    mortgage_estimate: Optional[float] = None
    permits: Optional[str] = None
    comps: Optional[str] = None
    rent_estimate: Optional[float] = None


class QuillAnalyzeResponse(BaseModel):
    analyst: str = "Quill AI"
    decision: str
    max_offer: float
    arv_explanation: str
    repair_estimate: str
    risk_flags: List[str]
    offer_letter: str
    questions_to_ask_agent: List[str]
