import uuid
import enum
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Enum, Text, Integer, BigInteger, Boolean
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class DocumentStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    is_demo = Column(Boolean, default=False, nullable=False)  # gates quota enforcement in ingestion/chat routers
    created_at = Column(DateTime, default=datetime.utcnow)

    api_keys = relationship("APIKey", back_populates="tenant", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="tenant", cascade="all, delete-orphan")
    chats = relationship("Chat", back_populates="tenant", cascade="all, delete-orphan")


class APIKey(Base):
    """
    Two kinds of keys per tenant:
      - 'admin' keys: used by the tenant dashboard (Next.js) for uploads/management
      - 'embed' keys: public-safe, used by the widget script tag, chat-only scope
    Store only a hash of the key, never the raw value.
    """
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    key_hash = Column(String, nullable=False, unique=True, index=True)
    key_prefix = Column(String, nullable=False)  # first 8 chars, shown in dashboard for identification
    scope = Column(String, nullable=False, default="admin")  # "admin" | "embed"
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)

    tenant = relationship("Tenant", back_populates="api_keys")


class Source(Base):
    """A logical source a document belongs to, e.g. 'Help Center', 'Product Docs'."""
    __tablename__ = "sources"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    documents = relationship("Document", back_populates="source")


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    source_id = Column(UUID(as_uuid=False), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True)

    filename = Column(String, nullable=False)
    storage_path = Column(String, nullable=False)  # path in Supabase storage bucket
    mime_type = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)

    status = Column(Enum(DocumentStatus), default=DocumentStatus.pending, nullable=False)
    error_message = Column(Text, nullable=True)
    chunk_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)

    tenant = relationship("Tenant", back_populates="documents")
    source = relationship("Source", back_populates="documents")


class Chat(Base):
    __tablename__ = "chats"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    visitor_id = Column(String, nullable=True)  # anonymous widget visitor identifier
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="chats")
    messages = relationship("Message", back_populates="chat", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    chat_id = Column(UUID(as_uuid=False), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    citations = Column(Text, nullable=True)  # JSON-encoded list of {source, document_id, chunk_id}
    created_at = Column(DateTime, default=datetime.utcnow)

    chat = relationship("Chat", back_populates="messages")
