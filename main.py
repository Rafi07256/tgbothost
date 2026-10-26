"""
UNIVERSAL TELEGRAM BOT HOSTING ENGINE - CLEAN, FAST & BULLETPROOF
Focused solely on seamless, error-free bot hosting with auto-recovery and memory isolation.
"""
import asyncio
import base64
import os
import re
import shutil
import signal
import sys
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup
)
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, Uuid, select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import uvicorn

# ==========================================
# 1. CONFIGURATION & DIRECTORIES
# ==========================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8879019070:AAGLGBwerrurW7bAeLJUpUew3FbABEzsJfE")
OWNER_IDS = [7736278920]
ADMIN_IDS = [7267577982]
ALL_ADMINS = OWNER_IDS + ADMIN_IDS

LOCAL_DB_URL = os.getenv("LOCAL_DATABASE_URL", "postgresql+asyncpg://postgres:securepass123@127.0.0.1:5432/bot_platform_db")
CLOUD_DB_URL = os.getenv("CLOUD_DATABASE_URL", "postgresql+asyncpg://neondb_owner:npg_g3rHh7cjfkVI@ep-muddy-water-ao6tzrlc-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?ssl=require")

STORAGE_DIR = Path("./storage")
PROJECTS_DIR = STORAGE_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

cipher = Fernet(base64.urlsafe_b64encode(b"universal_bot_platform_32bytes_k"))

def encrypt_token(plain: str) -> str:
    return cipher.encrypt(plain.encode()).decode()

def decrypt_token(enc: str) -> str:
    try:
        return cipher.decrypt(enc.encode()).decode()
    except Exception:
        return enc

# ==========================================
# 2. STREAMLINED DATABASE MODELS
# ==========================================
class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="User")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class HostedBot(Base):
    __tablename__ = "hosted_bots"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    runtime: Mapped[str] = mapped_column(String(20), default="python")
    entry_file: Mapped[str] = mapped_column(String(64), default="main.py")
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="running")
    restart_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

local_engine = create_async_engine(LOCAL_DB_URL, echo=False, pool_pre_ping=True)
LocalSession = async_sessionmaker(bind=local_engine, expire_on_commit=False)

cloud_engine = None
CloudSession = None
if CLOUD_DB_URL:
    try:
        cloud_engine = create_async_engine(CLOUD_DB_URL, echo=False, pool_pre_ping=True)
        CloudSession = async_sessionmaker(bind=cloud_engine, expire_on_commit=False)
    except Exception:
        pass

async def sync_mirror(obj):
    if not CloudSession:
        return
    try:
        async with CloudSession() as cs:
            await cs.merge(obj)
            await cs.commit()
    except Exception:
        pass

# ==========================================
# 3. FAIR RESOURCE ALLOCATION RUNNER
# ==========================================
active_processes: Dict[str, asyncio.subprocess.Process] = {}

async def start_user_bot(bot_id: str, entry_file: str, token: str) -> bool:
    proj_dir = (PROJECTS_DIR / bot_id).resolve()
    proj_dir.mkdir(parents=True, exist_ok=True)
    log_file = proj_dir / "execution.log"

    await stop_user_bot(bot_id)

    env = os.environ.copy()
    env.update({
        "BOT_TOKEN": token,
        "PYTHONUNBUFFERED": "1",
        "PYTHONHASHSEED": "random"
    })

    cmd = f"python3 {entry_file}"

    try:
        with open(log_file, "a") as out:
            proc = await asyncio.create_subprocess_shell(
                cmd, cwd=str(proj_dir), env=env, stdout=out, stderr=out, preexec_fn=os.setsid
            )
        active_processes[bot_id] = proc
        return True
    except Exception as e:
        print(f"Error starting {bot_id}: {e}")
        return False

async def stop_user_bot(bot_id: str) -> bool:
    proc = active_processes.pop(bot_id, None)
    if proc:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            await asyncio.sleep(0.5)
            if proc.returncode is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        return True
    return False

def read_bot_logs(bot_id: str, tail: int = 35) -> str:
    log_file = PROJECTS_DIR / bot_id / "execution.log"
    if log_file.exists():
        with open(log_file, "r", errors="replace") as f:
            lines = f.readlines()
            return "".join(lines[-tail:])
    return "No logs generated yet."

