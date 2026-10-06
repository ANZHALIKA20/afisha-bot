""" Следит за афишей Белорусского театра кукол и пишет в Telegram,
когда появляется новая дата спектакля «Записки юного врача»."""
import datetime
import json
import os
import re
import sys

import requests
from bs4 import BeautifulSoup

URL = "https://puppet-minsk.by/bilety/afisha"
KEYWORD = "юного врача"  # по какому слову ищем спектакль
STATE_FILE = "state.json"
FAILS_BEFORE_ALERT = 6  # сколько проверок подряд могут упасть, прежде чем напишем в Telegram

TOKEN = os.environ.get("TG_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": "true"},
        timeout=30,
    )
    if not r.ok:
        print("Telegram ответил ошибкой:", r.status_code, r.text)
    r.raise_for_status()


def fetch_events():
    """Возвращает {id_сеанса: {title, date, url}} для всех спектаклей афиши."""
    r = requests.get(
        URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
        timeout=30,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")
    events = {}
    for a in soup.find_all("a", href=True):
        if "tce.by/shows" not in a["href"]:
            continue
        m = re.search(r"data=(\d+)", a["href"])
        if not m:
            continue
        row = a.find_parent("tr")
        row_text = row.get_text(" ", strip=True) if row else ""
        d = re.search(r"\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2}", row_text)
        events[m.group(1)] = {
            "title": a.get_text(" ", strip=True),
            "date": d.group(0) if d else "дата не определена",
            "url": a["href"],
        }
    return events


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def main():
    state = load_state()
    state["heartbeat"] = datetime.date.today().isoformat()  # раз в сутки, чтобы GitHub не «усыпил» проект

    try:
        events = fetch_events()
    except Exception as e:  # сайт недоступен или заблокировал запрос
        print("Не удалось открыть афишу:", repr(e))
        state["fails"] = state.get("fails", 0) + 1
        if state["fails"] == FAILS_BEFORE_ALERT:
            send("⚠️ Бот уже около часа не может открыть афишу театра. "
                 "Проверь вручную: " + URL)
        save_state(state)
        return
    state["fails"] = 0

    if not events:
        print("В афише не найдено ни одного спектакля — возможно, сайт изменился.")
        if not state.get("warned_empty"):
            send("⚠️ Бот открыл афишу, но не увидел в ней спектаклей. "
                 "Возможно, сайт изменился. Проверь вручную: " + URL)
            state["warned_empty"] = True
        save_state(state)
        return
    state["warned_empty"] = False

    target = {k: v for k, v in events.items() if KEYWORD in v["title"].lower()}
    first_run = "known" not in state
    known = set(state.get("known", []))
    new = sorted((k for k in target if k not in known), key=lambda k: target[k]["date"][6:10] + target[k]["date"][3:5] + target[k]["date"][0:2])

    if first_run:
        if target:
            lines = [f"• {v['date']}" for v in sorted(target.values(), key=lambda v: v["date"][6:10] + v["date"][3:5] + v["date"][0:2])]
            send("✅ Бот запущен и следит за афишей.\n"
                 f"Сейчас в афише «Записки юного врача»: {len(target)} дат(ы):\n"
                 + "\n".join(lines)
                 + "\n\nКак только появится новая дата — я напишу сюда.")
        else:
            send("✅ Бот запущен и следит за афишей. "
                 "Сейчас «Записок юного врача» в афише нет — напишу, как только появятся.")
    else:
        for k in new:
            v = target[k]
            send(f"🎭 Новая дата: {v['title']}\n"
                 f"📅 {v['date']}\n"
                 f"🎟 Купить: {v['url']}\n\n"
                 "Не забудь: в личном кабинете tce.by выставить счёт и применить промокод из любых 5 цифр.")
            print("Отправлено уведомление:", v["date"])

    state["known"] = sorted(known | set(target))
    save_state(state)
    print(f"Спектаклей в афише: {len(events)}, «Записок юного врача»: {len(target)}, новых: {len(new)}")


if __name__ == "__main__":
    main()
