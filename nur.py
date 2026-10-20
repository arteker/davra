"""Davra Nur: ichki valyuta (hamyon, kunlik bonus, suhbatda do'stga o'tkazma, tarix)."""
import logging
import os
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, StrictInt
from sqlalchemy import CheckConstraint, Column, Float, ForeignKey, Integer, String, select, update
from sqlalchemy.exc import IntegrityError

import main as core

log = logging.getLogger("davra")
Base = core.Base
router = APIRouter(prefix="/api")

TZ_OFFSET = int(float(os.getenv("NUR_TZ_OFFSET_HOURS", "5")) * 3600)
BONUS_BASE, BONUS_STEP, BONUS_STEPS_MAX = 10, 2, 5
MAX_TRANSFER = int(os.getenv("NUR_MAX_TRANSFER", "5000"))
MAX_GRANT = 1_000_000
HISTORY_PAGE = 30


class NurAccount(Base):
    __tablename__ = "nur_accounts"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_nur_balance_nonneg"),)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    balance = Column(Integer, nullable=False, default=0, server_default="0")
    last_bonus_day = Column(Integer, nullable=False, default=0, server_default="0")
    streak = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(Float, nullable=False)


class NurTransaction(Base):
    __tablename__ = "nur_transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    kind = Column(String(12), nullable=False)
    amount = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    peer_id = Column(Integer, nullable=True)
    note = Column(String(80), nullable=False, default="", server_default="")
    created_at = Column(Float, nullable=False)


