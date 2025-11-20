import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import Course, Purchase, CourseToken, Balance, Order, User

app = FastAPI(title="Course Token Marketplace API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Utilities
class ObjectIdStr(str):
    @staticmethod
    def to_obj(id_str: str):
        try:
            return ObjectId(id_str)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid id")

# Health
@app.get("/")
def read_root():
    return {"message": "Course Token Marketplace API"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Connected & Working"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()
            except Exception:
                pass
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response

# ==== Users (simple creator/buyer registry) ====
@app.post("/api/users", response_model=dict)
def create_user(user: User):
    user_id = create_document("user", user)
    return {"id": user_id}

@app.get("/api/users", response_model=List[dict])
def list_users():
    return get_documents("user")

# ==== Courses ====
@app.post("/api/courses", response_model=dict)
def create_course(course: Course):
    # also create the course token record with full supply in treasury by default
    course_id = create_document("course", course)
    token_record = CourseToken(
        course_id=course_id,
        token_symbol=course.token_symbol,
        total_supply=course.token_supply,
        circulating_supply=0,
        treasury_eth_address=course.treasury_eth_address,
        treasury_token_balance=course.token_supply,
        treasury_revenue_usd=0.0,
    )
    create_document("coursetoken", token_record)
    return {"id": course_id}

@app.get("/api/courses", response_model=List[dict])
def list_courses():
    return get_documents("course")

@app.get("/api/courses/{course_id}", response_model=dict)
def get_course(course_id: str):
    course = db.course.find_one({"_id": ObjectIdStr.to_obj(course_id)})
    if not course:
        raise HTTPException(404, "Course not found")
    token = db.coursetoken.find_one({"course_id": course_id})
    course["token"] = token
    return course

# ==== Purchases (buy course, reward tokens to buyer) ====
class PurchaseRequest(BaseModel):
    user_id: str

@app.post("/api/courses/{course_id}/purchase", response_model=dict)
def purchase_course(course_id: str, req: PurchaseRequest):
    course = db.course.find_one({"_id": ObjectIdStr.to_obj(course_id)})
    if not course:
        raise HTTPException(404, "Course not found")
    token = db.coursetoken.find_one({"course_id": course_id})
    if not token:
        raise HTTPException(500, "Token record missing for course")

    price = float(course.get("price_usd", 0))

    # Record purchase
    purchase = Purchase(user_id=req.user_id, course_id=course_id, price_usd=price)
    purchase_id = create_document("purchase", purchase)

    # Tokenomics: allocate a fixed amount of tokens per course purchase to buyer from treasury
    # For demo, 1% of total supply per purchase or at least 1 token
    allocate = max(1, int(token.get("total_supply", 1000) * 0.01))
    if token.get("treasury_token_balance", 0) < allocate:
        allocate = token.get("treasury_token_balance", 0)
    
    db.coursetoken.update_one(
        {"course_id": course_id},
        {
            "$inc": {
                "circulating_supply": allocate,
                "treasury_token_balance": -allocate,
                "treasury_revenue_usd": price,
            }
        },
    )

    # Credit buyer balance
    bal = db.balance.find_one({"user_id": req.user_id, "course_id": course_id})
    if bal:
        db.balance.update_one({"_id": bal["_id"]}, {"$inc": {"amount": allocate}})
    else:
        create_document("balance", Balance(user_id=req.user_id, course_id=course_id, amount=allocate))

    return {"purchase_id": purchase_id, "tokens_awarded": allocate, "price_usd": price}

# ==== Simple Orderbook for token trading (off-chain demo) ====
@app.post("/api/orders", response_model=dict)
def place_order(order: Order):
    # validate course exists
    course = db.course.find_one({"_id": ObjectIdStr.to_obj(order.course_id)})
    if not course:
        raise HTTPException(404, "Course not found")

    # if selling, ensure user balance
    if order.side == "sell":
        bal = db.balance.find_one({"user_id": order.user_id, "course_id": order.course_id})
        if not bal or int(bal.get("amount", 0)) < order.amount:
            raise HTTPException(400, "Insufficient token balance")
        # lock tokens by moving to reserved field
        db.balance.update_one({"_id": bal["_id"]}, {"$inc": {"amount": -order.amount, "reserved": order.amount}})

    order_id = create_document("order", order)
    return {"id": order_id}

@app.get("/api/orders", response_model=List[dict])
def list_orders(course_id: Optional[str] = None, side: Optional[str] = None):
    q = {}
    if course_id:
        q["course_id"] = course_id
    if side:
        q["side"] = side
    return get_documents("order", q, limit=100)

class TradeRequest(BaseModel):
    buy_order_id: str
    sell_order_id: str
    amount: int

@app.post("/api/trades", response_model=dict)
def match_trade(req: TradeRequest):
    buy = db.order.find_one({"_id": ObjectIdStr.to_obj(req.buy_order_id)})
    sell = db.order.find_one({"_id": ObjectIdStr.to_obj(req.sell_order_id)})
    if not buy or not sell:
        raise HTTPException(404, "Order not found")
    if buy["side"] != "buy" or sell["side"] != "sell":
        raise HTTPException(400, "Invalid order sides")
    if buy["course_id"] != sell["course_id"]:
        raise HTTPException(400, "Course mismatch")

    amount = min(req.amount, buy["amount"], sell["amount"])  # partial fills allowed
    price = sell["price_usd"]  # simple: execute at sell price

    # Update open quantities
    db.order.update_one({"_id": sell["_id"]}, {"$inc": {"amount": -amount}})
    db.order.update_one({"_id": buy["_id"]}, {"$inc": {"amount": -amount}})

    # Release seller reserved and transfer to buyer
    bal_seller = db.balance.find_one({"user_id": sell["user_id"], "course_id": sell["course_id"]})
    if not bal_seller:
        db.balance.insert_one({"user_id": sell["user_id"], "course_id": sell["course_id"], "amount": 0, "reserved": 0})
        bal_seller = db.balance.find_one({"user_id": sell["user_id"], "course_id": sell["course_id"]})
    db.balance.update_one({"_id": bal_seller["_id"]}, {"$inc": {"reserved": -amount}})

    bal_buyer = db.balance.find_one({"user_id": buy["user_id"], "course_id": buy["course_id"]})
    if bal_buyer:
        db.balance.update_one({"_id": bal_buyer["_id"]}, {"$inc": {"amount": amount}})
    else:
        db.balance.insert_one({"user_id": buy["user_id"], "course_id": buy["course_id"], "amount": amount})

    # Track treasury revenue for secondary market fee example (optional small fee)
    db.coursetoken.update_one({"course_id": buy["course_id"]}, {"$inc": {"treasury_revenue_usd": amount * price * 0.005}})

    return {"filled_amount": amount, "price_usd": price}

# ==== Balances ====
@app.get("/api/balances/{user_id}", response_model=List[dict])
def get_user_balances(user_id: str):
    return get_documents("balance", {"user_id": user_id})
