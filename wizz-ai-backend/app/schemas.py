from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: str
    filename: str
    status: str
    chunk_count: int
    error_message: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class IngestResponse(BaseModel):
    document_id: str
    status: str
    message: str


class SourceCreate(BaseModel):
    name: str


class SourceOut(BaseModel):
    id: str
    name: str
    created_at: datetime

    class Config:
        from_attributes = True
