import os
import os.path
import time
import asyncio
import httpx
import aiosqlite
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.enums import ParseMode
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties

from util import now_tz, today_bounds_epoch, next_run_at, TZ
from db import init_db, add_note, get_notes_between, get_last_n, DB_PATH
from ai import whisper_transcribe, analyze_notes_text, render_daily_summary

APP_URL        = os.getenv("APP_URL")             # https://<your-app>.fly.dev
BOT_TOKEN      = os.getenv("TG_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROUP_ID       = int(os.getenv("GROUP_ID", "0"))  # -100123456789 (група/канал)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret-path")
RUN_DAILY      = os.getenv("RUN_DAILY", "1")

if not (APP_URL and BOT_TOKEN and OPENAI_API_KEY and GROUP_ID):
    raise RuntimeError("APP_URL, TG_TOKEN, OPENAI_API_KEY, GROUP_ID є обов'язковими env")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI()

# ===== Допоміжне =====
def ts_to_local_str(ts: int) -> str:
    dt_local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ)
    return dt_local.strftime("%Y-%m-%d %H:%M")

async def build_and_send_summary(chat_id: int):
    """Звіт за сьогодні по НОТАТКАХ САМЕ ЦЬОГО ЧАТУ (для кнопки/локальних перевірок)."""
    start_ep, end_ep = today_bounds_epoch()
    print(f"DB_QUERY build_and_send_summary chat={chat_id} window=[{start_ep},{end_ep})")
    rows = await get_notes_between(str(chat_id), start_ep, end_ep)
    print(f"DB_QUERY rows_count={len(rows)}")

    today_str = now_tz().date().isoformat()

    if not rows:
        await bot.send_message(chat_id, f"**Звіт за {today_str}**: без нових нотаток.")
        return

    texts, authors = [], set()
    for _, user_id, _, text, ts in rows:
        authors.add(user_id)
        texts.append(text)

    concat = "\n".join(texts)
    author_str = "кілька учасників" if len(authors) > 1 else (next(iter(authors)) if authors else "—")
    try:
        analysis = await analyze_notes_text(concat)
        rendered = render_daily_summary(today_str, author_str, analysis)
        await bot.send_message(chat_id, rendered)
    except Exception as e:
        print(f"ANALYZE_ERROR(chat): {e}")
        bullet = "\n".join([f"- {t}" for t in texts])
        await bot.send_message(
            chat_id,
            f"**Звіт за {today_str} ({author_str})**\n"
            f"_Аналіз тимчасово недоступний; нижче сирі нотатки:_\n{bullet}"
        )

async def fetch_all_notes_today():
    """Усі нотатки за сьогодні без фільтра по чату. Повертає список (user_id, chat_id, text, ts)."""
    start_ep, end_ep = today_bounds_epoch()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, chat_id, text, created_at_epoch "
            "FROM notes WHERE created_at_epoch >= ? AND created_at_epoch < ? "
            "ORDER BY created_at_epoch ASC",
            (start_ep, end_ep)
        )
        rows = await cur.fetchall()
    return rows  # [(user_id, chat_id, text, ts), ...]

async def build_and_send_summary_all(target_chat_id: int):
    """
    ЗВЕДЕНИЙ ЗВІТ ЗА СЬОГОДНІ ПО ВСІХ КОРИСТУВАЧАХ (з усіх чатів).
    Для кожного user_id — окремий блок (із GPT-аналізом або сирим фолбеком).
    """
    today_str = now_tz().date().isoformat()
    rows = await fetch_all_notes_today()
    print(f"DB_QUERY summary_all rows={len(rows)}")

    if not rows:
        await bot.send_message(target_chat_id, f"**Зведений звіт за {today_str}**: сьогодні ще немає нотаток.")
        return

    # Групуємо по користувачу
    by_user: dict[str, list[str]] = defaultdict(list)
    for user_id, chat_id, text, ts in rows:
        by_user[user_id].append(text)

    sections = []
    for user_id, texts in by_user.items():
        concat = "\n".join(texts)
        try:
            analysis = await analyze_notes_text(concat)
            sections.append(render_daily_summary(today_str, f"user:{user_id}", analysis))
        except Exception as e:
            print(f"ANALYZE_ERROR(user={user_id}): {e}")
            bullet = "\n".join([f"- {t}" for t in texts])
            sections.append(f"**Звіт за {today_str} (user:{user_id})**\n_Аналіз недоступний; сирі нотатки:_\n{bullet}")

    final = "🧾 *Зведений звіт за сьогодні (по користувачах):*\n\n" + "\n\n".join(sections)
    await bot.send_message(target_chat_id, final)

