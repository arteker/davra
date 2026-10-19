"""Davra qo'shimchalari: rasm/fayl/ovozli xabar, Web Push, sozlamalar, maxfiylik, seanslar,
bloklanganlar, saqlangan xabarlar, yo'naltirish (forward), xabarni qadash, a'zo yorliqlari."""
import asyncio
import io
import json
import logging
import os
import re
import secrets
import time
from urllib.parse import quote, unquote

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, LargeBinary, String, Text, delete, func, select
from sqlalchemy.exc import IntegrityError

import features as feat
import main as core

log = logging.getLogger("davra")
Base = core.Base
router = APIRouter(prefix="/api")

MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "8")) * 1024 * 1024
MAX_VOICE = 4 * 1024 * 1024
IMAGE_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
VOICE_MIMES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/aac", "audio/x-m4a"}
MIME_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")

VAPID_PUBLIC = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE = os.getenv("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:admin@davra.app").strip()


# ------------------------------------------------------------------ modellar
class Attachment(Base):
    """Rasm / fayl / ovozli xabar — bazada saqlanadi (Render'da disk kerak emas).
    Fayl `key` (taxmin qilib bo'lmaydigan kalit) orqali ochiladi; kalit faqat xabar ishtirokchilariga yetadi."""
    __tablename__ = "attachments"
    id = Column(Integer, primary_key=True)
    key = Column(String(32), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    kind = Column(String(8), nullable=False)  # image | file | voice
    name = Column(String(120), nullable=False)
    mime = Column(String(100), nullable=False)
    size = Column(Integer, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration = Column(Float, nullable=True)
    data = Column(LargeBinary, nullable=False)
    used = Column(Boolean, nullable=False, default=False)
    created_at = Column(Float, nullable=False)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    endpoint = Column(String(700), unique=True, nullable=False)
    p256dh = Column(String(200), nullable=False)
    auth = Column(String(100), nullable=False)
    device = Column(String(120), nullable=True)
    created_at = Column(Float, nullable=False)


class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data = Column(Text, nullable=False, default="{}")
    updated_at = Column(Float, nullable=False)


class AppConfig(Base):
    """Ilova sozlamalari (masalan, avtomatik yaratilgan VAPID kalitlari)."""
    __tablename__ = "app_config"
    key = Column(String(40), primary_key=True)
    value = Column(Text, nullable=False)


class SavedItem(Base):
    """"Saqlangan xabarlar": o'zim uchun eslatma yoki boshqa suhbatdan saqlangan xabar."""
    __tablename__ = "saved_items"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    text = Column(Text, nullable=False, default="")
    attach = Column(Text, nullable=True)
    source = Column(String(80), nullable=False, default="")
    created_at = Column(Float, nullable=False)


# ------------------------------------------------------------------ yordamchilar
def _bearer(authorization):
    return (authorization or "").removeprefix("Bearer ").strip()


def _meta(row):
    return {"key": row.key, "kind": row.kind, "name": row.name, "mime": row.mime, "size": row.size,
            "w": row.width, "h": row.height, "dur": row.duration}


def clean_name(raw, fallback="fayl"):
    name = re.sub(r"[\x00-\x1f\\/:*?\"<>|]+", "_", raw or "").strip(" .")[:100]
    return name or fallback


async def _migrate_and_cleanup():
    if _prev_migrate:
        await _prev_migrate()
    async with core.SessionLocal() as db:  # yuklangan-u yuborilmagan fayllar bir sutkadan keyin o'chadi
        await db.execute(delete(Attachment).where(Attachment.used.is_(False),
                                                  Attachment.created_at < time.time() - 86400))
        await db.commit()
    await _init_vapid()


_prev_migrate = core.hooks.migrate


def _generate_vapid():
    """Web Push uchun yangi VAPID kalit juftligini yaratadi (public, private) — base64url."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    vapid = Vapid()
    vapid.generate_keys()
    public = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    return b64(public), b64(private)


async def _load_vapid_rows(db):
    rows = (await db.execute(select(AppConfig).where(AppConfig.key.in_(("vapid_public", "vapid_private"))))).scalars().all()
    return {row.key: row.value for row in rows}


async def _init_vapid():
    """VAPID kalitlari: muhit o'zgaruvchisida bo'lsa o'sha, bo'lmasa bazadan; hech qayerda yo'q bo'lsa
    bir marta yaratilib bazada saqlanadi (kalit o'zgarsa, eski obunalar yaroqsiz bo'ladi — shuning uchun saqlanadi)."""
    global VAPID_PUBLIC, VAPID_PRIVATE
    if VAPID_PUBLIC and VAPID_PRIVATE:
        return
    async with core.SessionLocal() as db:
        stored = await _load_vapid_rows(db)
        if "vapid_public" not in stored or "vapid_private" not in stored:
            try:
                public, private = await asyncio.to_thread(_generate_vapid)
            except Exception:
                log.exception("VAPID kalitlarini yaratib bo'lmadi (pywebpush o'rnatilganmi?) — push o'chiq")
                return
            db.add(AppConfig(key="vapid_public", value=public))
            db.add(AppConfig(key="vapid_private", value=private))
            try:
                await db.commit()
            except IntegrityError:  # boshqa jarayon bir vaqtda yaratib qo'ygan
                await db.rollback()
            stored = await _load_vapid_rows(db)
    VAPID_PUBLIC, VAPID_PRIVATE = stored.get("vapid_public", ""), stored.get("vapid_private", "")


async def resolve_attachment(db, user_id, key):
    """Xabarga biriktirish uchun: fayl shu foydalanuvchiniki ekanini tekshiradi, JSON qaytaradi."""
    row = (await db.execute(select(Attachment).where(Attachment.key == key))).scalar_one_or_none()
    if not row or row.user_id != user_id:
        raise ValueError("Fayl topilmadi, qayta yuklang")
    row.used = True
    return json.dumps(_meta(row), ensure_ascii=False)


async def release_attachments(db, raws):
    """Hech bir xabar/saqlangan yozuv ishlatmayotgan fayllarni bazadan o'chiradi."""
    keys = set()
    for raw in raws:
        meta = core._j(raw)
        if isinstance(meta, dict) and meta.get("key"):
            keys.add(meta["key"])
    for key in keys:
        pattern = '%"key": "' + feat._like_escape(key) + '"%'
        refs = 0
        for model in (core.Message, feat.RoomMessage, SavedItem):
            refs += (await db.execute(select(func.count()).select_from(model).where(
                model.attach.like(pattern, escape="\\")))).scalar_one()
        if not refs:
            await db.execute(delete(Attachment).where(Attachment.key == key))
    await db.commit()


# ------------------------------------------------------------------ fayl yuklash va berish
def _process_image(data):
    """Rasmni tekshiradi, EXIF burilishini to'g'rilaydi va 1600px gacha JPEG'ga o'tkazadi."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 40_000_000
    try:
        probe = Image.open(io.BytesIO(data))
        if probe.format not in IMAGE_FORMATS.values():
            raise HTTPException(415, "Faqat JPEG, PNG yoki WebP rasm yuboring")
        if probe.width * probe.height > 40_000_000:
            raise HTTPException(400, "Rasm o'lchami juda katta")
        probe.verify()
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, (255, 255, 255))
            flat.paste(image, mask=image.split()[3])
            image = flat
        else:
            image = image.convert("RGB")
        image.thumbnail((1600, 1600), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue(), image.width, image.height
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise HTTPException(400, "Yaroqli rasm faylini tanlang")


@router.post("/upload")
async def upload(request: Request, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
    if core.limited(f"upload:{user.id}", 40, 3600):
        raise HTTPException(429, "Soatiga ko'pi bilan 40 ta fayl yuborish mumkin")
    declared = request.headers.get("content-length")
    if declared and (not declared.isdigit() or int(declared) > MAX_UPLOAD):
        raise HTTPException(413, f"Fayl {MAX_UPLOAD // 1024 // 1024} MB dan kichik bo'lishi kerak")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_UPLOAD:
            raise HTTPException(413, f"Fayl {MAX_UPLOAD // 1024 // 1024} MB dan kichik bo'lishi kerak")
    if not chunks:
        raise HTTPException(400, "Fayl tanlanmadi")
    data = bytes(chunks)
    ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    hint = request.headers.get("x-kind", "").strip().lower()
    name = clean_name(unquote(request.headers.get("x-filename", "")))
    width = height = duration = None
    if hint == "voice":
        if ctype not in VOICE_MIMES:
            raise HTTPException(415, "Ovoz formati qo'llab-quvvatlanmaydi")
        if len(data) > MAX_VOICE:
            raise HTTPException(413, "Ovozli xabar juda uzun")
        try:
            duration = max(0.0, min(float(request.headers.get("x-duration", "0")), 600.0))
        except ValueError:
            duration = 0.0
        kind, mime, name = "voice", ctype, "ovozli-xabar"
    elif ctype in IMAGE_FORMATS and hint != "file":
        data, width, height = await asyncio.to_thread(_process_image, data)
        kind, mime = "image", "image/jpeg"
    else:
        kind = "file"
        mime = ctype if MIME_RE.match(ctype) else "application/octet-stream"
    async with core.SessionLocal() as db:
        row = Attachment(key=secrets.token_urlsafe(16), user_id=user.id, kind=kind, name=name, mime=mime,
                         size=len(data), width=width, height=height, duration=duration, data=data,
                         used=False, created_at=time.time())
        db.add(row)
        await db.commit()
        return _meta(row)


@router.get("/files/{key}")
async def get_file(key: str, request: Request):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,32}", key):
        raise HTTPException(404, "Fayl topilmadi")
    async with core.SessionLocal() as db:
        row = (await db.execute(select(Attachment).where(Attachment.key == key))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Fayl topilmadi")
    data = row.data
    headers = {"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff",
               "Accept-Ranges": "bytes", "Content-Security-Policy": "sandbox"}
    if row.kind == "image":
        media = "image/jpeg"
    elif row.kind == "voice":
        media = row.mime
    else:  # boshqa fayllar hech qachon sahifa ichida ochilmaydi (XSS'dan himoya)
        media = "application/octet-stream"
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(row.name)
    total = len(data)
    status, start, end = 200, 0, total - 1
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", request.headers.get("range", "").strip())
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = min(int(match.group(2)), total - 1) if match.group(2) else total - 1
        else:
            start = max(0, total - int(match.group(2)))
        if start > end or start >= total:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return Response(content=data[start:end + 1], status_code=status, media_type=media, headers=headers)


# ------------------------------------------------------------------ sozlamalar (serverda saqlanadi)
BG_PRESETS = {"pattern", "plain", "blue", "green", "violet", "sunset"}
FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
CHAT_KEY_RE = re.compile(r"^(r:)?\d{1,9}$")


def sanitize_settings(raw):
    if not isinstance(raw, dict):
        raise ValueError("Sozlamalar noto'g'ri")
    out = {}
    notif = raw.get("notif")
    if isinstance(notif, dict):
        out["notif"] = {k: bool(notif[k]) for k in ("direct", "groups", "channels", "preview", "sound") if k in notif}
    muted = raw.get("muted")
    if isinstance(muted, list):
        out["muted"] = [m for m in muted if isinstance(m, str) and CHAT_KEY_RE.match(m)][:300]
    chat = raw.get("chat")
    if isinstance(chat, dict):
        cleaned = {}
        size = chat.get("textSize")
        if isinstance(size, int) and not isinstance(size, bool) and 12 <= size <= 22:
            cleaned["textSize"] = size
        if chat.get("bg") in BG_PRESETS:
            cleaned["bg"] = chat["bg"]
        for key in ("enterSend", "hour12"):
            if key in chat:
                cleaned[key] = bool(chat[key])
        out["chat"] = cleaned
    folders = raw.get("folders")
    if isinstance(folders, list):
        items = []
        for folder in folders[:20]:
            if not isinstance(folder, dict):
                continue
            fid, name, chats = folder.get("id"), folder.get("name"), folder.get("chats")
            if not (isinstance(fid, str) and FOLDER_ID_RE.match(fid) and isinstance(name, str) and isinstance(chats, list)):
                continue
            name = " ".join(name.split())[:24]
            if name:
                items.append({"id": fid, "name": name,
                              "chats": [c for c in chats if isinstance(c, str) and CHAT_KEY_RE.match(c)][:300]})
        out["folders"] = items
    advanced = raw.get("advanced")
    if isinstance(advanced, dict):
        cleaned = {}
        if "reduceMotion" in advanced:
            cleaned["reduceMotion"] = bool(advanced["reduceMotion"])
        scale = advanced.get("scale")
        if isinstance(scale, int) and not isinstance(scale, bool) and 90 <= scale <= 130:
            cleaned["scale"] = scale
        out["advanced"] = cleaned
    return out


class SettingsIn(BaseModel):
    data: dict


@router.get("/settings")
async def get_settings(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        row = await db.get(UserSettings, user.id)
        return {"data": core._j(row.data) or {} if row else {}}


@router.put("/settings")
async def put_settings(body: SettingsIn, authorization: str = Header(None)):
    try:
        cleaned = sanitize_settings(body.data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    text = json.dumps(cleaned, ensure_ascii=False)
    if len(text) > 30_000:
        raise HTTPException(413, "Sozlamalar juda katta")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if core.limited(f"settings:{user.id}", 120, 600):
            raise HTTPException(429, "Juda tez saqlayapsiz")
        row = await db.get(UserSettings, user.id)
        if row:
            row.data, row.updated_at = text, time.time()
        else:
            db.add(UserSettings(user_id=user.id, data=text, updated_at=time.time()))
        await db.commit()
    return {"data": cleaned}


# ------------------------------------------------------------------ maxfiylik, parol, seanslar
class PrivacyIn(BaseModel):
    hide_seen: bool
    allow_requests: bool


@router.put("/privacy")
async def put_privacy(body: PrivacyIn, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        changed = bool(user.hide_seen) != body.hide_seen
        user.hide_seen, user.allow_requests = body.hide_seen, body.allow_requests
        await db.commit()
        if changed:
            await feat._broadcast_profile(db, user)
        return core.ser_user(user)


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


@router.post("/password")
async def change_password(body: PasswordIn, authorization: str = Header(None)):
    if not (8 <= len(body.new_password) <= 128):
        raise HTTPException(400, "Yangi parol kamida 8 ta belgidan iborat bo'lsin")
    token_hash = core.tok_hash(_bearer(authorization))
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if core.limited(f"pwchange:{user.id}", 6, 600):
            raise HTTPException(429, "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring")
        if not await asyncio.to_thread(core.check_pw, body.old_password, user.pw_hash):
            raise HTTPException(403, "Joriy parol noto'g'ri")
        user.pw_hash = await asyncio.to_thread(core.hash_pw, body.new_password)
        others = (await db.execute(select(core.Session.token_hash).where(
            core.Session.user_id == user.id, core.Session.token_hash != token_hash))).scalars().all()
        await db.execute(delete(core.Session).where(core.Session.user_id == user.id,
                                                    core.Session.token_hash != token_hash))
        await db.commit()
    for other in others:  # boshqa qurilmalar darhol chiqib ketadi
        await core.hub.close_session(other)
    return {"ok": True, "closed": len(others)}


@router.get("/sessions")
async def list_sessions(authorization: str = Header(None)):
    token_hash = core.tok_hash(_bearer(authorization))
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        rows = (await db.execute(select(core.Session).where(core.Session.user_id == user.id)
                                 .order_by(core.Session.created_at.desc()))).scalars().all()
        return [{"id": s.id, "device": s.device or "Noma'lum qurilma", "created_at": s.created_at,
                 "current": s.token_hash == token_hash} for s in rows]


@router.delete("/sessions/{session_id}")
async def end_session(session_id: int, authorization: str = Header(None)):
    token_hash = core.tok_hash(_bearer(authorization))
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        row = await db.get(core.Session, session_id)
        if not row or row.user_id != user.id:
            raise HTTPException(404, "Seans topilmadi")
        if row.token_hash == token_hash:
            raise HTTPException(400, "Joriy seansni «Chiqish» tugmasi bilan tugating")
        victim = row.token_hash
        await db.delete(row)
        await db.commit()
    await core.hub.close_session(victim)
    return {"ok": True}


@router.post("/sessions/terminate-others")
async def end_other_sessions(authorization: str = Header(None)):
    token_hash = core.tok_hash(_bearer(authorization))
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        others = (await db.execute(select(core.Session.token_hash).where(
            core.Session.user_id == user.id, core.Session.token_hash != token_hash))).scalars().all()
        await db.execute(delete(core.Session).where(core.Session.user_id == user.id,
                                                    core.Session.token_hash != token_hash))
        await db.commit()
    for other in others:
        await core.hub.close_session(other)
    return {"ok": True, "closed": len(others)}


# ------------------------------------------------------------------ bloklash
@router.get("/blocked")
async def list_blocked(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        chats = (await db.execute(select(core.Chat).where(
            core.Chat.status == "blocked", core.Chat.blocked_by == user.id))).scalars().all()
        peers = await feat._users_bulk(db, [c.user_b if c.user_a == user.id else c.user_a for c in chats])
        return [{"chat_id": c.id, "user": core.ser_user(peers[c.user_b if c.user_a == user.id else c.user_a])}
                for c in chats if (c.user_b if c.user_a == user.id else c.user_a) in peers]


@router.post("/chats/{chat_id}/block")
async def block_chat(chat_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        chat = await core.member_chat(db, chat_id, user.id)
        chat.status, chat.blocked_by = "blocked", user.id
        await db.commit()
        peer = chat.user_b if chat.user_a == user.id else chat.user_a
    await core.hub.send(peer, {"type": "chat_gone", "chat_id": chat_id})
    return {"ok": True}


@router.post("/chats/{chat_id}/unblock")
async def unblock_chat(chat_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        chat = await core.member_chat(db, chat_id, user.id)
        if chat.status != "blocked" or chat.blocked_by != user.id:
            raise HTTPException(403, "Bu suhbatni siz bloklamagansiz")
        chat.status, chat.blocked_by = "accepted", None
        await db.commit()
        peer = chat.user_b if chat.user_a == user.id else chat.user_a
        await core.hub.send(peer, {"type": "chat_update", "chat": await core.ser_chat(db, chat, peer)})
        return await core.ser_chat(db, chat, user.id)


# ------------------------------------------------------------------ qadash (pin) va a'zo yorlig'i
class PinIn(BaseModel):
    message_id: int | None = None


@router.post("/chats/{chat_id}/pin")
async def pin_direct(chat_id: int, body: PinIn, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        chat = await core.member_chat(db, chat_id, user.id)
        if chat.status == "blocked":
            raise HTTPException(403, "Bu chat bloklangan")
        if body.message_id is None:
            chat.pinned_id = None
        else:
            msg = await db.get(core.Message, body.message_id)
            if not msg or msg.chat_id != chat_id or msg.deleted:
                raise HTTPException(404, "Xabar topilmadi")
            chat.pinned_id = msg.id
        await db.commit()
        for uid in (chat.user_a, chat.user_b):
            await core.hub.send(uid, {"type": "chat_update", "chat": await core.ser_chat(db, chat, uid)})
        return await core.ser_chat(db, chat, user.id)


@router.post("/rooms/{room_id}/pin")
async def pin_room(room_id: int, body: PinIn, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room = await feat._room(db, room_id)
        member = await feat._membership(db, room_id, user.id)
        if not feat.member_rights(member)["pin_messages"]:
            raise HTTPException(403, "Xabarni qadash huquqingiz yo'q")
        if body.message_id is None:
            room.pinned_id = None
        else:
            msg = await db.get(feat.RoomMessage, body.message_id)
            if not msg or msg.room_id != room_id or msg.deleted:
                raise HTTPException(404, "Xabar topilmadi")
            room.pinned_id = msg.id
        await db.commit()
        await feat._broadcast_room_update(db, room)
        return await feat.serialize_room(db, room, user.id)


class TagIn(BaseModel):
    tag: str = ""


@router.put("/rooms/{room_id}/members/{member_id}/tag")
async def set_member_tag(room_id: int, member_id: int, body: TagIn, authorization: str = Header(None)):
    tag = " ".join(body.tag.split())
    if len(tag) > 16 or any(ord(c) < 32 for c in tag):
        raise HTTPException(400, "Yorliq 16 belgigacha bo'lsin")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        room = await feat._room(db, room_id)
        me = await feat._membership(db, room_id, user.id)
        if room.kind != "group":
            raise HTTPException(400, "Yorliqlar faqat guruhlarda ishlaydi")
        is_admin = me.role in {"owner", "admin"}
        if member_id != user.id and not is_admin:
            raise HTTPException(403, "Boshqa a'zoga yorliqni faqat adminlar bera oladi")
        target = await feat._get_member(db, room_id, member_id)
        if not target:
            raise HTTPException(404, "A'zo topilmadi")
        if tag and not is_admin and core.display_name_reserved(tag):
            raise HTTPException(400, "Bu yorliqni ishlatib bo'lmaydi")
        target.tag = tag
        await db.commit()
        member_ids = (await db.execute(select(feat.RoomMember.user_id).where(feat.RoomMember.room_id == room_id))).scalars().all()
    event = {"type": "member_tag", "room_id": room_id, "user_id": member_id, "tag": tag}
    await core.hub.broadcast([(uid, event) for uid in member_ids])
    return {"ok": True, "tag": tag}


@router.get("/rooms/{room_id}/reads")
async def room_reads(room_id: int, authorization: str = Header(None)):
    """Guruh a'zolarining o'qilganlik chegarasi: ✓✓ ni hisoblash uchun."""
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        await feat._membership(db, room_id, user.id)
        rows = (await db.execute(select(feat.RoomMember.user_id, feat.RoomMember.last_read_id).where(
            feat.RoomMember.room_id == room_id))).all()
        return {str(uid): last for uid, last in rows}


# ------------------------------------------------------------------ yo'naltirish (forward)
class ForwardIn(BaseModel):
    source: str
    source_id: int
    message_id: int
    targets: list[dict]


@router.post("/forward")
async def forward(body: ForwardIn, authorization: str = Header(None)):
    if body.source not in {"direct", "room"} or not 1 <= len(body.targets) <= 5:
        raise HTTPException(400, "Noto'g'ri so'rov (ko'pi bilan 5 ta suhbatga)")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if body.source == "room":
            room = await feat._room(db, body.source_id)
            await feat._membership(db, body.source_id, user.id)
            msg = await db.get(feat.RoomMessage, body.message_id)
            if not msg or msg.room_id != body.source_id or msg.deleted:
                raise HTTPException(404, "Xabar topilmadi")
            origin = room.title if room.kind == "channel" else None
        else:
            chat = await core.member_chat(db, body.source_id, user.id)
            msg = await db.get(core.Message, body.message_id)
            if not msg or msg.chat_id != chat.id or msg.deleted:
                raise HTTPException(404, "Xabar topilmadi")
            origin = None
        author = await db.get(core.User, msg.sender_id)
        text, attach = msg.text, msg.attach
        fwd = (msg.fwd or origin or (author.display_name if author else "Foydalanuvchi"))[:80]
    sent, errors = 0, []
    for target in body.targets:
        kind, tid = target.get("kind"), target.get("id")
        if kind not in {"direct", "room"} or not isinstance(tid, int) or isinstance(tid, bool):
            errors.append({"kind": kind, "id": tid, "message": "Noto'g'ri manzil"})
            continue
        if core.limited(f"forward:{user.id}", 40, 60):
            errors.append({"kind": kind, "id": tid, "message": "Juda tez yuboryapsiz"})
            continue
        try:
            if kind == "room":
                await feat.post_room(user.id, tid, text, attach_json=attach, fwd=fwd)
            else:
                await core.post_direct(user.id, tid, text, attach_json=attach, fwd=fwd)
            sent += 1
        except core.SendError as exc:
            errors.append({"kind": kind, "id": tid, "message": exc.message})
    return {"sent": sent, "errors": errors}


# ------------------------------------------------------------------ saqlangan xabarlar
class SavedIn(BaseModel):
    text: str = ""
    source: str = ""        # "direct" | "room" | "" (o'zimga eslatma)
    source_id: int = 0
    message_id: int = 0


def _saved_view(item):
    return {"id": item.id, "text": item.text, "attachment": core._j(item.attach),
            "source": item.source, "created_at": item.created_at}


@router.get("/saved")
async def list_saved(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        rows = (await db.execute(select(SavedItem).where(SavedItem.user_id == user.id)
                                 .order_by(SavedItem.id.desc()).limit(200))).scalars().all()
        return [_saved_view(item) for item in rows]


@router.post("/saved")
async def add_saved(body: SavedIn, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        total = (await db.execute(select(func.count()).select_from(SavedItem).where(SavedItem.user_id == user.id))).scalar_one()
        if total >= 500:
            raise HTTPException(409, "Saqlangan xabarlar 500 tadan oshmasin — eskilarini o'chiring")
        text, attach, source = body.text.strip(), None, ""
        if body.source == "room":
            await feat._membership(db, body.source_id, user.id)
            msg = await db.get(feat.RoomMessage, body.message_id)
            if not msg or msg.room_id != body.source_id or msg.deleted:
                raise HTTPException(404, "Xabar topilmadi")
            author = await db.get(core.User, msg.sender_id)
            text, attach, source = msg.text, msg.attach, (author.display_name if author else "")
        elif body.source == "direct":
            chat = await core.member_chat(db, body.source_id, user.id)
            msg = await db.get(core.Message, body.message_id)
            if not msg or msg.chat_id != chat.id or msg.deleted:
                raise HTTPException(404, "Xabar topilmadi")
            author = await db.get(core.User, msg.sender_id)
            text, attach, source = msg.text, msg.attach, (author.display_name if author else "")
        elif body.source:
            raise HTTPException(400, "Noto'g'ri manba")
        if not text and not attach:
            raise HTTPException(400, "Bo'sh yozuvni saqlab bo'lmaydi")
        if len(text) > 4000:
            raise HTTPException(400, "Matn 4000 belgidan uzun")
        item = SavedItem(user_id=user.id, text=text, attach=attach, source=source[:80], created_at=time.time())
        db.add(item)
        await db.commit()
        return _saved_view(item)


@router.delete("/saved/{item_id}")
async def delete_saved(item_id: int, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        item = await db.get(SavedItem, item_id)
        if not item or item.user_id != user.id:
            raise HTTPException(404, "Yozuv topilmadi")
        old = item.attach
        await db.delete(item)
        await db.commit()
        if old:
            await release_attachments(db, [old])
    return {"ok": True}


# ------------------------------------------------------------------ Web Push
def push_enabled():
    return bool(VAPID_PUBLIC and VAPID_PRIVATE)


_vapid = None
_push_slots = asyncio.Semaphore(8)


def _get_vapid():
    global _vapid
    if _vapid is None:
        from py_vapid import Vapid
        _vapid = Vapid.from_string(private_key=VAPID_PRIVATE)
    return _vapid


def _send_push(sub, payload):
    """Bitta qurilmaga push yuboradi (alohida oqimda). HTTP holatini qaytaradi (410/404 = obuna o'lgan)."""
    from pywebpush import WebPushException, webpush
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=json.dumps(payload, ensure_ascii=False), vapid_private_key=_get_vapid(),
                vapid_claims={"sub": VAPID_SUBJECT}, ttl=3600, timeout=10)
        return 201
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning("push yuborilmadi (%s): %s", status, exc)
        return status or 0
    except Exception:
        log.exception("push xatosi")
        return 0


def _preview(msg):
    attachment, text = msg.get("attachment"), msg.get("text") or ""
    if attachment:
        label = {"image": "📷 Rasm", "voice": "🎤 Ovozli xabar"}.get(attachment.get("kind"), "📎 " + attachment.get("name", "Fayl"))
        return f"{label}: {text}" if text else label
    return text


async def _push_one(sub, payload):
    async with _push_slots:
        return sub["id"], await asyncio.to_thread(_send_push, sub, payload)


async def on_message(kind, sender, recipients, key, msg, room, mentioned):
    """Yangi xabar uchun ilova yopiq (yoki ko'rinmas) bo'lgan qabul qiluvchilarga push yuboradi."""
    if not push_enabled() or not recipients:
        return
    recipients = [uid for uid in recipients if not core.hub.is_visible(uid)]
    if not recipients:
        return
    async with core.SessionLocal() as db:
        subs = (await db.execute(select(PushSubscription).where(PushSubscription.user_id.in_(recipients)))).scalars().all()
        if not subs:
            return
        rows = (await db.execute(select(UserSettings).where(UserSettings.user_id.in_({s.user_id for s in subs})))).scalars().all()
    prefs = {row.user_id: (core._j(row.data) or {}) for row in rows}
    key_text = str(key)
    category = "direct" if kind == "direct" else ("channels" if room and room.kind == "channel" else "groups")
    body_text = core.snippet(_preview(msg), 140)
    jobs = []
    for sub in subs:
        user_prefs = prefs.get(sub.user_id, {})
        notif = user_prefs.get("notif", {})
        if notif.get(category, True) is False or key_text in user_prefs.get("muted", []):
            continue
        mention = sub.user_id in mentioned
        if notif.get("preview", True) is False:
            body = "Yangi xabar"
        elif kind == "room":
            body = f"{sender.display_name}: {body_text}"
        else:
            body = body_text
        title = (room.title if kind == "room" else sender.display_name) + (" · sizni eslatdi" if mention else "")
        payload = {"title": title, "body": body, "tag": "davra-" + key_text, "key": key}
        jobs.append(_push_one({"id": sub.id, "endpoint": sub.endpoint, "p256dh": sub.p256dh, "auth": sub.auth}, payload))
    if not jobs:
        return
    dead = [sub_id for sub_id, status in await asyncio.gather(*jobs) if status in (404, 410)]
    if dead:
        async with core.SessionLocal() as db:
            await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead)))
            await db.commit()