def _today(now=None):
    return int(((now or time.time()) + TZ_OFFSET) // 86400)


def _bonus_for(streak):
    return BONUS_BASE + BONUS_STEP * min(max(streak, 1) - 1, BONUS_STEPS_MAX)


def _clean_note(raw):
    note = " ".join((raw or "").split())[:60]
    if any(ord(c) < 32 for c in note):
        raise HTTPException(400, "Izohda noto'g'ri belgilar bor")
    return note


# YANGILANGAN _ACCOUNT FUNKSIYASI
async def _account(db, uid):
    acc = await db.get(NurAccount, uid)
    if acc:
        # Foydalanuvchi ma'lumotlariga balansni biriktiramiz (Avatar uchun)
        user = await db.get(core.User, uid)
        if user: user.nur_balance = acc.balance
        return acc
    db.add(NurAccount(user_id=uid, balance=0, last_bonus_day=0, streak=0, created_at=time.time()))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
    acc = await db.get(NurAccount, uid)
    user = await db.get(core.User, uid)
    if user: user.nur_balance = acc.balance
    return acc


async def _fresh(db, uid):
    await _account(db, uid)
    return (await db.execute(select(NurAccount).where(NurAccount.user_id == uid)
                             .execution_options(populate_existing=True))).scalar_one()


async def _state(db, uid):
    acc = await _fresh(db, uid)
    day = _today()
    can = acc.last_bonus_day < day
    alive = acc.last_bonus_day >= day - 1
    return {"balance": acc.balance, "can_claim": can,
            "streak": acc.streak if alive else 0,
            "next_bonus": _bonus_for(acc.streak + 1 if alive else 1),
            "next_claim_at": None if can else (day + 1) * 86400 - TZ_OFFSET,
            "max_transfer": MAX_TRANSFER}


def _ser_tx(tx, peers):
    peer = peers.get(tx.peer_id)
    return {"id": tx.id, "kind": tx.kind, "amount": tx.amount, "balance_after": tx.balance_after,
            "note": tx.note or "", "created_at": tx.created_at,
            "peer": {"id": peer.id, "display_name": peer.display_name, "username": peer.username} if peer else None}


async def _history(db, uid, before=0, limit=HISTORY_PAGE):
    stmt = select(NurTransaction).where(NurTransaction.user_id == uid)
    if before:
        stmt = stmt.where(NurTransaction.id < before)
    rows = (await db.execute(stmt.order_by(NurTransaction.id.desc()).limit(limit + 1))).scalars().all()
    more, rows = len(rows) > limit, rows[:limit]
    ids = {r.peer_id for r in rows if r.peer_id}
    peers = {}
    if ids:
        peers = {u.id: u for u in (await db.execute(select(core.User).where(core.User.id.in_(ids)))).scalars().all()}
    return [_ser_tx(r, peers) for r in rows], more


async def _balance(db, uid):
    return (await db.execute(select(NurAccount.balance).where(NurAccount.user_id == uid))).scalar_one()


def _notify(uid, balance, tx=None):
    event = {"type": "nur_update", "balance": balance}
    if tx:
        event["tx"] = tx
    return core.hub.send(uid, event)


@router.get("/nur")
async def get_nur(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        state = await _state(db, user.id)
        items, more = await _history(db, user.id)
        return dict(state, history=items, has_more=more)


@router.get("/nur/history")
async def get_history(before: int = 0, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        items, more = await _history(db, user.id, before=max(0, before))
        return {"history": items, "has_more": more}


@router.post("/nur/bonus")
async def claim_bonus(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if core.limited(f"nurbonus:{user.id}", 20, 600):
            raise HTTPException(429, "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring")
        acc = await _fresh(db, user.id)
        day = _today()
        if acc.last_bonus_day >= day:
            raise HTTPException(409, "Bugungi bonus allaqachon olingan")
        streak = acc.streak + 1 if acc.last_bonus_day == day - 1 else 1
        amount = _bonus_for(streak)
        res = await db.execute(update(NurAccount)
                               .where(NurAccount.user_id == user.id, NurAccount.last_bonus_day < day)
                               .values(balance=NurAccount.balance + amount, last_bonus_day=day, streak=streak)
                               .execution_options(synchronize_session=False))
        if res.rowcount != 1:
            await db.rollback()
            raise HTTPException(409, "Bugungi bonus allaqachon olingan")
        balance = await _balance(db, user.id)
        db.add(NurTransaction(user_id=user.id, kind="bonus", amount=amount, balance_after=balance,
                              note=f"{streak}-kun", created_at=time.time()))
        await db.commit()
        state = await _state(db, user.id)
    await _notify(user.id, balance)
    return dict(state, bonus=amount)


class TransferIn(BaseModel):
    chat_id: StrictInt
    amount: StrictInt
    note: str = ""


@router.post("/nur/transfer")
async def transfer(body: TransferIn, authorization: str = Header(None)):
    if not 1 <= body.amount <= MAX_TRANSFER:
        raise HTTPException(400, f"Miqdor 1 dan {MAX_TRANSFER} gacha bo'lsin")
    note = _clean_note(body.note)
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if core.limited(f"nurtx:{user.id}", 20, 3600):
            raise HTTPException(429, "Soatiga ko'pi bilan 20 ta o'tkazma qilish mumkin")
        chat = await core.member_chat(db, body.chat_id, user.id)
        if chat.status != "accepted":
            raise HTTPException(403, "Nur faqat qabul qilingan suhbatda yuboriladi")
        peer_id = chat.user_b if chat.user_a == user.id else chat.user_a
        if not await db.get(core.User, peer_id):
            raise HTTPException(404, "Foydalanuvchi topilmadi")
        await _account(db, user.id)
        await _account(db, peer_id)
        ops = {
            user.id: update(NurAccount).where(NurAccount.user_id == user.id, NurAccount.balance >= body.amount)
            .values(balance=NurAccount.balance - body.amount),
            peer_id: update(NurAccount).where(NurAccount.user_id == peer_id)
            .values(balance=NurAccount.balance + body.amount),
        }
        for uid in sorted(ops):
            res = await db.execute(ops[uid].execution_options(synchronize_session=False))
            if res.rowcount != 1:
                await db.rollback()
                raise HTTPException(400, "Nur yetarli emas")
        mine, theirs = await _balance(db, user.id), await _balance(db, peer_id)
        now = time.time()
        db.add(NurTransaction(user_id=user.id, kind="sent", amount=-body.amount, balance_after=mine,
                              peer_id=peer_id, note=note, created_at=now))
        db.add(NurTransaction(user_id=peer_id, kind="received", amount=body.amount, balance_after=theirs,
                              peer_id=user.id, note=note, created_at=now))
        await db.commit()
        chat_id = chat.id
    text = f"✦ {body.amount} Nur yubordi" + (f"\n{note}" if note else "")
    try:
        await core.post_direct(user.id, chat_id, text)
    except core.SendError as exc:
        log.warning("Nur o'tkazmasi xabari yozilmadi: %s", exc.message)
    await _notify(user.id, mine, {"kind": "sent", "amount": body.amount})
    await _notify(peer_id, theirs, {"kind": "received", "amount": body.amount})
    return {"balance": mine, "amount": body.amount}


class GrantIn(BaseModel):
    username: str
    amount: StrictInt
    note: str = ""


@router.post("/nur/grant")
async def grant(body: GrantIn, authorization: str = Header(None)):
    if not 1 <= body.amount <= MAX_GRANT:
        raise HTTPException(400, f"Miqdor 1 dan {MAX_GRANT} gacha bo'lsin")
    note = _clean_note(body.note)
    username = body.username.strip().lower().lstrip("@")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if not user.is_owner:
            raise HTTPException(403, "Bu amal faqat Davra egasi uchun")
        if core.limited(f"nurgrant:{user.id}", 60, 3600):
            raise HTTPException(429, "Juda ko'p urinish")
        target = (await db.execute(select(core.User).where(core.User.username == username))).scalar_one_or_none()
        if not target:
            raise HTTPException(404, "Foydalanuvchi topilmadi")
        await _account(db, target.id)
        await db.execute(update(NurAccount).where(NurAccount.user_id == target.id)
                         .values(balance=NurAccount.balance + body.amount).execution_options(synchronize_session=False))
        balance = await _balance(db, target.id)
        db.add(NurTransaction(user_id=target.id, kind="grant", amount=body.amount, balance_after=balance,
                              peer_id=user.id, note=note, created_at=time.time()))
        await db.commit()
    await _notify(target.id, balance, {"kind": "grant", "amount": body.amount})
    return {"ok": True, "username": target.username, "balance": balance}


core.app.include_router(router)