async def build_and_send_summary_me(target_chat_id: int, user_id: str):
    """Звіт за сьогодні лише для конкретного користувача (з усіх чатів)."""
    today_str = now_tz().date().isoformat()
    start_ep, end_ep = today_bounds_epoch()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT text, created_at_epoch FROM notes "
            "WHERE user_id=? AND created_at_epoch >= ? AND created_at_epoch < ? "
            "ORDER BY created_at_epoch ASC",
            (user_id, start_ep, end_ep)
        )
        rows = await cur.fetchall()  # [(text, ts), ...]

    if not rows:
        await bot.send_message(target_chat_id, f"**Звіт за {today_str} (ви)**: без нових нотаток.")
        return

    texts = [r[0] for r in rows]
    concat = "\n".join(texts)
    try:
        analysis = await analyze_notes_text(concat)
        rendered = render_daily_summary(today_str, "ви", analysis)
        await bot.send_message(target_chat_id, rendered)
    except Exception as e:
        print(f"ANALYZE_ERROR(me): {e}")
        bullet = "\n".join([f"- {t}" for t in texts])
        await bot.send_message(
            target_chat_id,
            f"**Звіт за {today_str} (ви)**\n_Аналіз недоступний; сирі нотатки:_\n{bullet}"
        )

# ===== Хендлери =====
@router.message(F.voice)
async def handle_voice(message: types.Message):
    # 1) забрати файл із Telegram
    f = await bot.get_file(message.voice.file_id)
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}"
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.get(file_url)
        r.raise_for_status()
        file_bytes = r.content

    # 2) транскрипція через whisper-1
    text = await whisper_transcribe(file_bytes, filename=os.path.basename(f.file_path), language="uk")

    # 3) зберегти як epoch UTC
    epoch_now = int(time.time())
    chat_id_str = str(message.chat.id)
    user_id_str = str(message.from_user.id)
    await add_note(user_id_str, chat_id_str, text, epoch_now)
    print(f"DB_SAVE chat={chat_id_str} user={user_id_str} ts={epoch_now}")

    # 4) підтвердження + кнопка «Сформувати звіт» (локальний для цього чату)
    preview = (text[:200] + "…") if len(text) > 200 else text
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Сформувати звіт", callback_data="make_summary")]
    ])
    await message.reply(f"✅ Транскрибовано:\n_{preview}_", reply_markup=kb)

@router.message(F.text == "/summary")
async def cmd_summary(message: types.Message):
    # Локальний звіт за поточним чатом
    await build_and_send_summary(message.chat.id)

@router.message(F.text == "/summary_all")
async def cmd_summary_all(message: types.Message):
    # Зведений звіт по всіх користувачах за сьогодні
    await build_and_send_summary_all(message.chat.id)

@router.message(F.text == "/summary_me")
async def cmd_summary_me(message: types.Message):
    await build_and_send_summary_me(message.chat.id, str(message.from_user.id))

