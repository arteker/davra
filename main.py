"""Davra: messenjer backend (FastAPI + WebSocket).

Ishga tushirish (lokal):   uvicorn main:app --reload     yoki     python main.py
Render:                    uvicorn main:app --host 0.0.0.0 --port $PORT
Muhit o'zgaruvchilari:
  DATABASE_URL        Neon PostgreSQL. Bo'lmasa, SQLite (./suhbat.db) ishlatiladi.
  OWNER_USERNAME / OWNER_PASSWORD / OWNER_DISPLAY_NAME   rasmiy egasi akkaunti
  TRUSTED_PROXY_HOPS  serveringiz oldidagi proksi soni (Render: 1, Cloudflare+Render: 2). Standart: 1
  VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT   (ixtiyoriy) Web Push kalitlari.
                      Berilmasa, kalitlar avtomatik yaratilib bazada saqlanadi.
  MAX_UPLOAD_MB       yuboriladigan fayl hajmi chegarasi (standart 8)
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import types
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

if __name__ == "__main__":  # `python main.py` — modul "main" nomi bilan qayta import qilinadi
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
    raise SystemExit

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
# BU YERGA text QO'SHILDI
from sqlalchemy import (Boolean, Column, Float, ForeignKey, Integer, String, Text, text,
                        UniqueConstraint, delete, false, func, or_, select, true, update)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

log = logging.getLogger("davra")

# features.py va extras.py shu obyektga o'z funksiyalarini ulaydi (monkey-patch o'rniga aniq "ilgaklar").
hooks = types.SimpleNamespace(
    migrate=None,                    # async ()                      — qo'shimcha migratsiyalar
    serialize_direct_messages=None,  # async (db, messages, user_id) — reaksiya/javob bilan
    handle_room_send=None,           # async (user_id, data)
    handle_room_typing=None,         # async (user_id, data)
    handle_room_read=None,           # async (user_id, data)
    resolve_attachment=None,         # async (db, user_id, key) -> json matn (fayl shu foydalanuvchiniki bo'lsa)
    release_attachments=None,        # async (db, [json, ...])       — ishlatilmay qolgan fayllarni o'chiradi
    on_message=None,                 # async (kind, sender, recipients, key, msg, room, mentioned) — push
)

SESSION_TTL = 90 * 24 * 3600  # sessiya 90 kun amal qiladi

# ----------------------------------------------------------------- baza
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(32), unique=True, index=True, nullable=False)
    display_name = Column(String(50), nullable=False)
    bio = Column(String(160), nullable=False, default="")
    pw_hash = Column(String(200), nullable=False)
    created_at = Column(Float, nullable=False)
    last_seen = Column(Float, nullable=False)
    avatar_url = Column(String(255), nullable=True)
    is_owner = Column(Boolean, nullable=False, default=False)
    hide_seen = Column(Boolean, nullable=False, default=False, server_default=false())       # "oxirgi marta ko'rilgan"ni yashirish
    allow_requests = Column(Boolean, nullable=False, default=True, server_default=true())    # notanishlar yozishi mumkinmi


class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    created_at = Column(Float, nullable=False)
    device = Column(String(120), nullable=True)


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (UniqueConstraint("user_a", "user_b", name="uq_chat_pair"),)
    id = Column(Integer, primary_key=True)
    user_a = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)  # kichik id
    user_b = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)  # katta id
    requester_id = Column(Integer, nullable=False)  # birinchi yozgan odam
    status = Column(String(10), nullable=False, default="pending")  # pending | accepted | blocked
    blocked_by = Column(Integer, nullable=True)
    created_at = Column(Float, nullable=False)
    pinned_id = Column(Integer, nullable=True)  # qadalgan xabar


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), index=True, nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(Float, nullable=False)
    read_at = Column(Float, nullable=True)
    edited_at = Column(Float, nullable=True)
    deleted = Column(Boolean, nullable=False, default=False)
    attach = Column(Text, nullable=True)           # rasm/fayl/ovoz: JSON {key, kind, name, mime, size, ...}
    fwd = Column(String(80), nullable=True)        # yo'naltirilgan xabar: asl muallif/kanal nomi
    reply_to_id = Column(Integer, nullable=True)   # javob berilgan xabar


def make_engine():
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./suhbat.db")
    kwargs = {}
    if url.startswith(("postgres://", "postgresql://")):
        url = url.replace("postgres://", "postgresql://", 1)
        u = urlsplit(url)
        # sslmode va channel_binding kabi libpq-ga xos parametrlarni olib tashlaymiz
        q = [(k, v) for k, v in parse_qsl(u.query) if k not in ("sslmode", "channel_binding")]
        # asyncpg uchun +asyncpg qo'shamiz
        url = urlunsplit(("postgresql+asyncpg", u.netloc, u.path, urlencode(q), ""))
        host = (u.hostname or "").lower()
        if host not in ("localhost", "127.0.0.1"):
            kwargs["connect_args"] = {"ssl": True}  # Neon uchun SSL majburiy
        kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=300)
    return create_async_engine(url, **kwargs)

engine = make_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if hooks.migrate:
        await hooks.migrate()
    async with SessionLocal() as db:  # muddati o'tgan sessiyalarni tozalash
        await db.execute(delete(Session).where(Session.created_at < time.time() - SESSION_TTL))
        await db.commit()
    await bootstrap_owner()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# ----------------------------------------------------------- yordamchilar
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")
# Rasmiy ko'rinadigan nomlarni oddiy foydalanuvchi ola olmaydi (egasini taqlid qilishdan himoya).
RESERVED_USERNAMES = {
    "owner", "admin", "administrator", "root", "system", "support", "help", "official",
    "moderator", "mod", "staff", "team", "davra", "muloqot", "suhbat", "security", "bot", "null",
}
RESERVED_DISPLAY = {
    "owner", "admin", "administrator", "support", "official", "moderator", "davra", "muloqot",
    "davraegasi", "davraadmin", "davrasupport", "davrajamoasi", "davrateam",
    "muloqotegasi", "muloqotadmin", "muloqotsupport", "muloqotjamoasi", "muloqotteam",
}
attempts = defaultdict(deque)
_limited_calls = 0


class SendError(Exception):
    """Xabar yuborib bo'lmaganda (ruxsat yo'q, bloklangan, bo'sh va hokazo)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _j(raw):
    """JSON matnni ob'ektga aylantiradi (buzuq/bo'sh bo'lsa None)."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def snippet(text, limit=100):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


ATTACH_LABELS = {"image": "📷 Rasm", "voice": "🎤 Ovozli xabar"}


def preview_text(text, attach_raw):
    """Ro'yxat/qadash/javob uchun qisqa matn: rasm va fayllar uchun belgi bilan."""
    attachment = _j(attach_raw)
    if isinstance(attachment, dict):
        label = ATTACH_LABELS.get(attachment.get("kind")) or "📎 " + str(attachment.get("name") or "Fayl")
        return f"{label}: {text}" if text else label
    return text or ""


