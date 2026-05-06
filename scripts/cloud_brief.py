#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = ROOT / "archive"
BODY_FILE = ROOT / "newsbrief_body.txt"
SEEN_FILE = DATA_DIR / "seen.json"
CATEGORY_HISTORY_FILE = DATA_DIR / "category_history.json"

CATEGORIES = [
    "AI Core / AI 核心动态",
    "AI x Marketing / AI 与营销",
    "Marketing / 市场营销",
    "Tech Macro / 科技宏观",
    "Geopolitics & Econ / 地缘政治与经济",
    "Culture & Creator Economy / 文化与创作者经济",
    "Fashion & Film / 时尚与电影",
    "Psychology & Healing / 心理与疗愈",
    "Healthcare Consumer Goods / 医疗消费品",
]


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s)>\]]+", text)
    return [url.rstrip(".,;") for url in urls]


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_prompt(today: str, seen: list[dict], category_history: list[dict]) -> str:
    return f"""
Create today's personal bilingual news brief for Vivianding.

Today: {today}
Recipient: vivianding98@gmail.com

Requirements:
- Search current news using web search.
- Produce up to 10 stories across these 9 categories:
  {", ".join(CATEGORIES)}
- Prefer 10 stories when enough qualifying stories exist.
- Include Marketing, Fashion & Film, Psychology & Healing, and Healthcare Consumer Goods when qualifying stories exist.
- Cover both China-related and international news across the brief.
- Use only stories from the last 48 hours.
- Do not repeat URLs or near-duplicate topics from the recent seen cache.
- Every reader-facing line must be bilingual: English first, then Chinese.
- Cite source name and URL for every story.
- Neutral reporting tone. No persona, no script, no CTA.

Recent seen cache:
{json.dumps(seen[-80:], ensure_ascii=False)}

Recent category history:
{json.dumps(category_history[-14:], ensure_ascii=False)}

Output only the email body in this exact style:

10 stories for today / 今日 10 条新闻 — categories / 栏目: [English category list] / [中文栏目列表].

━━━ [ENGLISH CATEGORY NAME] / [中文栏目名] ━━━
1. [English headline]
   中文标题: [Chinese headline]
   Source / 来源: [Source] · [URL] · [X] hours ago / [X] 小时前
   Summary: 2-3 English sentences.
   中文摘要: 2-3 Chinese sentences.
   Why it matters: 1 specific English sentence.
   为什么重要: 1 specific Chinese sentence.

Skipped today / 今日略过: None / 无, or a bilingual explanation.
""".strip()


def generate_body(today: str, seen: list[dict], category_history: list[dict]) -> str:
    client = OpenAI()
    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5"),
        reasoning={"effort": "low"},
        tools=[
            {
                "type": "web_search",
                "user_location": {
                    "type": "approximate",
                    "country": "US",
                    "region": "California",
                    "city": "Los Angeles",
                    "timezone": "America/Los_Angeles",
                },
            }
        ],
        input=build_prompt(today, seen, category_history),
    )
    return response.output_text.strip()


def send_email(subject: str, body: str) -> None:
    smtp_user = os.environ["NEWSBRIEF_SMTP_USER"]
    smtp_pass = os.environ["NEWSBRIEF_SMTP_PASS"]
    to = os.environ.get("NEWSBRIEF_TO", "vivianding98@gmail.com")
    recipients = [addr.strip() for addr in to.split(",") if addr.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipients, msg.as_string())


def update_state(today: str, body: str, seen: list[dict], category_history: list[dict]) -> None:
    cutoff = datetime.fromisoformat(today) - timedelta(days=7)
    kept_seen = [
        item for item in seen
        if item.get("date") and datetime.fromisoformat(item["date"]) >= cutoff
    ]

    for url in extract_urls(body):
        kept_seen.append({"url": url, "headline_hash": short_hash(url), "date": today})

    history_cutoff = datetime.fromisoformat(today) - timedelta(days=14)
    kept_history = [
        item for item in category_history
        if item.get("date") and datetime.fromisoformat(item["date"]) >= history_cutoff
    ]

    used = [cat for cat in CATEGORIES if cat.split(" / ")[0].upper() in body.upper()]
    kept_history.append({"date": today, "categories": used})

    save_json(SEEN_FILE, kept_seen)
    save_json(CATEGORY_HISTORY_FILE, kept_history)


def main() -> int:
    required = ["OPENAI_API_KEY", "NEWSBRIEF_SMTP_USER", "NEWSBRIEF_SMTP_PASS", "NEWSBRIEF_TO"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"Missing required secrets: {', '.join(missing)}", file=sys.stderr)
        return 1

    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)

    today = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    seen = load_json(SEEN_FILE, [])
    category_history = load_json(CATEGORY_HISTORY_FILE, [])

    body = generate_body(today, seen, category_history)
    BODY_FILE.write_text(body + "\n", encoding="utf-8")

    archive_file = ARCHIVE_DIR / f"{today}.md"
    archive_file.write_text(body + "\n", encoding="utf-8")

    send_email(f"News Brief — {today}", body)
    update_state(today, body, seen, category_history)

    print(f"News Brief sent to {os.environ['NEWSBRIEF_TO']} — archive: {archive_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
