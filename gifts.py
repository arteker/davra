"""Davra: Sovg'alar do'koni (Gifts)"""
import time
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, Float, ForeignKey, Integer, String, select, update
import main as core
import nur

router = APIRouter(prefix="/api/gifts")

# Sovg'alar ro'yxati va narxlari (Nurda)
GIFTS_CATALOG = {
    "rose":   {"name": "Atirgul",   "price": 10},
    "star":   {"name": "Yulduz",    "price": 50},
    "bear":   {"name": "Ayiqcha",   "price": 100},
    "crown":  {"name": "Toj",       "price": 500},
    "rocket": {"name": "Raketa",    "price": 1000},
}

class UserGift(core.Base):
    __tablename__ = "user_gifts"
    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    gift_type = Column(String(20), nullable=False)
    created_at = Column(Float, nullable=False)

@router.get("/catalog")
async def get_catalog():
    return GIFTS_CATALOG

@router.get("/received/{username}")
async def get_user_gifts(username: str, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        target = (await db.execute(select(core.User).where(core.User.username == username.lower()))).scalar_one_or_none()
        if not target:
            raise HTTPException(404, "Foydalanuvchi topilmadi")
        
        gifts = (await db.execute(select(UserGift).where(UserGift.receiver_id == target.id).order_by(UserGift.id.desc()))).scalars().all()
        result = []
        for g in gifts:
            sender = await db.get(core.User, g.sender_id)
            result.append({
                "id": g.id, "gift_type": g.gift_type, "created_at": g.created_at,
                "sender_name": sender.display_name if sender else "Noma'lum"
            })
        return result

class SendGiftIn(BaseModel):
    receiver_id: int
    gift_type: str

@router.post("/send")
async def send_gift(body: SendGiftIn, authorization: str = Header(None)):
    if body.gift_type not in GIFTS_CATALOG:
        raise HTTPException(400, "Bunday sovg'a mavjud emas")
    
    price = GIFTS_CATALOG[body.gift_type]["price"]
    gift_name = GIFTS_CATALOG[body.gift_type]["name"]

    async with core.SessionLocal() as db:
        sender = await core.require_user(db, authorization)
        receiver = await db.get(core.User, body.receiver_id)
        
        if not receiver or receiver.id == sender.id:
            raise HTTPException(400, "Qabul qiluvchi noto'g'ri tanlangan")

        # Nur hisobini tekshirish
        acc = await nur._account(db, sender.id)
        if acc.balance < price:
            raise HTTPException(400, f"Hisobingizda yetarli Nur yo'q. Kerak: {price} ✦")

        # Nurni yechish
        res = await db.execute(
            update(nur.NurAccount)
            .where(nur.NurAccount.user_id == sender.id, nur.NurAccount.balance >= price)
            .values(balance=nur.NurAccount.balance - price)
        )
        if res.rowcount != 1:
            raise HTTPException(400, "Nur yechishda xatolik")

        # Tarixga yozish (Nur)
        new_balance = acc.balance - price
        db.add(nur.NurTransaction(
            user_id=sender.id, kind="gift", amount=-price, balance_after=new_balance, 
            note=f"Sovg'a: {gift_name}", created_at=time.time()
        ))

        # Sovg'ani saqlash
        gift = UserGift(sender_id=sender.id, receiver_id=receiver.id, gift_type=body.gift_type, created_at=time.time())
        db.add(gift)
        await db.commit()

        # WebSocket orqali qabul qiluvchiga xabar yuborish
        await core.hub.send(receiver.id, {
            "type": "gift_received",
            "gift": {
                "sender_name": sender.display_name,
                "gift_type": body.gift_type,
                "gift_name": gift_name
            }
        })
        # O'ziga balans yangilanganini yuborish
        await core.hub.send(sender.id, {"type": "nur_update", "balance": new_balance})

        return {"ok": True, "new_balance": new_balance}

core.app.include_router(router)