from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, Text, func
from backend.models.base import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(500), nullable=False, index=True)
    original_path = Column(String(1000))
    file_type = Column(String(20), nullable=False)
    size_bytes = Column(BigInteger)
    chunks_count = Column(Integer, default=0)
    uploaded_by = Column(String(255))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_template = Column(Boolean, default=False)
    description = Column(Text)

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "original_path": self.original_path,
            "file_type": self.file_type,
            "size_bytes": self.size_bytes,
            "chunks_count": self.chunks_count,
            "uploaded_by": self.uploaded_by,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_template": self.is_template,
            "description": self.description,
        }