_background = set()


def spawn(coro):
    """Javobni kutdirmaydigan ishlar (push) uchun; xatolar jim yo'qolmaydi."""
    task = asyncio.create_task(coro)
    _background.add(task)

    def done(finished):
        _background.discard(finished)
        if not finished.cancelled() and finished.exception():
            log.error("fon vazifasi xatosi", exc_info=finished.exception())

    task.add_done_callback(done)


def _norm_label(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def owner_username():
    return os.getenv("OWNER_USERNAME", "").strip().lower().lstrip("@")


def username_reserved(username):
    return username in RESERVED_USERNAMES or (owner_username() and username == owner_username())


def display_name_reserved(name):
    return _norm_label(name) in RESERVED_DISPLAY


async def bootstrap_owner():
    username = owner_username()
    password = os.getenv("OWNER_PASSWORD", "")
    if not username and not password:
        return
    if not username or not password:
        raise RuntimeError("OWNER_USERNAME va OWNER_PASSWORD ikkalasini ham sozlang")
    if not USERNAME_RE.fullmatch(username):
        raise RuntimeError("OWNER_USERNAME 3–32 ta kichik lotin harfi, raqam yoki _ bo'lishi kerak")
    if len(password) < 12 or len(password) > 128:
        raise RuntimeError("OWNER_PASSWORD 12–128 belgidan iborat bo'lishi kerak")
    async with SessionLocal() as db:
        owner = (await db.execute(select(User).where(User.is_owner.is_(True)))).scalar_one_or_none()
        if owner:
            if owner.username != username:
                # Egasining username'i o'zgartirildi: shu akkauntning o'zi qayta nomlanadi (chatlar saqlanadi)
                taken = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
                if taken:
                    raise RuntimeError("Yangi OWNER_USERNAME boshqa akkauntga tegishli; boshqa username tanlang")
                owner.username = username
                await db.commit()
            return
        existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if existing:
            raise RuntimeError("OWNER_USERNAME mavjud oddiy akkauntga tegishli; boshqa username tanlang")
        display_name = os.getenv("OWNER_DISPLAY_NAME", "Davra egasi").strip()[:50] or "Davra egasi"
        owner = User(
            username=username,
            display_name=display_name,
            bio="Rasmiy Davra egasi",
            pw_hash=await asyncio.to_thread(hash_pw, password),
            created_at=time.time(),
            last_seen=time.time(),
            is_owner=True,
        )
        db.add(owner)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            owner = (await db.execute(select(User).where(User.is_owner.is_(True)))).scalar_one_or_none()
            if not owner or owner.username != username:
                raise RuntimeError("Owner akkauntini yaratib bo'lmadi") from None


def _prune(q, window, now):
    while q and now - q[0] > window:
        q.popleft()


def _sweep_attempts():
    """Eskirgan limit yozuvlarini vaqti-vaqti bilan tozalaydi (xotira oqib ketmasligi uchun)."""
    now = time.time()
    for key in [k for k, q in attempts.items() if not q or now - q[-1] > 3700]:
        attempts.pop(key, None)


def limited(key, limit, window):
    """Urinishni hisoblaydi; limit oshgan bo'lsa True qaytaradi."""
    global _limited_calls
    _limited_calls += 1
    if _limited_calls % 500 == 0:
        _sweep_attempts()
    q = attempts[key]
    t = time.time()
    _prune(q, window, t)
    if len(q) >= limit:
        return True
    q.append(t)
    return False


def over_limit(key, limit, window):
    """Hisoblamasdan faqat tekshiradi."""
    q = attempts.get(key)
    if not q:
        return False
    _prune(q, window, time.time())
    return len(q) >= limit


def record(key):
    attempts[key].append(time.time())


def ip_of(request: Request):
    """Mijoz IP'si. X-Forwarded-For ning boshi mijoz tomonidan yozilishi mumkin, shuning uchun
    ishonchli proksi qo'shgan yozuv (oxiridan hisoblaganda) olinadi."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        try:
            hops = max(1, int(os.getenv("TRUSTED_PROXY_HOPS", "1")))
        except ValueError:
            hops = 1
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if parts:
            return parts[max(0, len(parts) - hops)]
    return request.client.host if request.client else "?"


def device_of(request: Request):
    """Seanslar ro'yxati uchun qisqa qurilma nomi (User-Agent'dan)."""
    ua = request.headers.get("user-agent", "")[:300]
    if not ua:
        return "Noma'lum qurilma"
    browser = next((name for mark, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                            ("Chrome/", "Chrome"), ("CriOS/", "Chrome"), ("Safari/", "Safari"))
                    if mark in ua), "Brauzer")
    system = next((name for mark, name in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                           ("Windows", "Windows"), ("Mac OS X", "macOS"), ("Linux", "Linux"))
                   if mark in ua), "")
    return f"{browser} · {system}" if system else browser


def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def check_pw(pw: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def tok_hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


async def user_from_token(db, token):
    if not token:
        return None
    s = (await db.execute(select(Session).where(Session.token_hash == tok_hash(token)))).scalar_one_or_none()
    if not s:
        return None
    if time.time() - s.created_at > SESSION_TTL:
        await db.delete(s)
        await db.commit()
        return None
    return await db.get(User, s.user_id)


async def require_user(db, authorization):
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = await user_from_token(db, token)
    if not user:
        raise HTTPException(401, "Kirish talab qilinadi")
    return user


class Hub:
    SEND_TIMEOUT = 5  # sekin mijoz butun tarqatishni to'xtatib qo'ymasligi uchun

    def __init__(self):
        self.conns = defaultdict(dict)   # user_id -> {ws: token_hash}
        self.visible = {}                # ws -> sahifa ko'rinib turibdimi (push uchun)

    def online(self, uid):
        return bool(self.conns.get(uid))

    def set_visible(self, ws, value):
        self.visible[ws] = bool(value)

    def is_visible(self, uid):
        """Foydalanuvchining kamida bitta oynasi hozir ekranda ko'rinib turibdimi?"""
        return any(self.visible.get(ws, True) for ws in self.conns.get(uid, ()))

    def disconnect(self, uid, ws):
        self.visible.pop(ws, None)
        sockets = self.conns.get(uid)
        if sockets is not None:
            sockets.pop(ws, None)
            if not sockets:
                self.conns.pop(uid, None)

    async def _send_one(self, uid, ws, data):
        try:
            await asyncio.wait_for(ws.send_json(data), self.SEND_TIMEOUT)
        except Exception:
            self.disconnect(uid, ws)

    async def send(self, uid, data):
        sockets = list(self.conns.get(uid, ()))
        if sockets:
            await asyncio.gather(*(self._send_one(uid, ws, data) for ws in sockets))

    async def broadcast(self, pairs):
        """pairs: [(user_id, event), ...] — hammasiga parallel yuboradi."""
        if pairs:
            await asyncio.gather(*(self.send(uid, event) for uid, event in pairs))

    async def close_session(self, token_hash):
        for uid, sockets in list(self.conns.items()):
            for ws, active_token_hash in list(sockets.items()):
                if active_token_hash != token_hash:
                    continue
                try:
                    await ws.close(code=4401, reason="Session revoked")
                except (WebSocketDisconnect, RuntimeError):
                    pass
                self.disconnect(uid, ws)