class PushSubIn(BaseModel):
    endpoint: str
    keys: dict


class PushUnsubIn(BaseModel):
    endpoint: str


@router.get("/push/key")
async def push_key(authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        await core.require_user(db, authorization)
    return {"enabled": push_enabled(), "key": VAPID_PUBLIC if push_enabled() else ""}


@router.post("/push/subscribe")
async def push_subscribe(body: PushSubIn, request: Request, authorization: str = Header(None)):
    if not push_enabled():
        raise HTTPException(503, "Push bildirishnomalar serverda sozlanmagan")
    p256dh, auth = body.keys.get("p256dh"), body.keys.get("auth")
    if not (body.endpoint.startswith("https://") and len(body.endpoint) <= 700
            and isinstance(p256dh, str) and isinstance(auth, str) and len(p256dh) <= 200 and len(auth) <= 100):
        raise HTTPException(400, "Obuna ma'lumoti noto'g'ri")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        row = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))).scalar_one_or_none()
        if row:  # shu qurilma boshqa akkauntga o'tgan bo'lishi mumkin
            row.user_id, row.p256dh, row.auth = user.id, p256dh, auth
        else:
            count = (await db.execute(select(func.count()).select_from(PushSubscription).where(
                PushSubscription.user_id == user.id))).scalar_one()
            if count >= 10:
                oldest = (await db.execute(select(PushSubscription).where(PushSubscription.user_id == user.id)
                                           .order_by(PushSubscription.id).limit(1))).scalar_one()
                await db.delete(oldest)
            db.add(PushSubscription(user_id=user.id, endpoint=body.endpoint, p256dh=p256dh, auth=auth,
                                    device=core.device_of(request), created_at=time.time()))
        await db.commit()
    return {"ok": True}


