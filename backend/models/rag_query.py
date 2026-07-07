from sqlalchemy import Column, Integer, Boolean, String, Text, DateTime, ForeignKey, JSON, func
from backend.models.base import Base


class RAGQuery(Base):
    __tablename__ = "rag_queries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    query_text = Column(Text, nullable=False)
    response_text = Column(Text)
    sources_used = Column(JSON)
    cached = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "query_text": self.query_text,
            "response_text": self.response_text,
            "sources_used": self.sources_used,
            "cached": self.cached,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
