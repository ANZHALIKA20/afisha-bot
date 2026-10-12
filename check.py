"""
Следит за афишей Белорусского театра кукол и пишет в Telegram,
когда появляется новая дата спектакля «Записки юного врача».
(Версия под новый сайт театра: афиша по адресу /afisha)
"""
import datetime
import json
import os
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://puppet-minsk.by/afisha"
SHOW_SLUG = "zapiski-yunogo-vracha"  # часть ссылки на спектакль; для другого спектакля поменяй
SHOW_TITLE = "юного врача"            # часть названия (маленькими буквами), нужна для самопроверки
STATE_FILE = "state.json"
FAILS_BEFORE_ALERT = 6  # сколько проверок подряд могут упасть, прежде чем напишем в Telegram

TOKEN = os.environ.get("TG_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# График 2 через 2: 2 рабочих дня, 2 выходных. Отсчёт от 6 октября 2026 (рабочий день).
SHIFT_START = datetime.date(2026, 10, 6)

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
DATE_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")", re.IGNORECASE)
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def work_status(date_str):
    """'28.11.2026 19:00' -> строка про то, рабочий день или выходной."""
    try:
        d = datetime.datetime.strptime(date_str[:10], "%d.%m.%Y").date()
    except ValueError:
        return ""
    if (d - SHIFT_START).days % 4 in (0, 1):
        return "💼 В этот день у тебя РАБОТА ❌"
    return "🟢 В этот день у тебя ВЫХОДНОЙ ✅"


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": "true"},
        timeout=30,
    )
    if not r.ok:
        print("Telegram ответил ошибкой:", r.status_code, r.text)
    r.raise_for_status()


def guess_year(day, month, today):
    """На сайте года нет: берём ближайший год, при котором дата не в далёком прошлом."""
    for year in (today.year, today.year + 1):
        try:
            d = datetime.date(year, month, day)
        except ValueError:
            continue
        if d >= today - datetime.timedelta(days=45):
            return year
    return today.year


def make_event(day, month, hh, mm, link, today):
    year = guess_year(int(day), MONTHS[month.lower()], today)
    dt = datetime.datetime(year, MONTHS[month.lower()], int(day), int(hh), int(mm))
    return dt.strftime("%Y-%m-%d %H:%M"), {
        "date": dt.strftime("%d.%m.%Y %H:%M"),
        "url": link,
    }


def parse_events(html, today=None):
    """Возвращает {'2026-10-20 19:00': {date, url}} для нужного спектакля."""
    today = today or datetime.date.today()
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()

    anchors = [a for a in soup.find_all("a", href=True) if SHOW_SLUG in a["href"]]
    events = {}

    # Способ 1: ищем ближайший блок вокруг ссылки, где ровно одна дата и одно время
    unresolved = set()
    for a in anchors:
        link = urljoin(URL, a["href"])
        found = None
        for parent in a.parents:
            if parent.name in ("body", "html", "[document]"):
                break
            text = parent.get_text(" ", strip=True)
            dates = DATE_RE.findall(text)
            if not dates:
                continue
            times = TIME_RE.findall(text)
            if len(dates) == 1 and len(times) == 1:
                found = (dates[0], times[0])
            break  # блок с датами найден; если он неоднозначный — пойдём способом 2
        if found:
            (day, month), (hh, mm) = found
            key, ev = make_event(day, month, hh, mm, link, today)
            events.setdefault(key, ev)
        else:
            unresolved.add(id(a))

    # Способ 2: идём по странице сверху вниз, запоминаем последнюю дату и время перед ссылкой
    if unresolved:
        last_date = last_time = None
        for el in soup.descendants:
            if isinstance(el, str):
                d = DATE_RE.findall(el)
                if d:
                    last_date, last_time = d[-1], None
                t = TIME_RE.findall(el)
                if t and last_date:
                    last_time = t[-1]
            elif getattr(el, "name", None) == "a" and id(el) in unresolved:
                if last_date and last_time:
                    (day, month), (hh, mm) = last_date, last_time
                    key, ev = make_event(day, month, hh, mm, urljoin(URL, el["href"]), today)
                    events.setdefault(key, ev)

    page_text = soup.get_text(" ", strip=True).lower()
    return events, page_text


def fetch_events():
    r = requests.get(
        URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
        timeout=30,
    )
    r.raise_for_status()
    return parse_events(r.content)


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
        target, page_text = fetch_events()
    except Exception as e:  # сайт недоступен или заблокировал запрос
        print("Не удалось открыть афишу:", repr(e))
        state["fails"] = state.get("fails", 0) + 1
        if state["fails"] == FAILS_BEFORE_ALERT:
            send("⚠️ Бот уже около часа не может открыть афишу театра. "
                 "Проверь вручную: " + URL)
        save_state(state)
        return
    state["fails"] = 0

    # Самопроверка: на странице есть название спектакля, а даты не нашлись — значит, сайт снова поменялся
    if not target and SHOW_TITLE in page_text:
        print("Название спектакля есть на странице, но даты не найдены — вероятно, сайт изменился.")
        if not state.get("warned_empty"):
            send("⚠️ Бот открыл афишу и видит спектакль, но не может прочитать даты. "
                 "Возможно, сайт снова изменился. Проверь вручную: " + URL)
            state["warned_empty"] = True
        save_state(state)
        return
    state["warned_empty"] = False

    # Старый формат state.json (номера сеансов с tce.by) — начинаем заново, без лишних уведомлений
    legacy = any(str(k).isdigit() for k in state.get("known", []))
    first_run = "known" not in state or legacy
    known = set() if legacy else set(state.get("known", []))
    new = sorted(k for k in target if k not in known)

    if first_run:
        intro = ("✅ Бот обновлён: сайт театра поменял адрес афиши, теперь слежу за новым."
                 if legacy else "✅ Бот запущен и следит за афишей.")
        if target:
            lines = [f"• {target[k]['date']}" for k in sorted(target)]
            send(intro + "\n"
                 f"Сейчас в афише «Записки юного врача»: {len(target)} дат(ы):\n"
                 + "\n".join(lines)
                 + "\n\nКак только появится новая дата — я напишу сюда.")
        else:
            send(intro + " Сейчас «Записок юного врача» в афише нет — напишу, как только появятся.")
    else:
        for k in new:
            v = target[k]
            send(f"🎭 Новая дата: Записки юного врача\n"
                 f"📅 {v['date']}\n"
                 f"{work_status(v['date'])}\n"
                 f"🎟 Купить: {v['url']}\n\n"
                 "Раньше для оплаты нужно было вручную выставить счёт в личном кабинете "
                 "и применить промокод из любых 5 цифр — проверь, актуально ли.")
            print("Отправлено уведомление:", v["date"])

    state["known"] = sorted(known | set(target))
    save_state(state)
    print(f"«Записок юного врача» в афише: {len(target)}, новых: {len(new)}")


if __name__ == "__main__":
    main()