@router.post("/push/test")
async def push_test(authorization: str = Header(None)):
    """O'z qurilmalaringizga sinov bildirishnomasi yuboradi (ilova ochiq bo'lsa ham)."""
    if not push_enabled():
        raise HTTPException(503, "Push bildirishnomalar serverda sozlanmagan")
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        if core.limited(f"pushtest:{user.id}", 5, 600):
            raise HTTPException(429, "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring")
        subs = (await db.execute(select(PushSubscription).where(PushSubscription.user_id == user.id))).scalars().all()
    if not subs:
        raise HTTPException(404, "Bu akkaunt uchun bildirishnoma yoqilgan qurilma yo'q")
    payload = {"title": "Davra", "body": "Bildirishnomalar ishlayapti ✅", "tag": "davra-test", "key": None}
    results = await asyncio.gather(*(_push_one({"id": s.id, "endpoint": s.endpoint, "p256dh": s.p256dh,
                                                "auth": s.auth}, payload) for s in subs))
    dead = [sub_id for sub_id, status in results if status in (404, 410)]
    if dead:
        async with core.SessionLocal() as db:
            await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead)))
            await db.commit()
    return {"sent": sum(1 for _, status in results if status in (200, 201, 202)), "devices": len(subs)}


@router.post("/push/unsubscribe")
async def push_unsubscribe(body: PushUnsubIn, authorization: str = Header(None)):
    async with core.SessionLocal() as db:
        user = await core.require_user(db, authorization)
        await db.execute(delete(PushSubscription).where(PushSubscription.endpoint == body.endpoint,
                                                        PushSubscription.user_id == user.id))
        await db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ ulash
core.app.include_router(router)
core.hooks.migrate = _migrate_and_cleanup
core.hooks.resolve_attachment = resolve_attachment
core.hooks.release_attachments = release_attachments
core.hooks.on_message = on_message