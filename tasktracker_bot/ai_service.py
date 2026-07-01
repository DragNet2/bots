"""
Task Tracker - AI Service using Groq
"""
import json
import httpx
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from config import GROQ_API_KEY, GROQ_MODEL


SYSTEM_PROMPT = """Ты - ассистент для создания задач. Тебе приходит сообщение от пользователя,
и тебе нужно извлечь из него структурированную задачу.

Верни JSON с полями:
- title: краткое название задачи (до 100 символов)
- description: подробное описание задачи (может быть пустым)
- priority: приоритет (low, medium, high)

Примеры:
- "Добавить кнопку логина" -> {"title": "Добавить кнопку логина", "description": "", "priority": "medium"}
- "СРОЧНО! Исправить баг с авторизацией на продакшене" -> {"title": "Исправить баг с авторизацией", "description": "Срочно на продакшене", "priority": "high"}
- "Хочу чтобы бот присылал уведомления в телегу" -> {"title": "Уведомления в Telegram", "description": "Бот должен присылать уведомления", "priority": "medium"}

Верни ТОЛЬКО JSON, без markdown разметки, без объяснений."""


async def analyze_message(message: str) -> Optional[dict]:
    """Analyze message and extract task using Groq API."""
    if not GROQ_API_KEY:
        return None

    url = f"https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message}
        ],
        "temperature": 0.3
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()

            content = result["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]

            return json.loads(content.strip())
    except Exception as e:
        print(f"Groq API error: {e}")
        return None


def parse_priority(priority_str: str) -> str:
    """Parse priority string to standard format."""
    p = priority_str.lower().strip()
    if p in ["high", "h", "высокий", "срочно", "urgent"]:
        return "high"
    elif p in ["low", "l", "низкий"]:
        return "low"
    return "medium"