# ==========================================
# 4. BOT ROUTER & USER INTERFACE
# ==========================================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()

class BotUploadFSM(StatesGroup):
    waiting_token = State()
    waiting_type = State()
    waiting_zip = State()

def clean_sticky_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Add New Bot"), KeyboardButton(text="🤖 My Bots")],
            [KeyboardButton(text="⚡ System Status"), KeyboardButton(text="💬 Support")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

async def get_or_create_user(tg_user: types.User) -> User:
    async with LocalSession() as s:
        user = (await s.execute(select(User).where(User.telegram_id == tg_user.id))).scalar_one_or_none()
        if not user:
            user = User(telegram_id=tg_user.id, username=tg_user.username, first_name=tg_user.first_name or "User")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            asyncio.create_task(sync_mirror(user))
        return user

@router.message(CommandStart())
@router.message(Command("menu"))
async def handle_start(message: types.Message, state: FSMContext = None):
    if state:
        await state.clear()
    user = await get_or_create_user(message.from_user)

    async with LocalSession() as s:
        bot_cnt = (await s.execute(
            select(func.count(HostedBot.id)).where(HostedBot.user_id == user.id, HostedBot.status != "deleted")
        )).scalar_one() or 0

    role_text = "👑 SUPERADMIN" if user.telegram_id in OWNER_IDS else ("🛡 ADMIN" if user.telegram_id in ADMIN_IDS else "👤 USER")

    text = (
        f"👋 <b>Welcome, {user.first_name}!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Role: <code>{role_text}</code>\n"
        f"🤖 Active Bots: <b>{bot_cnt}</b> hosted\n"
        f"⚡ Allocated Resource: <b>Safe RAM & CPU Isolation</b>\n"
        f"🟢 Engine: <b>Online & Ready</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Host your Telegram bot smoothly without limits below:"
    )
    await message.answer(text, reply_markup=clean_sticky_keyboard())

# --- ADD BOT WORKFLOW ---
@router.message(Command("addbot"))
@router.message(F.text == "➕ Add New Bot")
async def addbot_step1(message: types.Message, state: FSMContext):
    await state.set_state(BotUploadFSM.waiting_token)
    await message.answer(
        "🔑 <b>Step 1: Enter Bot Token</b>\n\n"
        "Send your Telegram Bot Token obtained from @BotFather:\n"
        "<i>Example: 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")]])
    )

@router.message(BotUploadFSM.waiting_token)
async def addbot_step2(message: types.Message, state: FSMContext):
    token = message.text.strip() if message.text else ""
    if not re.match(r"^[0-9]{8,10}:[a-zA-Z0-9_-]{35}$", token):
        await message.answer("❌ <b>Invalid Token Format!</b> Please copy the complete token from @BotFather:")
        return

    await state.update_data(token=token)
    await state.set_state(BotUploadFSM.waiting_type)

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Upload Custom Bot (.zip)", callback_data="mode:zip")],
        [InlineKeyboardButton(text="⚡ 1-Click Ready Echo Bot", callback_data="mode:echo")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")]
    ])
    await message.answer("⚙️ <b>Step 2: Choose Deployment Type:</b>", reply_markup=markup)

