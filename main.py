import os, asyncio, httpx
from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.enums import ParseMode
from aiogram.types import Update
from aiogram.client.default import DefaultBotProperties

from util import now_tz, today_bounds, TZ
from db import init_db, add_note, get_notes_between
from ai import whisper_transcribe, analyze_notes_text, render_daily_summary

APP_URL = os.getenv("APP_URL")            # https://<your-app>.fly.dev
BOT_TOKEN = os.getenv("TG_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROUP_ID = os.getenv("GROUP_ID")          # куди кидати щоденний звіт (ід групи/каналу, типу -100123456789)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret-path")  # довільний секрет у URL вебхука
RUN_DAILY = os.getenv("RUN_DAILY", "1")   # "1" вмикає планувальник усередині процесу

if not (APP_URL and BOT_TOKEN and OPENAI_API_KEY and GROUP_ID):
    raise RuntimeError("APP_URL, TG_TOKEN, OPENAI_API_KEY, GROUP_ID є обов'язковими env")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI()

@router.message(F.voice)
async def handle_voice(message: types.Message):
    # 1) забрати файл
    f = await bot.get_file(message.voice.file_id)
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(file_url)
        r.raise_for_status()
        file_bytes = r.content

    # 2) транскрипція Whisper
    text = await whisper_transcribe(file_bytes, filename=os.path.basename(f.file_path), language="uk")

    # 3) зберегти
    await add_note(str(message.from_user.id), str(message.chat.id), text, now_tz())

    # 4) коротке підтвердження
    preview = (text[:200] + "…") if len(text) > 200 else text
    await message.reply(f"✅ Транскрибовано:\n_{preview}_")

@router.message(F.text == "/today")
async def cmd_today(message: types.Message):
    start, end = today_bounds()
    rows = await get_notes_between(str(message.chat.id), start, end)
    if not rows:
        await message.reply("Сьогодні ще нема нотаток.")
        return
    formatted = "\n\n".join([f"🕘 {r[4]}:\n{r[3]}" for r in rows])
    await message.reply(f"Нотатки за сьогодні:\n\n{formatted}")

async def set_webhook():
    # встановити вебхук на APP_URL/WEBHOOK_SECRET
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    target = f"{APP_URL}/{WEBHOOK_SECRET}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json={"url": target, "allowed_updates": ["message"]})
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
    # внутрішній планувальник: щодня о 20:00 Europe/Kyiv
    while True:
        now = now_tz()
        run_at = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if now >= run_at:
            # наступний день
            from datetime import timedelta
            run_at = run_at + timedelta(days=1)
        await asyncio.sleep((run_at - now).total_seconds())
        try:
            # Готуємо зведення за сьогодні для цього GROUP_ID
            start, end = today_bounds()
            rows = await get_notes_between(str(GROUP_ID), start, end)
            # Якщо нотатки в приватах — можеш вирішити зводити по всіх чатах. Для простоти беремо по групі:
            # (Або збери по всіх чатах та вкажи автора)
            if not rows:
                # спробуємо альтернативу: зводити ВСІ нотатки, не тільки з групи
                # (для MVP можна пропустити; залишимо як є)
                pass
            texts = []
            authors = set()
            for _, user_id, chat_id, text, created_at in rows:
                texts.append(f"[{created_at}] {text}")
                authors.add(user_id)
            if not texts:
                await bot.send_message(int(GROUP_ID), f"**Звіт за {now.date().isoformat()}**: без нових нотаток.")
                continue
            concat = "\n".join(texts)
            analysis = await analyze_notes_text(concat)
            author_str = "кілька учасників" if len(authors) > 1 else next(iter(authors))
            rendered = render_daily_summary(now.date().isoformat(), author_str, analysis)
            await bot.send_message(int(GROUP_ID), rendered)
        except Exception as e:
            # не падаємо через збій одного циклу
            await bot.send_message(int(GROUP_ID), f"⚠️ Помилка генерації звіту: {e}")
            continue