@router.message(F.text == "/summary_raw")
async def cmd_summary_raw(message: types.Message):
    """Швидкий сирий звіт по поточному чату без GPT — для перевірки збереження нотаток."""
    start_ep, end_ep = today_bounds_epoch()
    rows = await get_notes_between(str(message.chat.id), start_ep, end_ep)
    today_str = now_tz().date().isoformat()
    if not rows:
        await message.reply(f"**Сирий звіт за {today_str}**: без нових нотаток.")
        return
    lines = [f"**Сирий звіт за {today_str}:**"]
    for _, user_id, _, text, ts in rows:
        lines.append(f"- {ts_to_local_str(ts)}: {text}")
    await message.reply("\n".join(lines))

@router.message(F.text == "/today")
async def cmd_today(message: types.Message):
    start_ep, end_ep = today_bounds_epoch()
    rows = await get_notes_between(str(message.chat.id), start_ep, end_ep)
    if not rows:
        await message.reply("Сьогодні ще нема нотаток.")
        return
    formatted = "\n\n".join([f"🕘 {ts_to_local_str(r[4])}:\n{r[3]}" for r in rows])
    await message.reply(f"Нотатки за сьогодні:\n\n{formatted}")

@router.message(F.text == "/diag")
async def cmd_diag(message: types.Message):
    """Діагностика вікна доби + останні нотатки поточного чату."""
    start_ep, end_ep = today_bounds_epoch()
    rows = await get_notes_between(str(message.chat.id), start_ep, end_ep)
    sample = rows[-5:] if rows else []
    lines = [
        f"chat_id={message.chat.id}",
        f"window: [{start_ep}, {end_ep})  (count={len(rows)})",
    ]
    for _, user_id, _, text, ts in sample:
        short = text.replace("\n", " ")
        if len(short) > 60:
            short = short[:60] + "…"
        lines.append(f"- ts={ts} ({ts_to_local_str(ts)}) user={user_id} text={short}")
    await message.reply("```\n" + "\n".join(lines) + "\n```", parse_mode="Markdown")

@router.message(F.text == "/diag_all")
async def cmd_diag_all(message: types.Message):
    """10 останніх нот по всій БД — для швидкої перевірки запису."""
    rows = await get_last_n(10)
    if not rows:
        await message.reply("БД порожня.")
        return
    lines = []
    for _id, user_id, chat_id, text, ts in rows:
        short = text.replace("\n", " ")
        if len(short) > 60:
            short = short[:60] + "…"
        lines.append(f"#{_id} chat={chat_id} user={user_id} ts={ts} ({ts_to_local_str(ts)}) :: {short}")
    await message.reply("```\n" + "\n".join(lines) + "\n```", parse_mode="Markdown")

@router.callback_query(F.data == "make_summary")
async def on_make_summary(cb: types.CallbackQuery):
    # ✅ ФІКС: беремо chat_id саме з повідомлення, де знаходиться кнопка
    await cb.answer("Готую зведення…")
    chat_id = cb.message.chat.id
    await build_and_send_summary(chat_id)

# ===== Вебхук / старт =====
async def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    target = f"{APP_URL}/{WEBHOOK_SECRET}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json={"url": target, "allowed_updates": ["message", "callback_query"]})
        r.raise_for_status()

@app.on_event("startup")
async def on_startup():
    await init_db()
    await set_webhook()
    if RUN_DAILY == "1":
        asyncio.create_task(daily_summary_loop())

@app.post("/{token_path}")
async def telegram_webhook(token_path: str, request: Request):
    if token_path != WEBHOOK_SECRET:
        raise HTTPException(status_code=404)
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

async def daily_summary_loop():
    # Щодня о 20:00 Europe/Kyiv -> зведений звіт по всіх користувачах у GROUP_ID
    while True:
        target = next_run_at(20, 0, 0)
        delay = (target - now_tz()).total_seconds()
        await asyncio.sleep(delay)
        try:
            await build_and_send_summary_all(GROUP_ID)
        except Exception as e:
            try:
                await bot.send_message(GROUP_ID, f"⚠️ Помилка генерації зведеного звіту: {e}")
            except Exception:
                pass
            continue
