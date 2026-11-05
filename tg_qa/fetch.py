"""Выгрузка чатов хакатона из Telegram в markdown (только чтение).

Сессию заводит tg_qa/login.py. Скрипт ничего не отправляет и не
отмечает прочитанным: receive_updates=False, только iter_messages.

    python tg_qa/fetch.py --list                    # чаты и топики, похожие на хакатон
    python tg_qa/fetch.py --list --keywords dev,ml  # свои слова для поиска чатов
    python tg_qa/fetch.py                           # догрузить всё из sources.json
    python tg_qa/fetch.py --out docs/qa/raw --since 2026-07-01

Источники перечислены в tg_qa/sources.json (образец: sources.example.json):
    [{"name": "track-chat", "chat": -100123, "topic": 45, "title": "..."}]
topic — id форумного топика (null для обычного чата). Догрузка
инкрементальная: последний id каждого источника лежит в <out>/.state.json.
Сообщения админов чата помечаются [ОРГ] — по ним и собирается Q&A.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from telethon import TelegramClient, functions
from telethon.tl.types import ChannelParticipantsAdmins

_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_DIR)
load_dotenv(os.path.join(_DIR, ".env"))
SESSION_PATH = os.path.join(_DIR, ".session", "hackathon")
SOURCES_PATH = os.path.join(_DIR, "sources.json")
DEFAULT_OUT = os.path.join(_REPO, "qa", "raw")
DEFAULT_KEYWORDS = "хакат,hack"


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def _client():
    c = TelegramClient(SESSION_PATH, int(os.environ["TG_API_ID"]),
                       os.environ["TG_API_HASH"], receive_updates=False)
    await c.connect()
    if not await c.is_user_authorized():
        sys.exit("Сессии нет или она отозвана: запусти tg_qa/login.py")
    return c


async def _topics(c, entity):
    """Форумные топики чата: [(id, title)]. Пусто, если чат не форум."""
    if not getattr(entity, "forum", False):
        return []
    # запрос переезжал между channels.* и messages.* в разных слоях API
    req = getattr(functions.messages, "GetForumTopicsRequest", None) \
        or functions.channels.GetForumTopicsRequest
    res = await c(req(entity, offset_date=None, offset_id=0, offset_topic=0, limit=100))
    return [(t.id, getattr(t, "title", "")) for t in res.topics]


async def list_sources(keywords):
    c = await _client()
    async for d in c.iter_dialogs(limit=500):
        title = d.title or ""
        if not any(k in title.lower() for k in keywords):
            continue
        print(f"{d.id}\t{title}")
        for tid, ttitle in await _topics(c, d.entity):
            print(f"    topic {tid}\t{ttitle}")
    await c.disconnect()


async def _admin_ids(c, entity):
    try:
        return {u.id async for u in c.iter_participants(entity, filter=ChannelParticipantsAdmins)}
    except Exception:
        return set()  # без прав на список админов метки [ОРГ] не будет


def _link(chat_id, topic, msg_id):
    internal = str(chat_id).removeprefix("-100")
    return f"https://t.me/c/{internal}/{topic}/{msg_id}" if topic else f"https://t.me/c/{internal}/{msg_id}"


async def fetch(out_dir, since):
    sources = _load_json(SOURCES_PATH, [])
    if not sources:
        sys.exit("sources.json пуст: сначала --list и впиши нужные чаты/топики")
    state_path = os.path.join(out_dir, ".state.json")
    state = _load_json(state_path, {})
    c = await _client()
    for s in sources:
        entity = await c.get_entity(s["chat"])
        admins = await _admin_ids(c, entity)
        key = s["name"]
        last = state.get(key, 0)
        # General-топик форума (id 1) не адресуется через reply_to: его сообщения
        # без пометки топика, поэтому идём по всему чату и отсекаем остальные топики
        general = s.get("topic") == 1
        kwargs = {"reply_to": s["topic"]} if s.get("topic") and not general else {}
        rows = []
        async for m in c.iter_messages(entity, min_id=last, reverse=True, **kwargs):
            # форум хакатона часто общий для всех сезонов: старше since не берём
            if not (m.message or m.media) or (since and m.date < since):
                continue
            if general and m.reply_to and m.reply_to.forum_topic:
                continue
            sender = await m.get_sender()
            name = " ".join(filter(None, [getattr(sender, "first_name", None),
                                          getattr(sender, "last_name", None)])) \
                or getattr(sender, "title", None) or "?"
            org = " [ОРГ]" if m.sender_id in admins else ""
            reply = f" ↩ {m.reply_to.reply_to_msg_id}" if m.reply_to and m.reply_to.reply_to_msg_id \
                and m.reply_to.reply_to_msg_id != s.get("topic") else ""
            media = " 📎" if m.media and not m.message else ""
            ts = m.date.astimezone().strftime("%Y-%m-%d %H:%M")
            rows.append(f"### {ts} · {name}{org} · [#{m.id}]({_link(s['chat'], s.get('topic'), m.id)}){reply}{media}\n\n"
                        # m.text — с markdown-разметкой: ссылки, спрятанные в слова, не теряются
                        f"{(m.text or '').strip()}\n")
            last = max(last, m.id)
        path = os.path.join(out_dir, f"{key}.md")
        new_file = not os.path.exists(path)
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            if new_file:
                f.write(f"# {s.get('title', key)}\n\nСырая выгрузка, только чтение. "
                        f"Не редактировать руками: дописывается tg_qa/fetch.py.\n\n")
            f.write("\n".join(rows))
        state[key] = last
        print(f"{key}: +{len(rows)} сообщений (last id {last})")
    _save_json(state_path, state)
    await c.disconnect()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true", help="показать чаты и топики по ключевым словам")
    p.add_argument("--keywords", default=DEFAULT_KEYWORDS,
                   help="слова в названии чата для --list, через запятую")
    p.add_argument("--out", default=DEFAULT_OUT, help="куда класть выгрузку")
    p.add_argument("--since", help="не брать сообщения старше даты, ГГГГ-ММ-ДД")
    args = p.parse_args()
    if args.list:
        asyncio.run(list_sources([k.strip().lower() for k in args.keywords.split(",") if k.strip()]))
    else:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc) if args.since else None
        asyncio.run(fetch(os.path.abspath(args.out), since))