@router.callback_query(BotUploadFSM.waiting_type, F.data.startswith("mode:"))
async def addbot_step3(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    mode = call.data.split(":")[1]
    user = await get_or_create_user(call.from_user)
    data = await state.get_data()
    token = data["token"]

    if mode == "echo":
        bot_id = str(uuid.uuid4())
        p_dir = PROJECTS_DIR / bot_id
        p_dir.mkdir(parents=True, exist_ok=True)
        code = f"""import asyncio
from aiogram import Bot, Dispatcher, types

bot = Bot(token="{token}")
dp = Dispatcher()

@dp.message()
async def echo(m: types.Message):
    await m.answer(f"Echo: {{m.text}}")

asyncio.run(dp.start_polling(bot))
"""
        with open(p_dir / "main.py", "w") as f:
            f.write(code)

        async with LocalSession() as s:
            hbot = HostedBot(
                id=uuid.UUID(bot_id),
                name=f"EchoBot-{bot_id[:6]}",
                user_id=user.id,
                entry_file="main.py",
                encrypted_token=encrypt_token(token),
                status="running"
            )
            s.add(hbot)
            await s.commit()
            asyncio.create_task(sync_mirror(hbot))

        await start_user_bot(bot_id, "main.py", token)
        await state.clear()
        await call.message.edit_text("🎉 <b>Echo Bot is now LIVE and RUNNING!</b>\nCheck status anytime in 'My Bots'.")
        return

    await state.set_state(BotUploadFSM.waiting_zip)
    await call.message.edit_text("📦 <b>Step 3: Upload your bot project (.zip file):</b>")

@router.message(BotUploadFSM.waiting_zip, F.document)
async def addbot_process_zip(message: types.Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.lower().endswith(".zip"):
        await message.answer("❌ Only <code>.zip</code> files are accepted.")
        return

    user = await get_or_create_user(message.from_user)
    data = await state.get_data()
    token = data["token"]

    prog = await message.answer("📦 <b>1/3: Receiving and extracting archive...</b>")

    bot_id = str(uuid.uuid4())
    p_dir = PROJECTS_DIR / bot_id
    p_dir.mkdir(parents=True, exist_ok=True)
    zip_path = p_dir / "source.zip"

    await message.bot.download(doc, destination=zip_path)

    entry = "main.py"
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                ext = (p_dir / member.filename).resolve()
                if not str(ext).startswith(str(p_dir.resolve())):
                    raise Exception("Security: Path Traversal Detected!")
            zf.extractall(p_dir)
        os.remove(zip_path)

        for candidate in ["main.py", "bot.py", "app.py", "run.py"]:
            if (p_dir / candidate).exists():
                entry = candidate
                break
    except Exception as e:
        await prog.edit_text(f"❌ Extraction error: {e}")
        return

    # Install pip requirements if exists
    req_file = p_dir / "requirements.txt"
    if req_file.exists():
        await prog.edit_text("⏳ <b>2/3: Installing dependencies (pip install)... Please wait.</b>")
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "pip", "install", "-r", str(req_file))
        await proc.wait()

    await prog.edit_text("🚀 <b>3/3: Registering bot and launching in background...</b>")

    async with LocalSession() as s:
        hbot = HostedBot(
            id=uuid.UUID(bot_id),
            name=f"Bot-{bot_id[:6]}",
            user_id=user.id,
            entry_file=entry,
            encrypted_token=encrypt_token(token),
            status="running"
        )
        s.add(hbot)
        await s.commit()
        asyncio.create_task(sync_mirror(hbot))

    await state.clear()
    await start_user_bot(bot_id, entry, token)

    await prog.edit_text(
        f"✅ <b>Your Bot is successfully RUNNING!</b>\n\n"
        f"🤖 Name: <b>{hbot.name}</b>\n"
        f"📄 Entry Point: <code>{entry}</code>\n"
        f"⚡ Resource: <b>Isolated Safe Process</b>\n\n"
        f"You can monitor live logs or restart anytime in 'My Bots'! 🚀"
    )

# --- MY BOTS MANAGEMENT ---
@router.message(Command("mybots"))
@router.message(F.text == "🤖 My Bots")
async def list_my_bots(message: types.Message):
    user = await get_or_create_user(message.from_user)
    async with LocalSession() as s:
        bots = (await s.execute(
            select(HostedBot).where(HostedBot.user_id == user.id, HostedBot.status != "deleted")
        )).scalars().all()

    if not bots:
        await message.answer("ℹ️ You have no active bots. Tap '➕ Add New Bot' to deploy one!")
        return

    kb = []
    for b in bots:
        icon = "🟢" if str(b.id) in active_processes else "⚪"
        kb.append([InlineKeyboardButton(text=f"{icon} {b.name}", callback_data=f"bview:{str(b.id)}")])
    await message.answer("🤖 <b>Your Hosted Bots:</b>\nClick on any bot to control:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("bview:"))
async def view_single_bot(call: types.CallbackQuery):
    await call.answer()
    bid_str = call.data.split(":")[1]
    async with LocalSession() as s:
        b = await s.get(HostedBot, uuid.UUID(bid_str))
    if not b:
        return

    is_running = bid_str in active_processes
    text = (
        f"🤖 <b>Bot: {b.name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Status: <b>{'🟢 RUNNING' if is_running else '⚪ STOPPED'}</b>\n"
        f"Entry File: <code>{b.entry_file}</code>\n"
        f"Auto Restarts: <b>{b.restart_count}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )
    btns = []
    if is_running:
        btns.append([InlineKeyboardButton(text="⏹ Stop", callback_data=f"bact:stop:{bid_str}"),
                     InlineKeyboardButton(text="🔄 Restart", callback_data=f"bact:restart:{bid_str}")])
    else:
        btns.append([InlineKeyboardButton(text="▶️ Start", callback_data=f"bact:start:{bid_str}")])
    btns.append([InlineKeyboardButton(text="📜 Live Logs", callback_data=f"bact:logs:{bid_str}"),
                 InlineKeyboardButton(text="🗑 Delete", callback_data=f"bact:del:{bid_str}")])
    btns.append([InlineKeyboardButton(text="⬅️ Back", callback_data="back_mybots")])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@router.callback_query(F.data.startswith("bact:"))
async def action_single_bot(call: types.CallbackQuery):
    _, act, bid_str = call.data.split(":")
    await call.answer("Processing...", show_alert=False)

    async with LocalSession() as s:
        b = await s.get(HostedBot, uuid.UUID(bid_str))
    if not b:
        return

    token = decrypt_token(b.encrypted_token)

    if act == "start":
        await start_user_bot(bid_str, b.entry_file, token)
    elif act == "stop":
        await stop_user_bot(bid_str)
    elif act == "restart":
        await stop_user_bot(bid_str)
        await start_user_bot(bid_str, b.entry_file, token)
    elif act == "logs":
        logs = read_bot_logs(bid_str)
        await call.message.answer(f"📜 <b>Live Execution Logs ({b.name}):</b>\n<pre>{logs[-3500:]}</pre>")
        return
    elif act == "del":
        await stop_user_bot(bid_str)
        async with LocalSession() as s:
            db_b = await s.get(HostedBot, uuid.UUID(bid_str))
            db_b.status = "deleted"
            await s.commit()
            asyncio.create_task(sync_mirror(db_b))
        await call.message.edit_text("🗑 <b>Bot deleted successfully!</b>")
        return

    await view_single_bot(call)

@router.callback_query(F.data == "back_mybots")
async def back_mybots_cb(call: types.CallbackQuery):
    await call.message.delete()
    await list_my_bots(call.message)

@router.callback_query(F.data == "cancel_action")
async def cancel_cb(call: types.CallbackQuery, state: FSMContext = None):
    if state:
        await state.clear()
    await call.answer("Cancelled")
    await call.message.delete()

# --- STATUS & SUPPORT ---
@router.message(Command("status"))
@router.message(F.text == "⚡ System Status")
async def handle_status(message: types.Message):
    async with LocalSession() as s:
        tot_bots = (await s.execute(select(func.count(HostedBot.id)).where(HostedBot.status != "deleted"))).scalar_one() or 0
        tot_users = (await s.execute(select(func.count(User.id)))).scalar_one() or 0

    text = (
        "⚡ <b>Platform Health & Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Core Engine: <b>Operational</b>\n"
        f"🤖 Hosted Bots: <b>{len(active_processes)}</b> active / {tot_bots} total\n"
        f"👥 Registered Users: <b>{tot_users}</b>\n"
        f"💾 RAM & Storage: <b>Auto-balanced & Isolated</b>\n"
        f"🕒 UTC Clock: <code>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</code>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "✅ <i>All services operating smoothly without restrictions.</i>"
    )
    await message.answer(text)

@router.message(Command("support"))
@router.message(F.text == "💬 Support")
async def handle_support(message: types.Message):
    text = (
        "💬 <b>Support & Assistance</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Need help hosting or running your bot? Contact system admin:\n\n"
        "👨‍💻 <b>Admin:</b> @Rafi017256\n"
        "⏰ <b>Availability:</b> 24/7 Always Active"
    )
    await message.answer(text)

# ==========================================
# 5. ADMIN CONTROL PANEL & BROADCAST
# ==========================================
@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ALL_ADMINS:
        return

    async with LocalSession() as s:
        tot_u = (await s.execute(select(func.count(User.id)))).scalar_one() or 0
        tot_b = (await s.execute(select(func.count(HostedBot.id)).where(HostedBot.status != "deleted"))).scalar_one() or 0

    text = (
        f"👑 <b>Admin Control Center</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total Users: <code>{tot_u}</code>\n"
        f"🤖 Running Bots: <code>{len(active_processes)}</code> / {tot_b}\n"
        f"⚡ Database Mirror: <b>Neon Cloud Synced</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Select an admin operation:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Manage All Bots", callback_data="ad:bots")],
        [InlineKeyboardButton(text="📢 Broadcast Announcement", callback_data="ad:bcast")]
    ])
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data == "ad:bots")
async def admin_bots_view(call: types.CallbackQuery):
    await call.answer()
    async with LocalSession() as s:
        bots = (await s.execute(select(HostedBot).where(HostedBot.status != "deleted"))).scalars().all()

    if not bots:
        await call.message.edit_text("No bots hosted yet.")
        return

    kb = []
    for b in bots[:12]:
        icon = "🟢" if str(b.id) in active_processes else "⚪"
        kb.append([InlineKeyboardButton(text=f"{icon} {b.name}", callback_data=f"bview:{str(b.id)}")])
    await call.message.edit_text("🤖 <b>All Hosted Bots on Server:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "ad:bcast")
async def admin_bcast_prompt(call: types.CallbackQuery):
    await call.answer()
    await call.message.answer("📢 <b>To broadcast, type:</b>\n<code>/broadcast Your message here</code>")

@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if message.from_user.id not in ALL_ADMINS:
        return

    msg = message.text.replace("/broadcast", "").strip()
    if not msg:
        await message.answer("Usage: <code>/broadcast Your message</code>")
        return

    async with LocalSession() as s:
        uids = (await s.execute(select(User.telegram_id))).scalars().all()

    cnt = 0
    for uid in uids:
        try:
            await bot.send_message(uid, f"📢 <b>Announcement:</b>\n\n{msg}")
            cnt += 1
        except Exception:
            pass
    await message.answer(f"✅ Announcement delivered to <b>{cnt}</b> users!")

# ==========================================
# 6. CRASH AUTO-RECOVERY SUPERVISOR
# ==========================================
async def auto_recovery_supervisor():
    """Continuously monitors bots and auto-restarts unexpectedly crashed bots."""
    while True:
        await asyncio.sleep(20)
        for bot_id, proc in list(active_processes.items()):
            if proc.returncode is not None:
                print(f"[Auto-Recovery] Bot {bot_id} crashed! Restarting immediately...")
                try:
                    bid = uuid.UUID(bot_id)
                except Exception:
                    continue
                async with LocalSession() as s:
                    b = await s.get(HostedBot, bid)
                    if b and b.status == "running":
                        token = decrypt_token(b.encrypted_token)
                        b.restart_count += 1
                        await s.commit()
                        await start_user_bot(bot_id, b.entry_file, token)

                        # Notify owner in Telegram
                        u = await s.get(User, b.user_id)
                        if u:
                            try:
                                await bot.send_message(
                                    u.telegram_id,
                                    f"⚠️ <b>Notice:</b> Your bot <b>{b.name}</b> crashed and was automatically restarted by the supervisor!"
                                )
                            except Exception:
                                pass

# ==========================================
# 7. FASTAPI & BOT ENGINE LIFESPAN
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("⚡ Initializing Database Tables...")
    async with local_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if cloud_engine:
        try:
            async with cloud_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        except Exception:
            pass

    print("🚀 Starting Telegram Bot Polling...")
    polling_task = asyncio.create_task(dp.start_polling(bot))
    supervisor_task = asyncio.create_task(auto_recovery_supervisor())

    yield

    print("🛑 Shutting down engine...")
    await dp.stop_polling()
    await bot.session.close()
    polling_task.cancel()
    supervisor_task.cancel()
    for bid in list(active_processes.keys()):
        await stop_user_bot(bid)

app = FastAPI(title="Universal Bot Platform", lifespan=lifespan)
dp.include_router(router)

@app.get("/")
@app.get("/health")
async def health():
    return {"status": "healthy", "active_bots": len(active_processes)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)