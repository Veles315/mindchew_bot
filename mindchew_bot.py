import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

import openai
from aiohttp import web
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

# ────────────────────────────────────────────────
#  ЛОГИРОВАНИЕ
# ────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────
#  КОНФИГУРАЦИЯ
# ────────────────────────────────────────────────
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")          # например https://your-domain.com
PORT = int(os.getenv("PORT", "10000"))

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError("TELEGRAM_TOKEN и OPENAI_API_KEY обязательны в .env")

openai.api_key = OPENAI_API_KEY

HISTORY_FILE = "user_history.json"
REMINDERS_FILE = "user_reminders.json"
SUBSCRIPTIONS_FILE = "subscriptions.json"

FREE_MESSAGE_LIMIT = 30
FREE_REMINDER_LIMIT = 1

# ────────────────────────────────────────────────
#  ХРАНИЛИЩА (в памяти + диск)
# ────────────────────────────────────────────────
def load_json_safe(filepath: str, default: Any = {}) -> Dict:
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Не удалось загрузить {filepath}: {e}. Создаём пустой.")
        return default


user_history: Dict[str, List[Dict[str, str]]] = load_json_safe(HISTORY_FILE, {})
user_reminders: Dict[str, List[Dict[str, str]]] = load_json_safe(REMINDERS_FILE, {})
subscriptions: Dict[str, Dict[str, str]] = load_json_safe(SUBSCRIPTIONS_FILE, {})

REMINDER_STATE: Dict[str, Dict[str, Any]] = {}  # временное состояние диалога напоминаний

# ────────────────────────────────────────────────
#  ПОМОЩНИКИ
# ────────────────────────────────────────────────
def save_json(filepath: str, data: Any):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения {filepath}: {e}")


def save_history():
    save_json(HISTORY_FILE, user_history)


def save_reminders():
    save_json(REMINDERS_FILE, user_reminders)


def save_subscriptions():
    save_json(SUBSCRIPTIONS_FILE, subscriptions)


def is_subscribed(user_id: str) -> bool:
    sub = subscriptions.get(user_id)
    if not sub:
        return False
    expire_str = sub.get("until")
    try:
        expire_dt = datetime.strptime(expire_str, "%Y-%m-%d")
        return expire_dt >= datetime.now().date()
    except:
        return False


def count_active_reminders(user_id: str) -> int:
    now = datetime.now()
    reminders = user_reminders.get(user_id, [])
    active = [r for r in reminders if datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M") > now]
    return len(active)


# ────────────────────────────────────────────────
#  OpenAI вызов (асинхронный через поток)
# ────────────────────────────────────────────────
async def call_openai(messages: List[Dict[str, str]], model: str = "gpt-4o-mini") -> Any:
    def sync_call():
        return openai.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.75,
            max_tokens=1200,
        )

    return await asyncio.to_thread(sync_call)


