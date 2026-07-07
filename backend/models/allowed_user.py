from sqlalchemy import BigInteger, Column, Integer, String, Boolean, DateTime, func
from backend.models.base import Base


class AllowedUser(Base):
    __tablename__ = "allowed_users"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, nullable=True)
    max_user_id = Column(BigInteger, unique=True, nullable=True)
    full_name = Column(String(500), nullable=False)
    department = Column(String(500))
    consent_given = Column(Boolean, default=False)
    consent_timestamp = Column(DateTime(timezone=True))
    is_active = Column(Boolean, default=True, index=True)
    added_by = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "phone": self.phone,
            "max_user_id": self.max_user_id,
            "full_name": self.full_name,
            "department": self.department,
            "consent_given": self.consent_given,
            "consent_timestamp": self.consent_timestamp.isoformat() if self.consent_timestamp else None,
            "is_active": self.is_active,
            "added_by": self.added_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