hub = Hub()


def ser_user(u):
    hidden = bool(u.hide_seen)
    return {"id": u.id, "username": u.username, "display_name": u.display_name,
            "bio": u.bio, "avatar_url": u.avatar_url, "is_owner": bool(u.is_owner),
            "online": False if hidden else hub.online(u.id),
            "last_seen": None if hidden else u.last_seen,
            "hidden": hidden, "hide_seen": hidden, "allow_requests": bool(u.allow_requests)}


def ser_msg(m):
    deleted = bool(m.deleted)
    return {"id": m.id, "chat_id": m.chat_id, "sender_id": m.sender_id,
            "text": "Xabar o'chirildi" if deleted else m.text,
            "created_at": m.created_at, "read": m.read_at is not None,
            "edited": m.edited_at is not None, "deleted": deleted, "reactions": [],
            "attachment": None if deleted else _j(m.attach),
            "fwd": None if deleted else m.fwd,
            "reply_to": m.reply_to_id, "reply": None}


async def ser_chat(db, chat, me_id):
    peer_id = chat.user_b if chat.user_a == me_id else chat.user_a
    peer = await db.get(User, peer_id)
    last = (await db.execute(select(Message).where(Message.chat_id == chat.id)
                             .order_by(Message.id.desc()).limit(1))).scalar_one_or_none()
    unread = (await db.execute(select(func.count()).select_from(Message).where(
        Message.chat_id == chat.id, Message.sender_id != me_id, Message.read_at.is_(None),
        Message.deleted.is_(False)))).scalar_one()
    my_count = 0
    if chat.status == "pending":
        my_count = (await db.execute(select(func.count()).select_from(Message).where(
            Message.chat_id == chat.id, Message.sender_id == me_id))).scalar_one()
    pinned = None
    if chat.pinned_id:
        pm = await db.get(Message, chat.pinned_id)
        if pm and pm.chat_id == chat.id and not pm.deleted:
            pinned = {"id": pm.id, "sender_id": pm.sender_id, "text": snippet(preview_text(pm.text, pm.attach))}
    return {
        "id": chat.id, "status": chat.status, "created_at": chat.created_at,
        "i_am_requester": chat.requester_id == me_id, "my_count": my_count,
        "peer": ser_user(peer),
        "last_message": ser_msg(last) if last else None,
        "unread": unread, "pinned": pinned,
    }


