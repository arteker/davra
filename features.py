"""Profillar, guruh/kanallar, takliflar, reaksiyalar va xabar vositalari (Davra)."""
import asyncio
import io
import json
import logging
import re
import secrets
import time
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import (Boolean, Column, Float, ForeignKey, Integer, LargeBinary, String, Text,
                        UniqueConstraint, and_, delete, func, or_, select)
from sqlalchemy.exc import IntegrityError

import main as core

log = logging.getLogger("davra")
Base = core.Base
router = APIRouter(prefix="/api")

MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_GROUP_MEMBERS = 100
MAX_PENDING_INVITES = 30           # bitta odamda kutilayotgan takliflar chegarasi
LEGACY_UPLOAD_DIR = Path(__file__).resolve().parent / "static" / "uploads"
LEGACY_NAME_RE = re.compile(r"[0-9a-f]{48}\.jpg")
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_]{3,32})")

# Admin huquqlari (Telegramdagidek)
RIGHT_KEYS = ("change_info", "post_messages", "delete_messages", "pin_messages", "add_members", "ban_users", "add_admins")
DEFAULT_ADMIN_RIGHTS = {"change_info": True, "post_messages": True, "delete_messages": True, "pin_messages": True,
                        "add_members": True, "ban_users": True, "add_admins": False}


def rights_of(role, raw):
    """Rol va saqlangan JSON bo'yicha to'liq huquqlar lug'ati."""
    if role == "owner":
        return {k: True for k in RIGHT_KEYS}
    if role != "admin":
        return {k: False for k in RIGHT_KEYS}
    stored = core._j(raw) if raw else None
    base = dict(DEFAULT_ADMIN_RIGHTS)
    if isinstance(stored, dict):
        for k in RIGHT_KEYS:
            if k in stored:
                base[k] = bool(stored[k])
    return base


def clean_rights(raw):
    if not isinstance(raw, dict):
        raise HTTPException(400, "Huquqlar noto'g'ri")
    return {k: bool(raw.get(k, False)) for k in RIGHT_KEYS}


def member_rights(member):
    return rights_of(member.role, member.rights)


# ------------------------------------------------------------------ modellar
class Room(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True)
    kind = Column(String(12), nullable=False)  # group | channel
    title = Column(String(80), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(Float, nullable=False)
    is_public = Column(Boolean, nullable=False, default=False)
    pinned_id = Column(Integer, nullable=True)  # qadalgan xabar
    description = Column(String(255), nullable=False, default="", server_default="")
    avatar_url = Column(String(255), nullable=True)
    sign_messages = Column(Boolean, nullable=False, default=False, server_default="0")   # kanalda post muallifini ko'rsatish
    reactions_enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    invite_code = Column(String(32), nullable=True, index=True)                           # taklif havolasi


class RoomMember(Base):
    __tablename__ = "room_members"
    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_room_member"),)
    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    role = Column(String(12), nullable=False, default="member")  # owner | admin | member
    joined_at = Column(Float, nullable=False)
    last_read_id = Column(Integer, nullable=False, default=0, server_default="0")  # o'qilganlik chegarasi
    tag = Column(String(16), nullable=False, default="", server_default="")        # a'zo yorlig'i (guruhda)
    rights = Column(Text, nullable=True)                                           # admin huquqlari (JSON)
    promoted_by = Column(Integer, nullable=True)


class RoomMessage(Base):
    __tablename__ = "room_messages"
    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    created_at = Column(Float, nullable=False)
    edited_at = Column(Float, nullable=True)
    deleted = Column(Boolean, nullable=False, default=False)
    attach = Column(Text, nullable=True)           # rasm/fayl/ovoz: JSON
    fwd = Column(String(80), nullable=True)        # yo'naltirilgan xabar manbasi
    reply_to_id = Column(Integer, nullable=True)   # javob berilgan xabar


class RoomInvite(Base):
    """Notanish odamni guruhga majburan qo'shib bo'lmaydi: u taklifni qabul qilishi kerak."""
    __tablename__ = "room_invites"
    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_room_invite"),)
    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    invited_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(12), nullable=False, default="member")
    status = Column(String(10), nullable=False, default="pending")  # pending | declined
    created_at = Column(Float, nullable=False)


class RoomBan(Base):
    """Chiqarilgan (bloklangan) a'zolar: qayta qo'shila olmaydi, admin blokdan chiqarishi mumkin."""
    __tablename__ = "room_bans"
    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_room_ban"),)
    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    banned_by = Column(Integer, nullable=False)
    created_at = Column(Float, nullable=False)


class RoomAvatar(Base):
    __tablename__ = "room_avatars"
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True)
    data = Column(LargeBinary, nullable=False)
    updated_at = Column(Float, nullable=False)


class UserAvatar(Base):
    """Profil rasmi bazada saqlanadi — Render'da diskka bog'liq bo'lmaslik uchun."""
    __tablename__ = "user_avatars"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data = Column(LargeBinary, nullable=False)
    updated_at = Column(Float, nullable=False)


