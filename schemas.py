"""
Database Schemas for the Course + Token marketplace

Each Pydantic model corresponds to a MongoDB collection (lowercased class name).
Use these models for validation in API routes.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal

class User(BaseModel):
    name: str = Field(..., description="Display name")
    email: str = Field(..., description="Email address")
    role: Literal["creator", "buyer", "both"] = Field("both")
    eth_address: Optional[str] = Field(None, description="User's Ethereum address (optional)")
    is_active: bool = True

class Course(BaseModel):
    creator_id: str = Field(..., description="ID of the creator user")
    title: str
    description: str
    price_usd: float = Field(..., ge=0)
    category: Optional[str] = None
    cover_image_url: Optional[str] = None
    # Token config for this course
    token_symbol: str = Field(..., min_length=2, max_length=8)
    token_supply: int = Field(..., gt=0, description="Fixed total supply for the course token")
    treasury_eth_address: str = Field(..., description="ETH address where revenue accumulates")

class Purchase(BaseModel):
    user_id: str
    course_id: str
    price_usd: float
    status: Literal["paid", "refunded"] = "paid"

class CourseToken(BaseModel):
    course_id: str
    token_symbol: str
    total_supply: int
    circulating_supply: int = 0
    treasury_eth_address: str
    treasury_token_balance: int
    treasury_revenue_usd: float = 0.0

class Balance(BaseModel):
    user_id: str
    course_id: str
    amount: int = 0

class Order(BaseModel):
    course_id: str
    user_id: str
    side: Literal["buy", "sell"]
    price_usd: float = Field(..., gt=0)
    amount: int = Field(..., gt=0)
    status: Literal["open", "filled", "cancelled"] = "open"
