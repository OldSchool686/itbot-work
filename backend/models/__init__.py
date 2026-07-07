from backend.models.base import Base
from backend.models.admin import Admin
from backend.models.allowed_user import AllowedUser
from backend.models.user import User
from backend.models.ticket import Ticket
from backend.models.document import Document
from backend.models.rag_query import RAGQuery
from backend.models.department import Department

__all__ = [
    "Base",
    "Admin",
    "AllowedUser",
    "User",
    "Ticket",
    "Document",
    "RAGQuery",
    "Department",
]