class Reaction(Base):
    __tablename__ = "message_reactions"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", "emoji", name="uq_direct_reaction"),
        UniqueConstraint("room_message_id", "user_id", "emoji", name="uq_room_reaction"),
    )
    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=True, index=True)
    room_message_id = Column(Integer, ForeignKey("room_messages.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    emoji = Column(String(8), nullable=False)


# ------------------------------------------------------------------ migratsiya
async def migrate_feature_schema():
    """Qo'shimcha (additive) migratsiya: yetishmayotgan ustunlarni qo'shadi, ma'lumotni o'chirmaydi."""
    added = set()
    async with core.engine.begin() as conn:
        def work(sync_conn):
            from sqlalchemy import inspect, text
            inspector = inspect(sync_conn)
            additions = {
                "users": {
                    "avatar_url": "VARCHAR(255)",
                    "bio": "VARCHAR(160) NOT NULL DEFAULT ''",
                    "is_owner": "BOOLEAN NOT NULL DEFAULT FALSE",
                    "hide_seen": "BOOLEAN NOT NULL DEFAULT FALSE",
                    "allow_requests": "BOOLEAN NOT NULL DEFAULT TRUE",
                },
                "sessions": {"device": "VARCHAR(120)"},
                "chats": {"pinned_id": "INTEGER"},
                "messages": {
                    "edited_at": "FLOAT", "deleted": "BOOLEAN NOT NULL DEFAULT FALSE",
                    "attach": "TEXT", "fwd": "VARCHAR(80)", "reply_to_id": "INTEGER",
                },
                "rooms": {"pinned_id": "INTEGER", "description": "VARCHAR(255) NOT NULL DEFAULT ''",
                          "avatar_url": "VARCHAR(255)", "sign_messages": "BOOLEAN NOT NULL DEFAULT FALSE",
                          "reactions_enabled": "BOOLEAN NOT NULL DEFAULT TRUE", "invite_code": "VARCHAR(32)"},
                "room_members": {"last_read_id": "INTEGER NOT NULL DEFAULT 0", "tag": "VARCHAR(16) NOT NULL DEFAULT ''",
                                 "rights": "TEXT", "promoted_by": "INTEGER"},
                "room_messages": {"attach": "TEXT", "fwd": "VARCHAR(80)", "reply_to_id": "INTEGER"},
            }
            for table, wanted in additions.items():
                if not inspector.has_table(table):
                    continue
                existing = {column["name"] for column in inspector.get_columns(table)}
                for name, declaration in wanted.items():
                    if name not in existing:
                        sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"))
                        added.add((table, name))
            if ("room_members", "last_read_id") in added:
                # Eski a'zolar uchun mavjud xabarlar "o'qilgan" hisoblanadi (birdan yuzlab o'qilmagan chiqmasin)
                sync_conn.execute(text(
                    "UPDATE room_members SET last_read_id = COALESCE((SELECT MAX(id) FROM room_messages "
                    "WHERE room_messages.room_id = room_members.room_id), 0)"))
        await conn.run_sync(work)
    await _migrate_legacy_avatars()


async def _migrate_legacy_avatars():
    """Eski /uploads/*.jpg avatarlarni bazaga ko'chiradi (fayl topilmasa avatar tozalanadi)."""
    async with core.SessionLocal() as db:
        users = (await db.execute(select(core.User).where(core.User.avatar_url.like("/uploads/%")))).scalars().all()
        for user in users:
            name = user.avatar_url.rsplit("/", 1)[-1]
            path = LEGACY_UPLOAD_DIR / name
            if LEGACY_NAME_RE.fullmatch(name) and path.is_file():
                await _store_avatar(db, user, path.read_bytes())
            else:
                user.avatar_url = None
        if users:
            await db.commit()


# ------------------------------------------------------------------ yordamchilar
def _summary(rows, viewer_id):
    """rows: [(emoji, user_id), ...] -> [{emoji, count, me}]"""
    totals = {}
    for emoji, uid in rows:
        item = totals.setdefault(emoji, {"emoji": emoji, "count": 0, "me": False})
        item["count"] += 1
        if uid == viewer_id:
            item["me"] = True
    return list(totals.values())


async def _reactions_bulk(db, *, direct=None, room=None):
    ids = list(direct if direct is not None else room or [])
    out = defaultdict(list)
    if not ids:
        return out
    column = Reaction.message_id if direct is not None else Reaction.room_message_id
    rows = (await db.execute(select(column, Reaction.emoji, Reaction.user_id).where(column.in_(ids)))).all()
    for message_id, emoji, user_id in rows:
        out[message_id].append((emoji, user_id))
    return out


async def _users_bulk(db, ids):
    ids = list(ids)
    if not ids:
        return {}
    rows = (await db.execute(select(core.User).where(core.User.id.in_(ids)))).scalars().all()
    
    # NURNING BALANSINI HAM YUKLAYMIZ (Boshqalarda 3D avatar ko'rinishi uchun)
    import nur as nur_module
    nur_rows = (await db.execute(select(nur_module.NurAccount.user_id, nur_module.NurAccount.balance).where(nur_module.NurAccount.user_id.in_(ids)))).all()
    balances = {uid: bal for uid, bal in nur_rows}
    
    for u in rows:
        u.nur_balance = balances.get(u.id, 0)
        
    return {user.id: user for user in rows}

async def _reply_previews(db, model, ids):
    """Javob berilgan xabarlarning qisqa ko'rinishi (bitta-ikkita so'rov bilan)."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (await db.execute(select(model).where(model.id.in_(ids)))).scalars().all()
    users = await _users_bulk(db, {r.sender_id for r in rows})
    out = {}
    for row in rows:
        author = users.get(row.sender_id)
        gone = bool(row.deleted)
        out[row.id] = {"id": row.id, "sender_id": row.sender_id,
                       "sender_name": author.display_name if author else "",
                       "text": "Xabar o'chirildi" if gone else core.snippet(core.preview_text(row.text, row.attach)),
                       "deleted": gone}
    return out


async def _get_member(db, room_id, user_id):
    return (await db.execute(select(RoomMember).where(
        RoomMember.room_id == room_id, RoomMember.user_id == user_id))).scalar_one_or_none()


async def _membership(db, room_id, user_id):
    member = await _get_member(db, room_id, user_id)
    if not member:
        raise HTTPException(404, "Suhbat topilmadi yoki siz a'zo emassiz")
    return member


async def _room(db, room_id):
    room = await db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "Suhbat topilmadi")
    return room


async def _add_member(db, room_id, user_id, role):
    latest = (await db.execute(select(func.max(RoomMessage.id)).where(RoomMessage.room_id == room_id))).scalar_one() or 0
    db.add(RoomMember(room_id=room_id, user_id=user_id, role=role, joined_at=time.time(), last_read_id=latest))


async def _member_count(db, room_id):
    return (await db.execute(select(func.count()).select_from(RoomMember).where(RoomMember.room_id == room_id))).scalar_one()


def _room_msg_base(message, sender, tag=""):
    deleted = bool(message.deleted)
    return {"id": message.id, "room_id": message.room_id, "sender_id": message.sender_id,
            "sender_name": sender.display_name if sender else "",
            "sender_username": sender.username if sender else "",
            "sender_is_owner": bool(sender and sender.is_owner),
            "sender_is_premium": bool(sender and getattr(sender, 'is_premium', False)), # <--- YANGI QATOR
            "sender_tag": tag or "",
            "text": "Xabar o'chirildi" if deleted else message.text,
            "created_at": message.created_at, "edited": message.edited_at is not None,
            "deleted": deleted, "read": False,
            "attachment": None if deleted else core._j(message.attach),
            "fwd": None if deleted else message.fwd,
            "reply_to": message.reply_to_id, "reply": None}

async def _tags_for(db, room_id, user_ids):
    user_ids = list(user_ids)
    if not user_ids:
        return {}
    rows = (await db.execute(select(RoomMember.user_id, RoomMember.tag).where(
        RoomMember.room_id == room_id, RoomMember.user_id.in_(user_ids)))).all()
    return {uid: tag for uid, tag in rows}


async def _serialize_room_messages(db, messages, viewer_id):
    messages = list(messages)
    reactions = await _reactions_bulk(db, room=[m.id for m in messages])
    senders = await _users_bulk(db, {m.sender_id for m in messages})
    previews = await _reply_previews(db, RoomMessage, [m.reply_to_id for m in messages])
    result = []
    by_room = defaultdict(list)
    for m in messages:
        by_room[m.room_id].append(m.sender_id)
    tags = {}
    for room_id, ids in by_room.items():
        tags[room_id] = await _tags_for(db, room_id, set(ids))
    for m in messages:
        base = _room_msg_base(m, senders.get(m.sender_id), tags.get(m.room_id, {}).get(m.sender_id, ""))
        base["reply"] = previews.get(m.reply_to_id)
        result.append(dict(base, reactions=_summary(reactions.get(m.id, []), viewer_id)))
    return result


async def serialize_direct_messages(db, messages, user_id):
    messages = list(messages)
    reactions = await _reactions_bulk(db, direct=[m.id for m in messages])
    previews = await _reply_previews(db, core.Message, [m.reply_to_id for m in messages])
    out = []
    for m in messages:
        item = core.ser_msg(m)
        item["reply"] = previews.get(m.reply_to_id)
        out.append(dict(item, reactions=_summary(reactions.get(m.id, []), user_id)))
    return out


async def _unread_by_member(db, room_id):
    """Xonadagi har bir a'zo uchun o'qilmagan xabarlar soni (bitta so'rov)."""
    stmt = (select(RoomMember.user_id, func.count(RoomMessage.id))
            .select_from(RoomMember)
            .outerjoin(RoomMessage, and_(RoomMessage.room_id == RoomMember.room_id,
                                         RoomMessage.id > RoomMember.last_read_id,
                                         RoomMessage.sender_id != RoomMember.user_id,
                                         RoomMessage.deleted.is_(False)))
            .where(RoomMember.room_id == room_id)
            .group_by(RoomMember.user_id))
    return {uid: count for uid, count in (await db.execute(stmt)).all()}


async def _unread_for_user(db, user_id):
    """Bitta foydalanuvchining barcha xonalari bo'yicha o'qilmaganlar (bitta so'rov)."""
    stmt = (select(RoomMessage.room_id, func.count(RoomMessage.id))
            .join(RoomMember, and_(RoomMember.room_id == RoomMessage.room_id, RoomMember.user_id == user_id))
            .where(RoomMessage.id > RoomMember.last_read_id, RoomMessage.sender_id != user_id,
                   RoomMessage.deleted.is_(False))
            .group_by(RoomMessage.room_id))
    return {room_id: count for room_id, count in (await db.execute(stmt)).all()}


async def _unread_one(db, room_id, user_id):
    stmt = (select(func.count(RoomMessage.id)).select_from(RoomMessage)
            .join(RoomMember, and_(RoomMember.room_id == RoomMessage.room_id, RoomMember.user_id == user_id))
            .where(RoomMessage.room_id == room_id, RoomMessage.id > RoomMember.last_read_id,
                   RoomMessage.sender_id != user_id, RoomMessage.deleted.is_(False)))
    return (await db.execute(stmt)).scalar_one()


async def _room_payloads(db, room, viewers=None, unread=None):
    """Xona ma'lumotini har bir ko'ruvchi uchun TAYYORLAYDI — a'zolar soniga bog'liq bo'lmagan so'rovlar bilan."""
    members = (await db.execute(select(RoomMember.user_id, RoomMember.role, RoomMember.rights).where(RoomMember.room_id == room.id))).all()
    roles = {uid: role for uid, role, _ in members}
    rights_raw = {uid: raw for uid, _, raw in members}
    if unread is None:
        if viewers is not None and len(viewers) == 1:
            unread = {viewers[0]: await _unread_one(db, room.id, viewers[0])}
        else:
            unread = await _unread_by_member(db, room.id)
    last = (await db.execute(select(RoomMessage).where(RoomMessage.room_id == room.id)
                             .order_by(RoomMessage.id.desc()).limit(1))).scalar_one_or_none()
    last_base, last_rows = None, []
    if last:
        sender = await db.get(core.User, last.sender_id)
        last_base = _room_msg_base(last, sender)
        last_rows = (await _reactions_bulk(db, room=[last.id]))[last.id]
    pinned = None
    if room.pinned_id:
        pm = await db.get(RoomMessage, room.pinned_id)
        if pm and pm.room_id == room.id and not pm.deleted:
            pinned = {"id": pm.id, "sender_id": pm.sender_id, "text": core.snippet(core.preview_text(pm.text, pm.attach))}
    creator = await db.get(core.User, room.created_by)
    verified = bool(creator and creator.is_owner)
    out = {}
    for uid in (viewers if viewers is not None else list(roles)):
        if uid not in roles:
            continue
        out[uid] = {"id": room.id, "room_type": room.kind, "title": room.title,
                    "created_by": room.created_by, "created_at": room.created_at,
                    "is_public": room.is_public, "verified": verified, "role": roles[uid],
                    "description": room.description or "", "avatar_url": room.avatar_url,
                    "sign_messages": bool(room.sign_messages), "reactions_enabled": room.reactions_enabled is not False,
                    "rights": rights_of(roles[uid], rights_raw.get(uid)), "member_count": len(roles),
                    "last_message": dict(last_base, reactions=_summary(last_rows, uid)) if last_base else None,
                    "unread": unread.get(uid, 0), "pinned": pinned}
    return out


async def serialize_room(db, room, user_id):
    payloads = await _room_payloads(db, room, [user_id])
    if user_id not in payloads:
        raise HTTPException(404, "Suhbat topilmadi yoki siz a'zo emassiz")
    return payloads[user_id]


async def _broadcast_room_update(db, room):
    payloads = await _room_payloads(db, room)
    await core.hub.broadcast([(uid, {"type": "room_update", "room": p}) for uid, p in payloads.items()])


async def _broadcast_profile(db, user):
    direct_ids = (await db.execute(select(core.Chat.user_a, core.Chat.user_b).where(
        or_(core.Chat.user_a == user.id, core.Chat.user_b == user.id)))).all()
    recipients = {b if a == user.id else a for a, b in direct_ids}
    room_ids = (await db.execute(select(RoomMember.room_id).where(RoomMember.user_id == user.id))).scalars().all()
    if room_ids:
        room_users = (await db.execute(select(RoomMember.user_id).where(
            RoomMember.room_id.in_(room_ids), RoomMember.user_id != user.id))).scalars().all()
        recipients.update(room_users)
    recipients.add(user.id)
    event = {"type": "profile_update", "user": core.ser_user(user)}
    await core.hub.broadcast([(rid, event) for rid in recipients])


# ------------------------------------------------------------------ avatar
ALLOWED_IMAGE_TYPES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def _process_avatar(data, content_type):
    """Rasmni tekshiradi va 512x512 kvadrat JPEG'ga aylantiradi (EXIF burilishi hisobga olinadi)."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 20_000_000
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(415, "Faqat JPEG, PNG yoki WebP rasm yuklang")
    try:
        probe = Image.open(io.BytesIO(data))
        if probe.width * probe.height > 16_000_000:
            raise HTTPException(400, "Rasm o'lchami juda katta")
        if probe.format != ALLOWED_IMAGE_TYPES[content_type]:
            raise HTTPException(415, "Rasm formati qo'llab-quvvatlanmaydi")
        probe.verify()
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))  # telefon rasmlari yotib qolmasin
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, (255, 255, 255))
            flat.paste(image, mask=image.split()[3])
            image = flat
        else:
            image = image.convert("RGB")
        image = ImageOps.fit(image, (512, 512), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=86, optimize=True)
        return out.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise HTTPException(400, "Yaroqli rasm faylini tanlang")


async def _store_avatar(db, user, jpeg):
    row = await db.get(UserAvatar, user.id)
    if row:
        row.data, row.updated_at = jpeg, time.time()
    else:
        db.add(UserAvatar(user_id=user.id, data=jpeg, updated_at=time.time()))
    user.avatar_url = f"/api/avatar/{user.id}?v={int(time.time() * 1000)}"


@router.get("/avatar/{user_id}")
async def get_avatar(user_id: int):
    async with core.SessionLocal() as db:
        row = await db.get(UserAvatar, user_id)
    if not row:
        raise HTTPException(404, "Rasm topilmadi")
    # URL'dagi ?v=... har o'zgarishda yangilanadi, shuning uchun uzoq keshlash xavfsiz
    return Response(content=row.data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000, immutable",
                             "X-Content-Type-Options": "nosniff"})


class ProfileIn(BaseModel):
    display_name: str
    bio: str = ""


@router.put("/profile")
async def update_profile(body: ProfileIn, authorization: str = Header(None)):
    name = body.display_name.strip()
    if not name or len(name) > 50 or any(ord(c) < 32 for c in name):
        raise HTTPException(400, "Ism 1–50 belgidan iborat bo'lsin")
    bio = body.bio.strip()
    if len(bio) > 160 or any(ord(c) < 32 and c not in "\n\t" for c in bio):
        raise HTTPException(400, "Bio 160 belgigacha bo'lishi mumkin")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if not user.is_owner and core.display_name_reserved(name):
            raise HTTPException(400, "Bu ismni ishlatib bo'lmaydi, boshqasini tanlang")
        user.display_name = name
        user.bio = bio
        await db.commit()
        await _broadcast_profile(db, user)
        return core.ser_user(user)


@router.post("/profile/avatar")
async def update_avatar(request: Request, authorization: str = Header(None)):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_AVATAR_BYTES):
        raise HTTPException(413, "Rasm 2 MB dan kichik bo'lishi kerak")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
    if core.limited(f"avatar:{user.id}", 10, 3600):
        raise HTTPException(429, "Soatiga ko'pi bilan 10 marta rasm almashtirish mumkin")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_AVATAR_BYTES:
            raise HTTPException(413, "Rasm 2 MB dan kichik bo'lishi kerak")
    if not chunks:
        raise HTTPException(400, "Rasm tanlanmadi")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    jpeg = await asyncio.to_thread(_process_avatar, bytes(chunks), content_type)
    async with core.SessionLocal() as db:
        user = await db.get(core.User, user.id)
        await _store_avatar(db, user, jpeg)
        await db.commit()
        await _broadcast_profile(db, user)
        return {"avatar_url": user.avatar_url}


@router.delete("/profile/avatar")
async def delete_avatar(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        await db.execute(delete(UserAvatar).where(UserAvatar.user_id == user.id))
        user.avatar_url = None
        await db.commit()
        await _broadcast_profile(db, user)
    return {"avatar_url": None}


# ------------------------------------------------------------------ xonalar (guruh / kanal)
class RoomIn(BaseModel):
    kind: str
    title: str
    usernames: list[str] = []
    is_public: bool = True


class RoomUpdateIn(BaseModel):
    title: str | None = None
    description: str | None = None
    sign_messages: bool | None = None
    reactions_enabled: bool | None = None
    is_public: bool | None = None


class InviteIn(BaseModel):
    username: str
    role: str = "member"


class RoleIn(BaseModel):
    role: str


class TransferIn(BaseModel):
    username: str


@router.get("/rooms")
async def list_rooms(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        rooms = (await db.execute(select(Room).join(RoomMember, RoomMember.room_id == Room.id)
                                  .where(RoomMember.user_id == user.id)
                                  .order_by(Room.created_at.desc()))).scalars().all()
        unread = await _unread_for_user(db, user.id)
        result = []
        for room in rooms:
            payloads = await _room_payloads(db, room, [user.id], unread={user.id: unread.get(room.id, 0)})
            result.append(payloads[user.id])
        return result


@router.get("/channels")
async def discover_channels(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        rows = (await db.execute(select(Room).where(Room.kind == "channel", Room.is_public.is_(True))
                                 .order_by(Room.created_at.desc()).limit(100))).scalars().all()
        if not rows:
            return []
        ids = [room.id for room in rows]
        counts = dict((await db.execute(select(RoomMember.room_id, func.count()).where(
            RoomMember.room_id.in_(ids)).group_by(RoomMember.room_id))).all())
        joined = set((await db.execute(select(RoomMember.room_id).where(
            RoomMember.user_id == user.id, RoomMember.room_id.in_(ids)))).scalars().all())
        owners = set((await db.execute(select(core.User.id).where(
            core.User.is_owner.is_(True), core.User.id.in_({room.created_by for room in rows})))).scalars().all())
        return [{"id": room.id, "title": room.title, "member_count": counts.get(room.id, 0),
                 "joined": room.id in joined, "verified": room.created_by in owners} for room in rows]


async def _is_banned(db, room_id, user_id):
    return (await db.execute(select(RoomBan.id).where(RoomBan.room_id == room_id, RoomBan.user_id == user_id))).first() is not None


async def _chat_status(db, a, b):
    x, y = core.pair(a, b)
    chat = (await db.execute(select(core.Chat).where(core.Chat.user_a == x, core.Chat.user_b == y))).scalar_one_or_none()
    return chat.status if chat else None


async def _invite_or_add(db, room, inviter, invited, role):
    """Do'st (qabul qilingan chat) bo'lsa — to'g'ridan-to'g'ri qo'shadi, notanish bo'lsa — taklif yuboradi.
    Qaytaradi: (holat, invite|None). Holatlar: added | invited | member | blocked | full | declined | limit | ratelimit"""
    if await _get_member(db, room.id, invited.id):
        return "member", None
    if await _is_banned(db, room.id, invited.id):
        return "banned", None
    status = await _chat_status(db, inviter.id, invited.id)
    if status == "blocked":
        return "blocked", None
    if room.kind == "group" and await _member_count(db, room.id) >= MAX_GROUP_MEMBERS:
        return "full", None
    if status == "accepted":
        await _add_member(db, room.id, invited.id, role)
        return "added", None
    if not invited.allow_requests:  # notanishlardan so'rov/taklif qabul qilmaydi
        return "blocked", None
    invite = (await db.execute(select(RoomInvite).where(
        RoomInvite.room_id == room.id, RoomInvite.user_id == invited.id))).scalar_one_or_none()
    if invite:
        return ("declined" if invite.status == "declined" else "invited"), None
    pending = (await db.execute(select(func.count()).select_from(RoomInvite).where(
        RoomInvite.user_id == invited.id, RoomInvite.status == "pending"))).scalar_one()
    if pending >= MAX_PENDING_INVITES:
        return "limit", None
    if core.limited(f"invite:{inviter.id}", 60, 3600):
        return "ratelimit", None
    invite = RoomInvite(room_id=room.id, user_id=invited.id, invited_by=inviter.id, role=role,
                        status="pending", created_at=time.time())
    db.add(invite)
    await db.flush()
    return "invited", invite


def _invite_event(invite, room, inviter):
    return {"type": "room_invite", "invite": {
        "id": invite.id, "room_id": room.id, "title": room.title, "kind": room.kind,
        "invited_by": inviter.display_name, "invited_by_username": inviter.username,
        "created_at": invite.created_at}}


@router.post("/rooms")
async def create_room(body: RoomIn, authorization: str = Header(None)):
    kind = body.kind.strip().lower()
    title = body.title.strip()
    if kind not in {"group", "channel"} or not title or len(title) > 80 or any(ord(c) < 32 for c in title):
        raise HTTPException(400, "Guruh/kanal turi va 1–80 belgili nom kiriting")
    usernames = list(dict.fromkeys(re.sub(r"[^a-z0-9_]", "", name.strip().lower().lstrip("@"))
                                   for name in body.usernames))
    if len(usernames) > 99 or any(not core.USERNAME_RE.fullmatch(name) for name in usernames):
        raise HTTPException(400, "Ishtirokchilar ro'yxati noto'g'ri (ko'pi bilan 99 ta)")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if core.limited(f"newroom:{user.id}", 10, 3600):
            raise HTTPException(429, "Soatiga ko'pi bilan 10 ta yangi guruh/kanal yaratish mumkin")
        room = Room(kind=kind, title=title, created_by=user.id, created_at=time.time(),
                    is_public=(kind == "channel" and body.is_public))
        db.add(room)
        await db.flush()
        db.add(RoomMember(room_id=room.id, user_id=user.id, role="owner", joined_at=time.time(), last_read_id=0))
        added_ids, invites = [], []
        if kind == "group":
            for name in usernames:
                invited = (await db.execute(select(core.User).where(core.User.username == name))).scalar_one_or_none()
                if not invited:
                    raise HTTPException(404, f"Foydalanuvchi topilmadi: @{name}")
                if invited.id == user.id:
                    continue
                state, invite = await _invite_or_add(db, room, user, invited, "member")
                if state == "added":
                    added_ids.append(invited.id)
                elif state == "invited" and invite:
                    invites.append((invited.id, invite))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "Suhbat yaratilmadi")
        pairs = []
        if added_ids:
            payloads = await _room_payloads(db, room, added_ids)
            pairs += [(uid, {"type": "room_update", "room": payload}) for uid, payload in payloads.items()]
        pairs += [(uid, _invite_event(invite, room, user)) for uid, invite in invites]
        await core.hub.broadcast(pairs)
        result = await serialize_room(db, room, user.id)
        result.update({"added": len(added_ids), "invited": len(invites)})
        return result


@router.post("/rooms/{room_id}/join")
async def join_channel(room_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room = await _room(db, room_id)
        if room.kind != "channel" or not room.is_public:
            raise HTTPException(403, "Bu kanal ochiq obuna uchun mavjud emas")
        if await _is_banned(db, room_id, user.id):
            raise HTTPException(403, "Siz bu kanaldan chiqarilgansiz")
        if not await _get_member(db, room_id, user.id):
            await _add_member(db, room_id, user.id, "member")
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
        return await serialize_room(db, room, user.id)


@router.post("/rooms/{room_id}/members")
async def invite_room_member(room_id: int, body: InviteIn, authorization: str = Header(None)):
    if body.role not in {"member", "admin"}:
        raise HTTPException(400, "A'zo roli noto'g'ri")
    username = body.username.strip().lower().lstrip("@")
    if not core.USERNAME_RE.fullmatch(username):
        raise HTTPException(400, "Username noto'g'ri")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room = await _room(db, room_id)
        role = await _membership(db, room_id, user.id)
        if not member_rights(role)["add_members"]:
            raise HTTPException(403, "A'zo qo'shish huquqingiz yo'q")
        if body.role == "admin":
            raise HTTPException(400, "Adminlikni a'zo qo'shilgandan keyin tayinlang")
        invited = (await db.execute(select(core.User).where(core.User.username == username))).scalar_one_or_none()
        if not invited:
            raise HTTPException(404, "Foydalanuvchi topilmadi")
        state, invite = await _invite_or_add(db, room, user, invited, body.role)
        errors = {"blocked": (403, "Bu foydalanuvchini taklif qilib bo'lmaydi"),
                  "banned": (403, "Bu foydalanuvchi chiqarilgan. Avval «Chiqarilganlar» ro'yxatidan blokdan chiqaring"),
                  "full": (400, f"Guruhda {MAX_GROUP_MEMBERS} tadan ko'p a'zo bo'lishi mumkin emas"),
                  "declined": (409, "Foydalanuvchi bu guruh taklifini rad etgan"),
                  "limit": (429, "Foydalanuvchida juda ko'p kutilayotgan takliflar bor"),
                  "ratelimit": (429, "Soatiga ko'pi bilan 60 ta taklif yuborish mumkin")}
        if state in errors:
            await db.rollback()
            raise HTTPException(*errors[state])
        await db.commit()
        if state == "added":
            await _broadcast_room_update(db, room)
        elif state == "invited" and invite:
            await core.hub.send(invited.id, _invite_event(invite, room, user))
        result = await serialize_room(db, room, user.id)
        result["invite_state"] = state
        return result


@router.get("/rooms/{room_id}/members")
async def room_members(room_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        await _membership(db, room_id, user.id)
        members = (await db.execute(
            select(RoomMember, core.User)
            .join(core.User, core.User.id == RoomMember.user_id)
            .where(RoomMember.room_id == room_id)
            .order_by(RoomMember.role, core.User.display_name)
        )).all()
        return [{
            "id": member.user_id,
            "username": account.username,
            "display_name": account.display_name,
            "avatar_url": account.avatar_url,
            "is_owner": bool(account.is_owner),
            "role": member.role,
            "rights": member_rights(member) if member.role == "admin" else None,
            "promoted_by": member.promoted_by,
            "tag": member.tag or "",
            "joined_at": member.joined_at,
        } for member, account in members]


@router.patch("/rooms/{room_id}")
async def update_room(room_id: int, body: RoomUpdateIn, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room = await _room(db, room_id)
        member = await _membership(db, room_id, user.id)
        if not member_rights(member)["change_info"]:
            raise HTTPException(403, "Ma'lumotni o'zgartirish huquqingiz yo'q")
        if body.title is not None:
            title = body.title.strip()
            if not title or len(title) > 80 or any(ord(char) < 32 for char in title):
                raise HTTPException(400, "Nom 1–80 belgidan iborat bo'lsin")
            room.title = title
        if body.description is not None:
            desc = body.description.strip()
            if len(desc) > 255 or any(ord(c) < 32 and c not in "\n\t" for c in desc):
                raise HTTPException(400, "Tavsif 255 belgigacha bo'lsin")
            room.description = desc
        if body.sign_messages is not None:
            room.sign_messages = bool(body.sign_messages)
        if body.reactions_enabled is not None:
            room.reactions_enabled = bool(body.reactions_enabled)
        if body.is_public is not None and room.kind == "channel":
            room.is_public = bool(body.is_public)
        await db.commit()
        await _broadcast_room_update(db, room)
        return await serialize_room(db, room, user.id)


@router.delete("/rooms/{room_id}")
async def delete_room(room_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room = await _room(db, room_id)
        member = await _membership(db, room_id, user.id)
        if member.role != "owner":
            raise HTTPException(403, "Suhbatni faqat egasi o'chira oladi")
        member_ids = (await db.execute(select(RoomMember.user_id).where(RoomMember.room_id == room_id))).scalars().all()
        attachments = (await db.execute(select(RoomMessage.attach).where(
            RoomMessage.room_id == room_id, RoomMessage.attach.is_not(None)))).scalars().all()
        message_ids = select(RoomMessage.id).where(RoomMessage.room_id == room_id)
        await db.execute(delete(Reaction).where(Reaction.room_message_id.in_(message_ids)))
        await db.execute(delete(RoomMessage).where(RoomMessage.room_id == room_id))
        await db.execute(delete(RoomInvite).where(RoomInvite.room_id == room_id))
        await db.execute(delete(RoomMember).where(RoomMember.room_id == room_id))
        await db.delete(room)
        await db.commit()
        if attachments and core.hooks.release_attachments:
            await core.hooks.release_attachments(db, attachments)
    event = {"type": "room_removed", "room_id": room_id, "reason": "deleted"}
    await core.hub.broadcast([(uid, event) for uid in member_ids])
    return {"ok": True}


@router.post("/rooms/{room_id}/leave")
async def leave_room(room_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room = await _room(db, room_id)
        membership = await _membership(db, room_id, user.id)
        if membership.role == "owner":
            raise HTTPException(409, "Egasi chiqishidan oldin egalikni boshqa a'zoga topshirishi yoki suhbatni o'chirishi kerak")
        await db.delete(membership)
        await db.commit()
        await _broadcast_room_update(db, room)
        return {"ok": True}


@router.delete("/rooms/{room_id}/members/{member_id}")
async def remove_member(room_id: int, member_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room = await _room(db, room_id)
        me = await _membership(db, room_id, user.id)
        target = await _get_member(db, room_id, member_id)
        if not target:
            raise HTTPException(404, "A'zo topilmadi")
        if member_id == user.id:
            raise HTTPException(400, "O'zingizni chiqarib bo'lmaydi — «chiqish» tugmasidan foydalaning")
        if target.role == "owner":
            raise HTTPException(403, "Egasini chiqarib bo'lmaydi")
        if not member_rights(me)["ban_users"]:
            raise HTTPException(403, "A'zolarni chiqarish huquqingiz yo'q")
        if target.role == "admin" and me.role != "owner" and target.promoted_by != me.user_id:
            raise HTTPException(403, "Bu adminni faqat egasi yoki uni tayinlagan admin chiqara oladi")
        await db.delete(target)
        if not await _is_banned(db, room_id, member_id):
            db.add(RoomBan(room_id=room_id, user_id=member_id, banned_by=user.id, created_at=time.time()))
        await db.commit()
        await _broadcast_room_update(db, room)
    await core.hub.send(member_id, {"type": "room_removed", "room_id": room_id, "reason": "removed"})
    return {"ok": True}


@router.post("/rooms/{room_id}/members/{member_id}/role")
async def set_member_role(room_id: int, member_id: int, body: RoleIn, authorization: str = Header(None)):
    """Eski usul (standart huquqlar bilan). Yangisi: PUT /members/{id}/admin."""
    if body.role not in {"admin", "member"}:
        raise HTTPException(400, "Rol noto'g'ri")
    if body.role == "admin":
        return await put_admin(room_id, member_id, AdminIn(rights=dict(DEFAULT_ADMIN_RIGHTS)), authorization)
    return await remove_admin(room_id, member_id, authorization)


class AdminIn(BaseModel):
    rights: dict


async def _admin_guard(db, room_id, user, member_id):
    """Admin tayinlash/olish uchun umumiy tekshiruv. Qaytaradi: (room, me, target)."""
    room = await _room(db, room_id)
    me = await _membership(db, room_id, user.id)
    if not member_rights(me)["add_admins"]:
        raise HTTPException(403, "Adminlarni boshqarish huquqingiz yo'q")
    target = await _get_member(db, room_id, member_id)
    if not target:
        raise HTTPException(404, "A'zo topilmadi")
    if target.role == "owner" or member_id == user.id:
        raise HTTPException(400, "Egasining yoki o'zingizning rolingizni o'zgartirib bo'lmaydi")
    if target.role == "admin" and me.role != "owner" and target.promoted_by != me.user_id:
        raise HTTPException(403, "Bu adminni faqat egasi yoki uni tayinlagan admin o'zgartira oladi")
    return room, me, target


@router.put("/rooms/{room_id}/members/{member_id}/admin")
async def put_admin(room_id: int, member_id: int, body: AdminIn, authorization: str = Header(None)):
    rights = clean_rights(body.rights)
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room, me, target = await _admin_guard(db, room_id, user, member_id)
        mine = member_rights(me)
        for key, value in rights.items():
            if value and not mine[key]:
                raise HTTPException(403, "O'zingizda yo'q huquqni bera olmaysiz")
        was_member = target.role != "admin"
        target.role = "admin"
        target.rights = json.dumps(rights)
        if was_member:
            target.promoted_by = user.id
        await db.commit()
        await _broadcast_room_update(db, room)
        return {"ok": True, "rights": rights}


@router.delete("/rooms/{room_id}/members/{member_id}/admin")
async def remove_admin(room_id: int, member_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room, me, target = await _admin_guard(db, room_id, user, member_id)
        target.role, target.rights, target.promoted_by = "member", None, None
        await db.commit()
        await _broadcast_room_update(db, room)
        return {"ok": True}


# ------------------------------------------------------------------ chiqarilganlar
@router.get("/rooms/{room_id}/bans")
async def list_bans(room_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        me = await _membership(db, room_id, user.id)
        if not member_rights(me)["ban_users"]:
            raise HTTPException(403, "Huquqingiz yo'q")
        rows = (await db.execute(select(RoomBan, core.User).join(core.User, core.User.id == RoomBan.user_id)
                                 .where(RoomBan.room_id == room_id).order_by(RoomBan.created_at.desc()))).all()
        return [{"user": core.ser_user(u), "created_at": ban.created_at} for ban, u in rows]


@router.delete("/rooms/{room_id}/bans/{user_id}")
async def unban(room_id: int, user_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        me = await _membership(db, room_id, user.id)
        if not member_rights(me)["ban_users"]:
            raise HTTPException(403, "Huquqingiz yo'q")
        await db.execute(delete(RoomBan).where(RoomBan.room_id == room_id, RoomBan.user_id == user_id))
        await db.commit()
        return {"ok": True}


# ------------------------------------------------------------------ guruh/kanal rasmi
@router.get("/room-avatar/{room_id}")
async def get_room_avatar(room_id: int):
    async with core.SessionLocal() as db:
        row = await db.get(RoomAvatar, room_id)
    if not row:
        raise HTTPException(404, "Rasm topilmadi")
    return Response(content=row.data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})


@router.post("/rooms/{room_id}/avatar")
async def set_room_avatar(room_id: int, request: Request, authorization: str = Header(None)):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_AVATAR_BYTES):
        raise HTTPException(413, "Rasm 2 MB dan kichik bo'lishi kerak")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        me = await _membership(db, room_id, user.id)
        if not member_rights(me)["change_info"]:
            raise HTTPException(403, "Ma'lumotni o'zgartirish huquqingiz yo'q")
    if core.limited(f"roomavatar:{user.id}", 20, 3600):
        raise HTTPException(429, "Juda ko'p urinish")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_AVATAR_BYTES:
            raise HTTPException(413, "Rasm 2 MB dan kichik bo'lishi kerak")
    if not chunks:
        raise HTTPException(400, "Rasm tanlanmadi")
    jpeg = await asyncio.to_thread(_process_avatar, bytes(chunks), request.headers.get("content-type", "").split(";", 1)[0].lower())
    async with core.SessionLocal() as db:
        room = await _room(db, room_id)
        row = await db.get(RoomAvatar, room_id)
        if row:
            row.data, row.updated_at = jpeg, time.time()
        else:
            db.add(RoomAvatar(room_id=room_id, data=jpeg, updated_at=time.time()))
        room.avatar_url = f"/api/room-avatar/{room_id}?v={int(time.time() * 1000)}"
        await db.commit()
        await _broadcast_room_update(db, room)
        return await serialize_room(db, room, user.id)


@router.delete("/rooms/{room_id}/avatar")
async def delete_room_avatar(room_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        me = await _membership(db, room_id, user.id)
        if not member_rights(me)["change_info"]:
            raise HTTPException(403, "Ma'lumotni o'zgartirish huquqingiz yo'q")
        room = await _room(db, room_id)
        await db.execute(delete(RoomAvatar).where(RoomAvatar.room_id == room_id))
        room.avatar_url = None
        await db.commit()
        await _broadcast_room_update(db, room)
        return await serialize_room(db, room, user.id)


# ------------------------------------------------------------------ taklif havolasi
@router.get("/rooms/{room_id}/invite-link")
async def get_invite_link(room_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        me = await _membership(db, room_id, user.id)
        if not member_rights(me)["add_members"]:
            raise HTTPException(403, "Huquqingiz yo'q")
        room = await _room(db, room_id)
        if not room.invite_code:
            room.invite_code = secrets.token_urlsafe(9)
            await db.commit()
        return {"code": room.invite_code}


@router.post("/rooms/{room_id}/invite-link")
async def reset_invite_link(room_id: int, authorization: str = Header(None)):
    """Eski havola ishlamay qoladi, yangisi yaratiladi."""
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        me = await _membership(db, room_id, user.id)
        if not member_rights(me)["add_members"]:
            raise HTTPException(403, "Huquqingiz yo'q")
        room = await _room(db, room_id)
        room.invite_code = secrets.token_urlsafe(9)
        await db.commit()
        return {"code": room.invite_code}


@router.post("/join/{code}")
async def join_by_link(code: str, authorization: str = Header(None)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,32}", code):
        raise HTTPException(404, "Havola yaroqsiz")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if core.limited(f"joinlink:{user.id}", 30, 3600):
            raise HTTPException(429, "Juda ko'p urinish")
        room = (await db.execute(select(Room).where(Room.invite_code == code))).scalar_one_or_none()
        if not room:
            raise HTTPException(404, "Havola yaroqsiz yoki bekor qilingan")
        if await _is_banned(db, room.id, user.id):
            raise HTTPException(403, "Siz bu suhbatdan chiqarilgansiz")
        if not await _get_member(db, room.id, user.id):
            if room.kind == "group" and await _member_count(db, room.id) >= MAX_GROUP_MEMBERS:
                raise HTTPException(409, "Guruh to'lgan")
            await _add_member(db, room.id, user.id, "member")
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
            await _broadcast_room_update(db, room)
        return await serialize_room(db, room, user.id)


@router.post("/rooms/{room_id}/transfer")
async def transfer_ownership(room_id: int, body: TransferIn, authorization: str = Header(None)):
    username = body.username.strip().lower().lstrip("@")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room = await _room(db, room_id)
        me = await _membership(db, room_id, user.id)
        if me.role != "owner":
            raise HTTPException(403, "Egalikni faqat joriy egasi topshira oladi")
        target_user = (await db.execute(select(core.User).where(core.User.username == username))).scalar_one_or_none()
        target = await _get_member(db, room_id, target_user.id) if target_user else None
        if not target or target.user_id == user.id:
            raise HTTPException(404, "Suhbat a'zosi topilmadi")
        target.role, me.role = "owner", "admin"
        await db.commit()
        await _broadcast_room_update(db, room)
        return {"ok": True}


@router.post("/rooms/{room_id}/read")
async def mark_room_read(room_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        await _membership(db, room_id, user.id)
    await handle_room_read(user.id, {"room_id": room_id})
    return {"ok": True}


# ------------------------------------------------------------------ takliflar
@router.get("/invites")
async def list_invites(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        rows = (await db.execute(
            select(RoomInvite, Room, core.User)
            .join(Room, Room.id == RoomInvite.room_id)
            .join(core.User, core.User.id == RoomInvite.invited_by)
            .where(RoomInvite.user_id == user.id, RoomInvite.status == "pending")
            .order_by(RoomInvite.created_at.desc()))).all()
        return [{"id": invite.id, "room_id": room.id, "title": room.title, "kind": room.kind,
                 "invited_by": inviter.display_name, "invited_by_username": inviter.username,
                 "created_at": invite.created_at} for invite, room, inviter in rows]


@router.post("/invites/{invite_id}/accept")
async def accept_invite(invite_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        invite = await db.get(RoomInvite, invite_id)
        if not invite or invite.user_id != user.id or invite.status != "pending":
            raise HTTPException(404, "Taklif topilmadi")
        room = await db.get(Room, invite.room_id)
        if not room:
            await db.delete(invite)
            await db.commit()
            raise HTTPException(404, "Suhbat endi mavjud emas")
        if room.kind == "group" and await _member_count(db, room.id) >= MAX_GROUP_MEMBERS:
            raise HTTPException(409, "Guruh to'lgan")
        if not await _get_member(db, room.id, user.id):
            await _add_member(db, room.id, user.id, invite.role)
        await db.delete(invite)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
        await _broadcast_room_update(db, room)
        return await serialize_room(db, room, user.id)


@router.post("/invites/{invite_id}/decline")
async def decline_invite(invite_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        invite = await db.get(RoomInvite, invite_id)
        if not invite or invite.user_id != user.id or invite.status != "pending":
            raise HTTPException(404, "Taklif topilmadi")
        invite.status = "declined"  # qayta-qayta taklif qilib bezovta qilmaslik uchun yozuv saqlanadi
        await db.commit()
        return {"ok": True}


# ------------------------------------------------------------------ xabarlar
@router.get("/rooms/{room_id}/messages")
async def room_messages(room_id: int, before: int = 0, limit: int = 100, around: int = 0,
                        authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        await _membership(db, room_id, user.id)
        if around:  # qadalgan/javob berilgan xabarga sakrash: u atrofidagi oyna
            older = (await db.execute(select(RoomMessage).where(RoomMessage.room_id == room_id, RoomMessage.id <= around)
                                      .order_by(RoomMessage.id.desc()).limit(51))).scalars().all()
            newer = (await db.execute(select(RoomMessage).where(RoomMessage.room_id == room_id, RoomMessage.id > around)
                                      .order_by(RoomMessage.id.asc()).limit(51))).scalars().all()
            rows = list(reversed(older[:50])) + newer[:50]
            return {"messages": await _serialize_room_messages(db, rows, user.id),
                    "has_more": len(older) > 50, "has_newer": len(newer) > 50}
        limit = max(1, min(limit, 200))
        stmt = select(RoomMessage).where(RoomMessage.room_id == room_id)
        if before:
            stmt = stmt.where(RoomMessage.id < before)
        rows = (await db.execute(stmt.order_by(RoomMessage.id.desc()).limit(limit))).scalars().all()
        return {"messages": await _serialize_room_messages(db, list(reversed(rows)), user.id),
                "has_more": len(rows) == limit, "has_newer": False}


class ReactionIn(BaseModel):
    emoji: str


class MessageEditIn(BaseModel):
    text: str


ALLOWED_REACTIONS = {"👍", "❤️", "😂", "😮", "😢", "🔥", "👏", "✅"}


@router.post("/chats/{chat_id}/messages/{message_id}/reactions")
async def react_direct(chat_id: int, message_id: int, body: ReactionIn, authorization: str = Header(None)):
    return await _react(chat_id, message_id, body, authorization, False)


@router.post("/rooms/{room_id}/messages/{message_id}/reactions")
async def react_room(room_id: int, message_id: int, body: ReactionIn, authorization: str = Header(None)):
    return await _react(room_id, message_id, body, authorization, True)


async def _toggle_reaction(db, user_id, emoji, **target):
    query = select(Reaction).where(Reaction.user_id == user_id, Reaction.emoji == emoji,
                                   *[getattr(Reaction, key) == value for key, value in target.items()])
    reaction = (await db.execute(query)).scalar_one_or_none()
    if reaction:
        await db.delete(reaction)
    else:
        db.add(Reaction(user_id=user_id, emoji=emoji, **target))
    try:
        await db.commit()
    except IntegrityError:  # ikki marta tez bosish — ikkinchisi e'tiborga olinmaydi
        await db.rollback()


async def _react(parent_id, message_id, body, authorization, is_room):
    if body.emoji not in ALLOWED_REACTIONS:
        raise HTTPException(400, "Emoji qo'llab-quvvatlanmaydi")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if core.limited(f"react:{user.id}", 120, 60):
            raise HTTPException(429, "Juda tez bosyapsiz")
        if is_room:
            await _membership(db, parent_id, user.id)
            msg = await db.get(RoomMessage, message_id)
            if not msg or msg.room_id != parent_id or msg.deleted:
                raise HTTPException(404, "Xabar topilmadi")
            if (await _room(db, parent_id)).reactions_enabled is False:
                raise HTTPException(403, "Bu suhbatda reaksiyalar o'chirilgan")
            await _toggle_reaction(db, user.id, body.emoji, room_message_id=message_id)
            rows = (await _reactions_bulk(db, room=[message_id]))[message_id]
            member_ids = (await db.execute(select(RoomMember.user_id).where(RoomMember.room_id == parent_id))).scalars().all()
            await core.hub.broadcast([(mid, {"type": "reaction", "room_id": parent_id, "message_id": message_id,
                                             "reactions": _summary(rows, mid)}) for mid in member_ids])
        else:
            chat = await core.member_chat(db, parent_id, user.id)
            if chat.status == "blocked":
                raise HTTPException(403, "Bu chat bloklangan")
            msg = await db.get(core.Message, message_id)
            if not msg or msg.chat_id != parent_id or msg.deleted:
                raise HTTPException(404, "Xabar topilmadi")
            await _toggle_reaction(db, user.id, body.emoji, message_id=message_id)
            rows = (await _reactions_bulk(db, direct=[message_id]))[message_id]
            peer = chat.user_b if chat.user_a == user.id else chat.user_a
            await core.hub.broadcast([(mid, {"type": "reaction", "chat_id": parent_id, "message_id": message_id,
                                             "reactions": _summary(rows, mid)}) for mid in (user.id, peer)])
        return {"reactions": _summary(rows, user.id)}


@router.patch("/chats/{chat_id}/messages/{message_id}")
async def edit_direct(chat_id: int, message_id: int, body: MessageEditIn, authorization: str = Header(None)):
    return await _edit_delete(chat_id, message_id, body.text, authorization, False, False)


@router.delete("/chats/{chat_id}/messages/{message_id}")
async def delete_direct(chat_id: int, message_id: int, authorization: str = Header(None)):
    return await _edit_delete(chat_id, message_id, None, authorization, False, True)


@router.patch("/rooms/{room_id}/messages/{message_id}")
async def edit_room(room_id: int, message_id: int, body: MessageEditIn, authorization: str = Header(None)):
    return await _edit_delete(room_id, message_id, body.text, authorization, True, False)


@router.delete("/rooms/{room_id}/messages/{message_id}")
async def delete_room_message(room_id: int, message_id: int, authorization: str = Header(None)):
    return await _edit_delete(room_id, message_id, None, authorization, True, True)


def _check_edit_text(text, has_attachment):
    text = text.strip()
    if len(text) > 4000 or (not text and not has_attachment):
        raise HTTPException(400, "Xabar bo'sh yoki 4000 belgidan uzun")
    return text


async def _edit_delete(parent_id, message_id, text, authorization, is_room, deleting):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if core.limited(f"edit:{user.id}", 60, 60):
            raise HTTPException(429, "Juda tez o'zgartiryapsiz")
        if is_room:
            room = await _room(db, parent_id)
            member = await _membership(db, parent_id, user.id)
            msg = await db.get(RoomMessage, message_id)
            if not msg or msg.room_id != parent_id or msg.deleted:
                raise HTTPException(404, "Xabar topilmadi")
            rights = member_rights(member)
            if msg.sender_id != user.id and not (deleting and rights["delete_messages"]):
                raise HTTPException(403, "Faqat o'z xabaringizni tahrirlash mumkin")
            if room.kind == "channel" and not rights["post_messages"] and not (deleting and rights["delete_messages"]):
                raise HTTPException(403, "Faqat kanal admini xabarni boshqarishi mumkin")
            old_attach = msg.attach
            if deleting:
                msg.deleted = True
                msg.text = ""
                msg.attach = None
                if room.pinned_id == msg.id:
                    room.pinned_id = None
                await db.execute(delete(Reaction).where(Reaction.room_message_id == msg.id))
            else:
                msg.text = _check_edit_text(text, bool(msg.attach))
                msg.edited_at = time.time()
            await db.commit()
            if deleting and old_attach and core.hooks.release_attachments:
                await core.hooks.release_attachments(db, [old_attach])
            sender = await db.get(core.User, msg.sender_id)
            base = _room_msg_base(msg, sender, (await _tags_for(db, parent_id, [msg.sender_id])).get(msg.sender_id, ""))
            base["reply"] = (await _reply_previews(db, RoomMessage, [msg.reply_to_id])).get(msg.reply_to_id)
            rows = (await _reactions_bulk(db, room=[msg.id]))[msg.id]
            member_ids = (await db.execute(select(RoomMember.user_id).where(RoomMember.room_id == parent_id))).scalars().all()
            await core.hub.broadcast([(mid, {"type": "room_message_update", "room_id": parent_id,
                                             "message": dict(base, reactions=_summary(rows, mid))})
                                      for mid in member_ids])
            if deleting:
                await _broadcast_room_update(db, room)
            return dict(base, reactions=_summary(rows, user.id))
        chat = await core.member_chat(db, parent_id, user.id)
        if chat.status == "blocked":
            raise HTTPException(403, "Bu chat bloklangan")
        msg = await db.get(core.Message, message_id)
        if not msg or msg.chat_id != parent_id or msg.deleted:
            raise HTTPException(404, "Xabar topilmadi")
        if msg.sender_id != user.id:
            raise HTTPException(403, "Faqat o'z xabaringizni tahrirlash mumkin")
        old_attach = msg.attach
        if deleting:
            msg.deleted = True
            msg.text = ""
            msg.attach = None
            if chat.pinned_id == msg.id:
                chat.pinned_id = None
            await db.execute(delete(Reaction).where(Reaction.message_id == msg.id))
        else:
            msg.text = _check_edit_text(text, bool(msg.attach))
            msg.edited_at = time.time()
        await db.commit()
        if deleting and old_attach and core.hooks.release_attachments:
            await core.hooks.release_attachments(db, [old_attach])
        base = core.ser_msg(msg)
        base["reply"] = (await _reply_previews(db, core.Message, [msg.reply_to_id])).get(msg.reply_to_id)
        rows = (await _reactions_bulk(db, direct=[msg.id]))[msg.id]
        peer = chat.user_b if chat.user_a == user.id else chat.user_a
        await core.hub.broadcast([(mid, {"type": "message_update", "chat_id": parent_id,
                                         "message": dict(base, reactions=_summary(rows, mid))})
                                  for mid in (user.id, peer)])
        if deleting:
            await core.hub.broadcast([(uid, {"type": "chat_update", "chat": await core.ser_chat(db, chat, uid)})
                                      for uid in (user.id, peer)])
        return dict(base, reactions=_summary(rows, user.id))


def _like_escape(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/search")
async def search_messages(q: str = "", authorization: str = Header(None)):
    q = q.strip()
    if len(q) < 2 or len(q) > 80:
        return []
    pattern = f"%{_like_escape(q)}%"
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        direct_ids = (await db.execute(select(core.Chat.id).where(
            or_(core.Chat.user_a == user.id, core.Chat.user_b == user.id), core.Chat.status != "blocked"))).scalars().all()
        room_ids = (await db.execute(select(RoomMember.room_id).where(RoomMember.user_id == user.id))).scalars().all()
        result = []
        if direct_ids:
            rows = (await db.execute(select(core.Message).where(core.Message.chat_id.in_(direct_ids),
                                     core.Message.deleted.is_(False), core.Message.text.ilike(pattern, escape="\\"))
                                     .order_by(core.Message.id.desc()).limit(50))).scalars().all()
            chats = {c.id: c for c in (await db.execute(select(core.Chat).where(
                core.Chat.id.in_({m.chat_id for m in rows})))).scalars().all()} if rows else {}
            peers = await _users_bulk(db, {(c.user_b if c.user_a == user.id else c.user_a) for c in chats.values()})
            for msg in rows:
                chat = chats[msg.chat_id]
                peer = peers.get(chat.user_b if chat.user_a == user.id else chat.user_a)
                result.append({"kind": "direct", "id": chat.id, "title": peer.display_name if peer else "",
                               "sender_id": msg.sender_id, "text": msg.text, "message_id": msg.id,
                               "created_at": msg.created_at})
        if room_ids and len(result) < 50:
            rows = (await db.execute(select(RoomMessage).where(RoomMessage.room_id.in_(room_ids),
                                     RoomMessage.deleted.is_(False), RoomMessage.text.ilike(pattern, escape="\\"))
                                     .order_by(RoomMessage.id.desc()).limit(50 - len(result)))).scalars().all()
            rooms = {r.id: r for r in (await db.execute(select(Room).where(
                Room.id.in_({m.room_id for m in rows})))).scalars().all()} if rows else {}
            for msg in rows:
                room = rooms[msg.room_id]
                result.append({"kind": "room", "id": room.id, "title": room.title,
                               "sender_id": msg.sender_id, "text": msg.text, "message_id": msg.id,
                               "created_at": msg.created_at})
        return sorted(result, key=lambda item: item["created_at"], reverse=True)[:50]


# ------------------------------------------------------------------ WebSocket buyruqlari
async def post_room(user_id, room_id, text, attach_json=None, attach_key=None, fwd=None, reply_to=None, temp_id=None):
    """Guruh/kanalga xabar yozadi va tarqatadi (WS va yo'naltirish shu funksiyadan foydalanadi)."""
    text = (text or "").strip()
    if len(text) > 4000:
        raise core.SendError("invalid", "Xabar juda uzun")
    if not text and not attach_json and not attach_key:
        raise core.SendError("invalid", "Xabar bo'sh")
    async with core.SessionLocal() as db:
        room = await db.get(Room, room_id)
        member = await _get_member(db, room_id, user_id) if room else None
        if not room or not member:
            raise core.SendError("forbidden", "Siz bu suhbat a'zosi emassiz")
        if room.kind == "channel" and not member_rights(member)["post_messages"]:
            raise core.SendError("forbidden", "Kanalda faqat post yozish huquqi bor adminlar yoza oladi")
        if attach_key:
            if not core.hooks.resolve_attachment:
                raise core.SendError("invalid", "Fayl yuborish yoqilmagan")
            try:
                attach_json = await core.hooks.resolve_attachment(db, user_id, attach_key)
            except ValueError as exc:
                raise core.SendError("invalid", str(exc))
        reply_id = None
        if reply_to is not None:
            target = await db.get(RoomMessage, reply_to)
            if not target or target.room_id != room_id or target.deleted:
                raise core.SendError("invalid", "Javob beriladigan xabar topilmadi")
            reply_id = target.id
        msg = RoomMessage(room_id=room_id, sender_id=user_id, text=text, created_at=time.time(),
                          attach=attach_json, fwd=fwd, reply_to_id=reply_id)
        db.add(msg)
        await db.commit()
        sender = await db.get(core.User, user_id)
        base = (await _serialize_room_messages(db, [msg], user_id))[0]
        payloads = await _room_payloads(db, room)  # a'zolar soniga bog'liq bo'lmagan ~4 ta so'rov
        mentioned = set()
        names = {name.lower() for name in MENTION_RE.findall(text)}
        if names:
            mentioned = set((await db.execute(
                select(core.User.id).join(RoomMember, RoomMember.user_id == core.User.id).where(
                    RoomMember.room_id == room_id, core.User.username.in_(names),
                    core.User.id != user_id))).scalars().all())
    await core.hub.broadcast([
        (uid, {"type": "room_message", "message": base, "room": payload,
               "temp_id": temp_id if uid == user_id else None})
        for uid, payload in payloads.items()])
    if core.hooks.on_message:
        recipients = [uid for uid in payloads if uid != user_id]
        core.spawn(core.hooks.on_message("room", sender, recipients, f"r:{room_id}", base, room, mentioned))
    return base


async def handle_room_send(user_id, data):
    room_id, text = data.get("room_id"), data.get("text", "")
    temp_id, key, reply = data.get("temp_id"), data.get("attachment"), data.get("reply_to")

    async def fail(code, message):
        await core.hub.send(user_id, {"type": "error", "code": code, "temp_id": temp_id, "message": message})

    def is_int(value):
        return isinstance(value, int) and not isinstance(value, bool)

    if not is_int(room_id) or not isinstance(text, str) or (key is not None and not isinstance(key, str)) \
            or (reply is not None and not is_int(reply)):
        return await fail("invalid", "Noto'g'ri so'rov")
    try:
        await post_room(user_id, room_id, text, attach_key=key, reply_to=reply, temp_id=temp_id)
    except core.SendError as exc:
        await fail(exc.code, exc.message)


async def handle_room_typing(user_id, data):
    room_id = data.get("room_id")
    if not isinstance(room_id, int) or isinstance(room_id, bool):
        return
    async with core.SessionLocal() as db:
        if not await _get_member(db, room_id, user_id):
            return
        recipients = (await db.execute(select(RoomMember.user_id).where(
            RoomMember.room_id == room_id, RoomMember.user_id != user_id))).scalars().all()
    event = {"type": "room_typing", "room_id": room_id, "user_id": user_id}
    await core.hub.broadcast([(rid, event) for rid in recipients])


async def handle_room_read(user_id, data):
    room_id = data.get("room_id")
    if not isinstance(room_id, int) or isinstance(room_id, bool):
        return
    async with core.SessionLocal() as db:
        member = await _get_member(db, room_id, user_id)
        room = await db.get(Room, room_id) if member else None
        if not member or not room:
            return
        latest = (await db.execute(select(func.max(RoomMessage.id)).where(RoomMessage.room_id == room_id))).scalar_one() or 0
        if latest <= member.last_read_id:
            return
        member.last_read_id = latest
        await db.commit()
        payload = (await _room_payloads(db, room, [user_id], unread={user_id: 0}))[user_id]
        others = []
        if room.kind == "group":
            others = (await db.execute(select(RoomMember.user_id).where(
                RoomMember.room_id == room_id, RoomMember.user_id != user_id))).scalars().all()
    await core.hub.send(user_id, {"type": "room_update", "room": payload})
    if others:  # guruhda ✓✓ yangilansin
        event = {"type": "room_read_by", "room_id": room_id, "user_id": user_id, "last_read_id": latest}
        await core.hub.broadcast([(uid, event) for uid in others])


core.app.include_router(router)
core.hooks.migrate = migrate_feature_schema
core.hooks.serialize_direct_messages = serialize_direct_messages
core.hooks.handle_room_send = handle_room_send
core.hooks.handle_room_typing = handle_room_typing
core.hooks.handle_room_read = handle_room_read