# ────────────────────────────────────────────────
#  КОМАНДЫ И ХЕНДЛЕРЫ
# ────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🧠 Анализ личности", callback_data="analyze_personality")],
        [InlineKeyboardButton("📝 Мои напоминания", callback_data="my_reminders")],
        [InlineKeyboardButton("💳 Подписка Boosty", url="https://boosty.to/birukov-systems/...")],
        [InlineKeyboardButton("♻️ Сбросить историю", callback_data="reset_history")],
    ]

    text = (
        "🧠 Привет! Я — MindChewBot.\n\n"
        "Напиши всё, что крутится в голове — тревоги, мысли, планы, сомнения.\n"
        "Помогу разобраться, навести порядок, посмотреть на твою личность со стороны."
    )

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def show_reminders_list(user_id: str, query):
    reminders = user_reminders.get(user_id, [])
    keyboard = []

    if reminders:
        for r in reminders:
            dt = r["datetime"]
            txt = r["text"][:24] + "…" if len(r["text"]) > 24 else r["text"]
            keyboard.append([InlineKeyboardButton(f"{dt} — {txt}", callback_data=f"edit_reminder_{r['id']}")])

    keyboard.append([InlineKeyboardButton("➕ Новое напоминание", callback_data="set_reminder")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])

    text = "📋 Твои напоминания:" if reminders else "📭 Активных напоминаний пока нет."

    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def analyze_personality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    history = user_history.get(user_id, [])

    await query.answer()

    if not history:
        await query.message.reply_text("Напиши мне хотя бы несколько сообщений — тогда я смогу проанализировать.")
        return

    await query.message.chat.send_action("typing")

    # Только сообщения пользователя + последние 20
    user_msgs = [h["content"] for h in history if h["role"] == "user"][-20:]

    if not user_msgs:
        await query.message.reply_text("Не нашёл твоих сообщений для анализа…")
        return

    prompt = """Ты психолог и типолог (MBTI + общие паттерны поведения).
Проанализируй сообщения пользователя ниже.

Сначала укажи **наиболее вероятный тип MBTI** (4 буквы) и уверенность: высокая / средняя / низкая.

Затем разбери по шкалам:
• I/E —
• N/S —
• T/F —
• J/P —

Далее:
1. Основные сильные стороны
2. Типичные слабые стороны / слепые зоны
3. Возможные внутренние конфликты ("затыки")
4. 2–4 конкретных совета

Стиль: честный, доброжелательный, без воды.

Сообщения (сверху — самые новые):

""" + "\n───\n".join(user_msgs)

    messages = [
        {"role": "system", "content": "Ты точный и наблюдательный психолог."},
        {"role": "user", "content": prompt},
    ]

    try:
        response = await call_openai(messages)
        text = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI ошибка в анализе личности: {e}", exc_info=True)
        text = "⚠️ Не удалось выполнить анализ — ошибка API. Попробуй позже."

    await query.message.reply_text(text, parse_mode="Markdown")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data

    await query.answer()

    if data == "analyze_personality":
        await analyze_personality(update, context)

    elif data == "reset_history":
        if user_id in user_history:
            del user_history[user_id]
            save_history()
        await query.message.reply_text("♻️ История диалога очищена.")

    elif data == "my_reminders":
        await show_reminders_list(user_id, query)

    elif data == "set_reminder":
        if not is_subscribed(user_id) and count_active_reminders(user_id) >= FREE_REMINDER_LIMIT:
            await query.message.reply_text(
                f"🚫 Достигнут лимит бесплатных напоминаний ({FREE_REMINDER_LIMIT}).\n"
                "Подписка: [Boosty](https://boosty.to/...)",
                parse_mode="Markdown"
            )
            return

        REMINDER_STATE[user_id] = {"step": "date"}
        today = datetime.now().date()
        buttons = []
        for i in range(7):
            d = today + timedelta(days=i)
            buttons.append([InlineKeyboardButton(d.strftime("%Y-%m-%d"), callback_data=f"reminder_date_{d.isoformat()}")])

        await query.message.reply_text("📅 Выбери дату:", reply_markup=InlineKeyboardMarkup(buttons))

    # ── Остальные обработчики дат/времени/текста напоминаний ──
    # (оставил как было, но можно вынести в отдельную функцию при желании)
    elif data.startswith("reminder_date_"):
        REMINDER_STATE[user_id]["date"] = data[13:]
        REMINDER_STATE[user_id]["step"] = "hour"
        hours = [InlineKeyboardButton(f"{h:02d}", callback_data=f"reminder_hour_{h}") for h in range(24)]
        keyboard = [hours[i:i+6] for i in range(0, 24, 6)]
        await query.message.reply_text("🕒 Час:", reply_markup=InlineKeyboardMarkup(keyboard))

    # ... (дальше аналогично твоему коду для hour, minute, edit и т.д.)

    elif data == "back_to_menu":
        await start(update, context)

    else:
        await query.message.reply_text(f"Неизвестная команда: {data}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    text = update.message.text.strip()

    state = REMINDER_STATE.get(user_id, {})

    # ── Процесс создания/редактирования напоминания ──
    if state.get("step") == 3:  # ожидание текста напоминания
        # ... (твоя логика установки напоминания — оставь как есть)
        # после успешной установки:
        REMINDER_STATE.pop(user_id, None)
        return

    # ── Обычный диалог с GPT ──
    history = user_history.setdefault(user_id, [])

    history.append({"role": "user", "content": text})
    user_history[user_id] = history[-50:]  # лимит по количеству

    if not is_subscribed(user_id) and len([m for m in history if m["role"] == "user"]) >= FREE_MESSAGE_LIMIT:
        await update.message.reply_text(
            f"🚫 Лимит бесплатных сообщений ({FREE_MESSAGE_LIMIT}) исчерпан.\n"
            "Продолжить можно по [подписке](https://boosty.to/...)",
            parse_mode="Markdown"
        )
        return

    await update.message.chat.send_action("typing")

    try:
        resp = await call_openai(history)
        reply = resp.choices[0].message.content
    except Exception as e:
        logger.error(f"OpenAI ошибка: {e}", exc_info=True)
        reply = "⚠️ Ошибка связи с моделью. Попробуй позже."

    history.append({"role": "assistant", "content": reply})
    save_history()

    await update.message.reply_text(reply)


# ────────────────────────────────────────────────
#  WEBHOOK + ЗАПУСК
# ────────────────────────────────────────────────
async def handle_webhook(request):
    data = await request.json()
    app = request.app["telegram_app"]
    update = Update.de_json(data, app.bot)
    await app.update_queue.put(update)
    return web.Response(status=200)


async def root(request):
    return web.Response(text="MindChewBot webhook is running")


async def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()

    # webhook
    await app.bot.set_webhook(f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")

    web_app = web.Application()
    web_app["telegram_app"] = app
    web_app.router.add_post(f"/{TELEGRAM_TOKEN}", handle_webhook)
    web_app.router.add_get("/", root)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"🚀 Bot webhook запущен на порту {PORT}")

    try:
        await asyncio.sleep(3600 * 24 * 365)  # почти вечность
    except asyncio.CancelledError:
        pass
    finally:
        await app.stop()
        await app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
