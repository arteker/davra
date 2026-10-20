import time
from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import update
import main as core
import nur

router = APIRouter(prefix="/api/premium")

PREMIUM_PRICE = 1000  # 1 oylik Premium narxi (Nurda)
PREMIUM_DAYS = 30

@router.post("/buy")
async def buy_premium(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        
        if core.limited(f"premium_buy:{user.id}", 5, 60):
            raise HTTPException(429, "Juda ko'p urinish. Biroz kuting.")
            
        # Hisobni tekshirish
        acc = await nur._account(db, user.id)
        if acc.balance < PREMIUM_PRICE:
            raise HTTPException(400, f"Hisobingizda yetarli Nur yo'q. Kerak: {PREMIUM_PRICE} ✦")
            
        # Nur yechish
        res = await db.execute(
            update(nur.NurAccount)
            .where(nur.NurAccount.user_id == user.id, nur.NurAccount.balance >= PREMIUM_PRICE)
            .values(balance=nur.NurAccount.balance - PREMIUM_PRICE)
        )
        if res.rowcount != 1:
            raise HTTPException(400, "Nur yetarli emas yoki xatolik yuz berdi")
            
        # Tarixga yozish
        new_balance = acc.balance - PREMIUM_PRICE
        db.add(nur.NurTransaction(
            user_id=user.id, kind="premium", amount=-PREMIUM_PRICE, 
            balance_after=new_balance, note="Premium obuna (1 oy)", created_at=time.time()
        ))

        # Userni premium qilish (agar avvaldan bo'lsa, ustiga qo'shiladi)
        now = time.time()
        base_time = user.premium_until if (user.premium_until and user.premium_until > now) else now
        new_until = base_time + (PREMIUM_DAYS * 86400)
        
        user.is_premium = True
        user.premium_until = new_until
        
        await db.commit()
        
        # Frontendga xabar yuborish
        await core.hub.send(user.id, {"type": "profile_update", "user": core.ser_user(user)})
        await core.hub.send(user.id, {"type": "nur_update", "balance": new_balance})
        
        return {"ok": True, "premium_until": new_until, "new_balance": new_balance}