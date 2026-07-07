from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean, func
from backend.models.base import Base


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    full_name = Column(String(500), nullable=False)
    phone = Column(String(20), nullable=False)
    department = Column(String(500), nullable=False)
    category = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    photo_urls = Column(JSON)
    bitrix_deal_id = Column(Integer, unique=True)
    status = Column(String(50), default="new", index=True)
    closed_by_user = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "full_name": self.full_name,
            "phone": self.phone,
            "department": self.department,
            "category": self.category,
            "description": self.description,
            "photo_urls": self.photo_urls,
            "bitrix_deal_id": self.bitrix_deal_id,
            "status": self.status,
            "closed_by_user": self.closed_by_user or False,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TicketReply(Base):
    __tablename__ = "ticket_replies"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    admin_name = Column(String(255), nullable=False)
    reply_text = Column(Text, nullable=False)
    file_names = Column(JSON)
    sent_to_max = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "admin_name": self.admin_name,
            "reply_text": self.reply_text,
            "file_names": self.file_names,
            "sent_to_max": self.sent_to_max or False,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
