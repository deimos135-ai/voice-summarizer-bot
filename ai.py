import os, json, httpx
from typing import Dict

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "whisper-1")  # gpt-4o-transcribe | gpt-4o-mini-transcribe | whisper-1
ANALYZE_MODEL = os.getenv("ANALYZE_MODEL", "gpt-4o-mini")

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
    """
    Працює як із `whisper-1`, так і з новими `gpt-4o-*-transcribe` через той самий endpoint.
    Примітка: 4o-transcribe моделі повертають тільки text/json; розширені опції (srt/vtt/verbose_json) — лише для whisper-1. 
    """
    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    files = {"file": (filename, file_bytes, "audio/ogg")}
    data = {"model": TRANSCRIBE_MODEL}
    # параметр language підтримується; для змішаних мов можна не вказувати
    if language:
        data["language"] = language
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(url, headers=headers, data=data, files=files)
        r.raise_for_status()
        j = r.json()
        # API надає поле "text"
        return j.get("text") or j

async def analyze_notes_text(concatenated_text: str) -> Dict:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    payload = {
        "model": ANALYZE_MODEL,
        "messages": [
            {"role": "system", "content": "Ти корисний аналітик нотаток."},
            {"role": "user", "content": ANALYZE_PROMPT + "\n\nТекст нотаток:\n" + concatenated_text}
        ],
        "temperature": 0.2
    }
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except Exception:
        start = content.find("{"); end = content.rfind("}")
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
            title = t.get("title","").strip() or "(без назви)"
            due = f" — *до {t['due']}*" if t.get("due") else ""
            owner = f" (відп.: {t['owner']})" if t.get("owner") else ""
            prio = f" [{t.get('priority','med')}]"
            lines.append(f"- {title}{due}{owner}{prio}")
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
