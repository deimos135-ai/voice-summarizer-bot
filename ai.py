import os, json, httpx
from typing import Dict

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

ANALYZE_PROMPT = """Ти асистент, який з коротких розмовних нотаток робить структуру.
Поверни JSON формату:
{
 "events": [ "..." ],
 "tasks": [ {"title":"", "due": null, "owner":"", "priority":"low|med|high"} ],
 "risks": [ "..." ],
 "ideas": [ "..." ],
 "quotes": [ "..." ]
}
Якщо чогось немає — став порожні списки. Дати у ISO (YYYY-MM-DD), час 24-год., таймзона Europe/Kyiv.
Стисло переформульовуй. Витягуй дедлайни з контексту (“завтра”, “до понеділка”) та нормалізуй."""

async def whisper_transcribe(file_bytes: bytes, filename: str, language: str = "uk") -> str:
    # Telegram voice зазвичай .oga/.ogg; OpenAI приймає напряму
    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    files = {
        "file": (filename, file_bytes, "audio/ogg"),
    }
    data = {"model": "whisper-1", "language": language}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, data=data, files=files)
        r.raise_for_status()
        return r.json()["text"]

async def analyze_notes_text(concatenated_text: str) -> Dict:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "Ти корисний аналітик нотаток."},
            {"role": "user", "content": ANALYZE_PROMPT + "\n\nТекст нотаток:\n" + concatenated_text}
        ],
        "temperature": 0.2
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except Exception:
        # Якщо модель прислала текст — спробуємо вирізати JSON найпростішим способом
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            return json.loads(content[start:end+1])
        return {"events":[], "tasks":[], "risks":[], "ideas":[], "quotes":[]}

def render_daily_summary(date_str: str, author: str, analysis: Dict) -> str:
    lines = [f"**Звіт за {date_str} ({author})**"]
    if analysis.get("events"):
        lines.append("**Події:**")
        for e in analysis["events"]:
            lines.append(f"- {e}")
    if analysis.get("tasks"):
        lines.append("**Задачі (next actions):**")
        for t in analysis["tasks"]:
            due = f" — *до {t['due']}*" if t.get("due") else ""
            owner = f" (відп.: {t['owner']})" if t.get("owner") else ""
            prio = f" [{t.get('priority','med')}]" 
            lines.append(f"- {t['title']}{due}{owner}{prio}")
    if analysis.get("risks"):
        lines.append("**Ризики/блокери:**")
        for r in analysis["risks"]:
            lines.append(f"- {r}")
    if analysis.get("ideas"):
        lines.append("**Ідеї:**")
        for i in analysis["ideas"]:
            lines.append(f"- {i}")
    if analysis.get("quotes"):
        lines.append("**Цитати:**")
        for q in analysis["quotes"]:
            lines.append(f"> {q}")
    return "\n".join(lines) if len(lines) > 1 else f"**Звіт за {date_str}**: без нових нотаток."
