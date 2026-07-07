from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from backend.models.base import Base


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(500), unique=True, nullable=False)
    type = Column(String(50), nullable=False)
    parent_id = Column(Integer, ForeignKey("departments.id"))
    is_active = Column(Boolean, default=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "parent_id": self.parent_id,
            "is_active": self.is_active,
        }