def pair(a, b):
    return (a, b) if a < b else (b, a)


# ------------------------------------------------------------------ HTTP
class RegisterIn(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class ChatIn(BaseModel):
    username: str


async def new_session(db, user, request=None):
    token = secrets.token_urlsafe(32)
    db.add(Session(token_hash=tok_hash(token), user_id=user.id, created_at=time.time(),
                   device=device_of(request) if request else None))
    await db.commit()
    return token


# YANGILANGAN HEALTH ENDPOINT (Bazani uyg'oq tutadi)
@app.get("/api/health")
@app.get("/api/health/")
async def health():
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return {"ok": True, "db": "awake"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/register")
async def register(body: RegisterIn, request: Request):
    if limited("auth:" + ip_of(request), 20, 600):
        raise HTTPException(429, "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring")
    username = body.username.strip().lower().lstrip("@")
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Username 3-32 ta belgi: kichik lotin harflari, raqam va _")
    if username_reserved(username):
        raise HTTPException(400, "Bu username band qilingan, boshqasini tanlang")
    if not (8 <= len(body.password) <= 128):
        raise HTTPException(400, "Parol kamida 8 ta belgidan iborat bo'lsin")
    display = (body.display_name.strip() or username)[:50]
    if any(ord(c) < 32 for c in display):
        raise HTTPException(400, "Ismda noto'g'ri belgilar bor")
    if display_name_reserved(display):
        raise HTTPException(400, "Bu ismni ishlatib bo'lmaydi, boshqasini tanlang")
    pw = await asyncio.to_thread(hash_pw, body.password)
    async with SessionLocal() as db:
        user = User(username=username, display_name=display, pw_hash=pw,
                    created_at=time.time(), last_seen=time.time())
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "Bu username band")
        token = await new_session(db, user, request)
        return {"token": token, "me": ser_user(user)}


@app.post("/api/login")
async def login(body: LoginIn, request: Request):
    ip = ip_of(request)
    if limited("auth:" + ip, 20, 600):
        raise HTTPException(429, "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring")
    username = body.username.strip().lower().lstrip("@")[:64]
    # Noto'g'ri parol urinishlari: (IP+username) bo'yicha 10 daqiqada 8 ta, username bo'yicha soatiga 60 ta
    pair_key, user_key = f"fail:{ip}|{username}", f"failuser:{username}"
    if over_limit(pair_key, 8, 600) or over_limit(user_key, 60, 3600):
        raise HTTPException(429, "Juda ko'p noto'g'ri urinish. Birozdan keyin qayta urinib ko'ring")
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        ok = bool(user) and await asyncio.to_thread(check_pw, body.password, user.pw_hash)
        if not ok:
            record(pair_key)
            record(user_key)
            raise HTTPException(401, "Username yoki parol noto'g'ri")
        token = await new_session(db, user, request)
        return {"token": token, "me": ser_user(user)}


@app.post("/api/logout")
async def logout(authorization: str = Header(None)):
    token = (authorization or "").removeprefix("Bearer ").strip()
    async with SessionLocal() as db:
        s = (await db.execute(select(Session).where(Session.token_hash == tok_hash(token)))).scalar_one_or_none()
        if s:
            await db.delete(s)
            await db.commit()
    await hub.close_session(tok_hash(token))
    return {"ok": True}


@app.get("/api/me")
async def me(authorization: str = Header(None)):
    async with SessionLocal() as db:
        return ser_user(await require_user(db, authorization))


@app.get("/api/users")
async def search_users(q: str = "", authorization: str = Header(None)):
    async with SessionLocal() as db:
        user = await require_user(db, authorization)
        q = re.sub(r"[^a-z0-9_]", "", q.lower())[:32]
        if len(q) < 2:
            return []
        pattern = q.replace("_", "\\_") + "%"  # "_" LIKE'da joker belgi — oddiy belgi sifatida qidiramiz
        rows = (await db.execute(select(User).where(User.username.like(pattern, escape="\\"), User.id != user.id)
                                 .order_by(User.username).limit(10))).scalars().all()
        return [ser_user(u) for u in rows]


@app.get("/api/chats")
async def list_chats(authorization: str = Header(None)):
    async with SessionLocal() as db:
        user = await require_user(db, authorization)
        chats = (await db.execute(select(Chat).where(
            or_(Chat.user_a == user.id, Chat.user_b == user.id), Chat.status != "blocked"))).scalars().all()
        return [await ser_chat(db, c, user.id) for c in chats]


@app.post("/api/chats")
async def create_chat(body: ChatIn, authorization: str = Header(None)):
    async with SessionLocal() as db:
        user = await require_user(db, authorization)
        username = body.username.strip().lower().lstrip("@")
        other = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if not other or other.id == user.id:
            raise HTTPException(404, "Foydalanuvchi topilmadi")
        a, b = pair(user.id, other.id)
        chat = (await db.execute(select(Chat).where(Chat.user_a == a, Chat.user_b == b))).scalar_one_or_none()
        if chat:
            if chat.status == "blocked":
                if chat.blocked_by == user.id:
                    chat.status = "accepted"  # o'zim bloklagan edim, endi ochdim
                    chat.blocked_by = None
                    await db.commit()
                else:
                    raise HTTPException(403, "Bu foydalanuvchiga yozib bo'lmaydi")
        else:
            if not other.allow_requests:
                raise HTTPException(403, "Bu foydalanuvchi yangi suhbat so'rovlarini qabul qilmaydi")
            # spamga qarshi: soatiga 20 tadan ko'p yangi chat ochib bo'lmaydi
            if limited(f"newchat:{user.id}", 20, 3600):
                raise HTTPException(429, "Soatiga ko'pi bilan 20 ta yangi chat ochish mumkin")
            chat = Chat(user_a=a, user_b=b, requester_id=user.id, status="pending", created_at=time.time())
            db.add(chat)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                chat = (await db.execute(select(Chat).where(Chat.user_a == a, Chat.user_b == b))).scalar_one()
        return await ser_chat(db, chat, user.id)


async def member_chat(db, cid, uid):
    chat = await db.get(Chat, cid)
    if not chat or uid not in (chat.user_a, chat.user_b):
        raise HTTPException(404, "Chat topilmadi")
    return chat


@app.get("/api/chats/{cid}/messages")
async def get_messages(cid: int, before: int = 0, limit: int = 100, around: int = 0,
                       authorization: str = Header(None)):
    async with SessionLocal() as db:
        user = await require_user(db, authorization)
        await member_chat(db, cid, user.id)
        if around:  # qadalgan/javob berilgan xabarga sakrash: u atrofidagi oyna
            older = (await db.execute(select(Message).where(Message.chat_id == cid, Message.id <= around)
                                      .order_by(Message.id.desc()).limit(51))).scalars().all()
            newer = (await db.execute(select(Message).where(Message.chat_id == cid, Message.id > around)
                                      .order_by(Message.id.asc()).limit(51))).scalars().all()
            rows = list(reversed(older[:50])) + newer[:50]
            return {"messages": await hooks.serialize_direct_messages(db, rows, user.id),
                    "has_more": len(older) > 50, "has_newer": len(newer) > 50}
        limit = max(1, min(limit, 200))
        stmt = select(Message).where(Message.chat_id == cid)
        if before:
            stmt = stmt.where(Message.id < before)
        rows = (await db.execute(stmt.order_by(Message.id.desc()).limit(limit))).scalars().all()
        return {"messages": await hooks.serialize_direct_messages(db, list(reversed(rows)), user.id),
                "has_more": len(rows) == limit, "has_newer": False}


@app.post("/api/chats/{cid}/accept")
async def accept_chat(cid: int, authorization: str = Header(None)):
    async with SessionLocal() as db:
        user = await require_user(db, authorization)
        chat = await member_chat(db, cid, user.id)
        if chat.status == "pending" and chat.requester_id != user.id:
            chat.status = "accepted"
            await db.commit()
            peer_id = chat.user_b if chat.user_a == user.id else chat.user_a
            await hub.send(peer_id, {"type": "chat_update", "chat": await ser_chat(db, chat, peer_id)})
        return await ser_chat(db, chat, user.id)


@app.post("/api/chats/{cid}/decline")
async def decline_chat(cid: int, authorization: str = Header(None)):
    async with SessionLocal() as db:
        user = await require_user(db, authorization)
        chat = await member_chat(db, cid, user.id)
        if chat.requester_id != user.id:
            chat.status = "blocked"
            chat.blocked_by = user.id
            await db.commit()
        return {"ok": True}


# ------------------------------------------------------------- WebSocket
async def broadcast_presence(uid, online):
    async with SessionLocal() as db:
        u = await db.get(User, uid)
        if u and u.hide_seen:  # "oxirgi marta ko'rilgan" yashirilgan — holat tarqatilmaydi
            return
        rows = (await db.execute(select(Chat).where(
            or_(Chat.user_a == uid, Chat.user_b == uid), Chat.status != "blocked"))).scalars().all()
    last_seen = u.last_seen if u else time.time()
    event = {"type": "presence", "user_id": uid, "online": online, "last_seen": last_seen}
    await hub.broadcast([(c.user_b if c.user_a == uid else c.user_a, event) for c in rows])


async def post_direct(uid, cid, text, attach_json=None, attach_key=None, fwd=None, reply_to=None, temp=None):
    """Shaxsiy chatga xabar yozadi va tarqatadi. WS, yo'naltirish va boshqa joylar shu funksiyadan foydalanadi.
    Xatoda SendError ko'taradi."""
    text = (text or "").strip()
    if len(text) > 4000:
        raise SendError("invalid", "Xabar juda uzun (4000 belgigacha)")
    if not text and not attach_json and not attach_key:
        raise SendError("invalid", "Xabar bo'sh")
    async with SessionLocal() as db:
        chat = await db.get(Chat, cid)
        if not chat or uid not in (chat.user_a, chat.user_b):
            raise SendError("not_found", "Chat topilmadi")
        peer_id = chat.user_b if chat.user_a == uid else chat.user_a
        if chat.status == "blocked":
            raise SendError("blocked", "Bu chatga yozib bo'lmaydi")
        if chat.status == "pending":
            if chat.requester_id == uid:
                sent = (await db.execute(select(func.count()).select_from(Message).where(
                    Message.chat_id == cid, Message.sender_id == uid))).scalar_one()
                if sent >= 1:
                    raise SendError("pending", "So'rov qabul qilinmaguncha yana xabar yubora olmaysiz")
            else:
                chat.status = "accepted"  # javob yozish = qabul qilish
        if attach_key:
            if not hooks.resolve_attachment:
                raise SendError("invalid", "Fayl yuborish yoqilmagan")
            try:
                attach_json = await hooks.resolve_attachment(db, uid, attach_key)
            except ValueError as exc:
                raise SendError("invalid", str(exc))
        reply_id = None
        if reply_to is not None:
            target = await db.get(Message, reply_to)
            if not target or target.chat_id != cid or target.deleted:
                raise SendError("invalid", "Javob beriladigan xabar topilmadi")
            reply_id = target.id
        m = Message(chat_id=cid, sender_id=uid, text=text, created_at=time.time(),
                    attach=attach_json, fwd=fwd, reply_to_id=reply_id)
        db.add(m)
        await db.commit()
        msg = (await hooks.serialize_direct_messages(db, [m], uid))[0]
        chat_me = await ser_chat(db, chat, uid)
        chat_peer = await ser_chat(db, chat, peer_id)
        sender = await db.get(User, uid)
    await hub.broadcast([
        (uid, {"type": "message", "message": msg, "temp_id": temp, "chat": chat_me}),
        (peer_id, {"type": "message", "message": msg, "chat": chat_peer}),
    ])
    if hooks.on_message:
        spawn(hooks.on_message("direct", sender, [peer_id], cid, msg, None, set()))
    return msg


async def handle_send(uid, data):
    temp = data.get("temp_id")
    cid, text = data.get("chat_id"), data.get("text", "")
    key, reply = data.get("attachment"), data.get("reply_to")

    async def err(code, msg):
        await hub.send(uid, {"type": "error", "code": code, "message": msg, "temp_id": temp})

    def is_int(value):
        return isinstance(value, int) and not isinstance(value, bool)

    if not isinstance(text, str) or not is_int(cid) or (key is not None and not isinstance(key, str)) \
            or (reply is not None and not is_int(reply)):
        return await err("invalid", "Noto'g'ri so'rov")
    try:
        await post_direct(uid, cid, text, attach_key=key, reply_to=reply, temp=temp)
    except SendError as exc:
        await err(exc.code, exc.message)


async def handle_read(uid, data):
    cid = data.get("chat_id")
    if not isinstance(cid, int) or isinstance(cid, bool):
        return
    async with SessionLocal() as db:
        chat = await db.get(Chat, cid)
        if not chat or uid not in (chat.user_a, chat.user_b):
            return
        res = await db.execute(update(Message).where(
            Message.chat_id == cid, Message.sender_id != uid, Message.read_at.is_(None))
            .values(read_at=time.time()))
        await db.commit()
        peer_id = chat.user_b if chat.user_a == uid else chat.user_a
    if res.rowcount:
        await hub.broadcast([
            (peer_id, {"type": "read", "chat_id": cid, "reader_id": uid}),
            (uid, {"type": "read_self", "chat_id": cid}),
        ])


async def handle_typing(uid, data):
    cid = data.get("chat_id")
    if not isinstance(cid, int) or isinstance(cid, bool):
        return
    async with SessionLocal() as db:
        chat = await db.get(Chat, cid)
        if not chat or uid not in (chat.user_a, chat.user_b) or chat.status == "blocked":
            return
        peer_id = chat.user_b if chat.user_a == uid else chat.user_a
    await hub.send(peer_id, {"type": "typing", "chat_id": cid, "user_id": uid})


MAX_WS_FRAME = 20_000  # belgi; xabar matni 4000 gacha, qolgani JSON qobig'i uchun


async def dispatch(uid, t, data, ws, recent):
    """Bitta WebSocket buyrug'ini bajaradi. Xatolar chaqiruvchida ushlanadi."""
    if t == "ping":
        await ws.send_json({"type": "pong"})
    elif t == "visibility":
        hub.set_visible(ws, data.get("visible"))
    elif t in ("send", "room_send"):
        ts = time.time()
        while recent and ts - recent[0] > 10:
            recent.popleft()
        if len(recent) >= 20:
            await hub.send(uid, {"type": "error", "code": "rate", "temp_id": data.get("temp_id"),
                                 "message": "Juda tez yozyapsiz, biroz kuting"})
            return
        recent.append(ts)
        await (handle_send(uid, data) if t == "send" else hooks.handle_room_send(uid, data))
    elif t == "read":
        await handle_read(uid, data)
    elif t == "typing":
        await handle_typing(uid, data)
    elif t == "room_typing":
        await hooks.handle_room_typing(uid, data)
    elif t == "room_read":
        await hooks.handle_room_read(uid, data)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    token = ws.query_params.get("token")
    async with SessionLocal() as db:
        user = await user_from_token(db, token)
    if not user:
        await ws.close(code=4401)
        return
    uid = user.id
    first = not hub.online(uid)
    hub.conns[uid][ws] = tok_hash(token)
    hub.set_visible(ws, True)  # mijoz ulangandan keyin haqiqiy holatni yuboradi
    if first:
        try:
            await broadcast_presence(uid, True)
        except Exception:
            log.exception("presence(online) xatosi uid=%s", uid)
    recent = deque()
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_WS_FRAME:
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(data, dict) or not isinstance(data.get("type"), str):
                continue
            t = data["type"]
            try:
                await dispatch(uid, t, data, ws, recent)
            except (WebSocketDisconnect, RuntimeError):
                raise
            except Exception:
                # Bitta buyruqdagi xato butun ulanishni uzmasligi va jimgina yo'qolmasligi kerak
                log.exception("WS buyrug'i bajarilmadi: type=%s uid=%s", t, uid)
                await hub.send(uid, {"type": "error", "code": "server", "temp_id": data.get("temp_id"),
                                     "message": "Server xatosi. Birozdan keyin qayta urinib ko'ring"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        log.exception("WS ulanishi xato bilan tugadi uid=%s", uid)
    finally:
        hub.disconnect(uid, ws)
        if not hub.online(uid):
            try:
                async with SessionLocal() as db:
                    await db.execute(update(User).where(User.id == uid).values(last_seen=time.time()))
                    await db.commit()
                await broadcast_presence(uid, False)
            except Exception:
                log.exception("presence(offline) xatosi uid=%s", uid)


# Qo'shimcha: profillar, guruhlar, kanallar (features) va fayl/push/sozlamalar (extras).
import features  # noqa: E402,F401
import extras  # noqa: E402,F401

# Frontend (PWA) fayllari — eng oxirida ulanadi
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")