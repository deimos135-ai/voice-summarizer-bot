import os, asyncio, httpx, os.path
from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.enums import ParseMode
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties

from util import now_tz, today_bounds, next_run_at, TZ
from db import init_db, add_note, get_notes_between
from ai import whisper_transcribe, analyze_notes_text, render_daily_summary

APP_URL   = os.getenv("APP_URL")             # https://<your-app>.fly.dev
BOT_TOKEN = os.getenv("TG_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROUP_ID = int(os.getenv("GROUP_ID", "0"))   # -100123456789
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret-path")
RUN_DAILY = os.getenv("RUN_DAILY", "1")      # "1" -> вмикати внутрішній планувальник

if not (APP_URL and BOT_TOKEN and OPENAI_API_KEY and GROUP_ID):
    raise RuntimeError("APP_URL, TG_TOKEN, OPENAI_API_KEY, GROUP_ID є обов'язковими env")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI()

# ==== УТИЛІТИ ====
async def build_and_send_summary(chat_id: int):
    from datetime import date
    start, end = today_bounds()
    rows = await get_notes_between(str(chat_id), start, end)
    if not rows:
        await bot.send_message(chat_id, f"**Звіт за {date.today().isoformat()}**: без нових нотаток.")
        return

    texts, authors = [], set()
    for _, user_id, _, text, created_at in rows:
        texts.append(f"[{created_at}] {text}")
        authors.add(user_id)

    concat = "\n".join(texts)
    analysis = await analyze_notes_text(concat)
    author_str = "кілька учасників" if len(authors) > 1 else next(iter(authors))
    rendered = render_daily_summary(date.today().isoformat(), author_str, analysis)
    await bot.send_message(chat_id, rendered)

# ==== ХЕНДЛЕРИ ====
@router.message(F.voice)
async def handle_voice(message: types.Message):
    # 1) завантажити файл
    f = await bot.get_file(message.voice.file_id)
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}"
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.get(file_url)
        r.raise_for_status()
        file_bytes = r.content

    # 2) транскрипція
    text = await whisper_transcribe(file_bytes, filename=os.path.basename(f.file_path), language="uk")

    # 3) зберегти
    await add_note(str(message.from_user.id), str(message.chat.id), text, now_tz())

    # 4) підтвердження + кнопка «Сформувати звіт»
    preview = (text[:200] + "…") if len(text) > 200 else text
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Сформувати звіт", callback_data="make_summary")]
    ])
    await message.reply(f"✅ Транскрибовано:\n_{preview}_", reply_markup=kb)

@router.message(F.text == "/summary")
async def cmd_summary(message: types.Message):
    await build_and_send_summary(message.chat.id)

@router.message(F.text == "/today")
async def cmd_today(message: types.Message):
    start, end = today_bounds()
    rows = await get_notes_between(str(message.chat.id), start, end)
    if not rows:
        await message.reply("Сьогодні ще нема нотаток.")
        return
    formatted = "\n\n".join([f"🕘 {r[4]}:\n{r[3]}" for r in rows])
    await message.reply(f"Нотатки за сьогодні:\n\n{formatted}")

@router.callback_query(F.data == "make_summary")
async def on_make_summary(cb: types.CallbackQuery):
    await cb.answer("Готую зведення…")
    await build_and_send_summary(cb.message.chat.id)

# ==== ВЕБХУК/СТАРТ ====
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
    # щодня о 20:00 Europe/Kyiv -> шлемо зведення в GROUP_ID
    while True:
        run_at = next_run_at(20, 0, 0)
        delay = (run_at - now_tz()).total_seconds()
        await asyncio.sleep(delay)
        try:
            await build_and_send_summary(GROUP_ID)
        except Exception as e:
            # не падаємо через одну помилку
            try:
                await bot.send_message(GROUP_ID, f"⚠️ Помилка генерації звіту: {e}")
            except Exception:
                pass
            continue
