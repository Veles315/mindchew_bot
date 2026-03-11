import os
import json
import openai
import asyncio
import logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
from aiohttp import web
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters
)
from dotenv import load_dotenv

# 🔑 ВСТАВЬ СВОИ КЛЮЧИ
load_dotenv()  # загружает переменные из .env в окружение

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
openai.api_key = os.getenv("OPENAI_API_KEY")

HISTORY_FILE = "user_history.json"
REMINDERS_FILE = "user_reminders.json"
FREE_MESSAGE_LIMIT = 30
FREE_REMINDER_LIMIT = 1
REMINDER_STATE = {}

def load_json_safe(filepath):
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        print(f"⚠️ Не удалось загрузить {filepath}, создаю новый.")
        return {}
SUBSCRIPTIONS_FILE = "subscriptions.json"  # <-- вот это обязательно

user_history = load_json_safe(HISTORY_FILE)
user_reminders = load_json_safe(REMINDERS_FILE)
subscriptions = load_json_safe(SUBSCRIPTIONS_FILE)


if os.path.exists(REMINDERS_FILE):
    with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
        user_reminders = json.load(f)
else:
    user_reminders = {}

SUBSCRIPTIONS_FILE = "subscriptions.json"
if os.path.exists(SUBSCRIPTIONS_FILE):
    with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
        subscriptions = json.load(f)
else:
    subscriptions = {}

def save_history():
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(user_history, f, ensure_ascii=False, indent=2)

def is_subscribed(user_id):
    sub = subscriptions.get(str(user_id))
    if not sub:
        return False
    expire_str = sub.get("until")
    try:
        expire_dt = datetime.strptime(expire_str, "%Y-%m-%d")
        return expire_dt >= datetime.now()
    except:
        return False

def save_subscriptions():
    with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(subscriptions, f, ensure_ascii=False, indent=2)

def save_reminders():
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(user_reminders, f, ensure_ascii=False, indent=2)

def count_active_reminders(user_id):
    now_ts = datetime.now().timestamp()
    reminders = user_reminders.get(user_id, [])
    active = [r for r in reminders if datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M").timestamp() > now_ts]
    return len(active)

async def send_reminder_later(bot, chat_id, text, delay, user_id, reminder_id):
    logging.info(f"Reminder will fire in {delay} seconds for chat {chat_id}")
    await asyncio.sleep(delay)
    logging.info(f"Sending reminder to chat {chat_id}")
    await bot.send_message(chat_id=chat_id, text=f"🔔 Напоминание:\n{text}")
    if user_id in user_reminders:
        user_reminders[user_id] = [r for r in user_reminders[user_id] if r["id"] != reminder_id]
        save_reminders()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🧠 Анализ личности", callback_data="analyze_personality")],
        [InlineKeyboardButton("📝 Мои напоминания", callback_data="my_reminders")],
        [InlineKeyboardButton("💳 Подписка на Boosty", url="https://boosty.to/birukov-systems/posts/89b1960e-ceff-4f71-9b77-9040e631a7db?share=success_publish_link")],
        [InlineKeyboardButton("♻️ Сбросить историю", callback_data="reset_history")]
    ]
    text = """🧠 Привет! Я — MindChewBot.
Напиши, что у тебя в голове — всё, что тревожит или просто крутится в мыслях.

Я помогу навести порядок, покажу твой тип личности и подскажу, где могут быть внутренние затыки.

✍ Просто начни писать — как другу."""

    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_reminders_list(user_id, query):
    reminders = user_reminders.get(user_id, [])
    keyboard = []
    if reminders:
        for r in reminders:
            dt = r["datetime"]
            txt = r["text"]
            rid = r["id"]
            keyboard.append([InlineKeyboardButton(f"{dt} — {txt[:20]}...", callback_data=f"edit_reminder_{rid}")])
    keyboard.append([InlineKeyboardButton("➕ Новое напоминание", callback_data="set_reminder")])
    keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")])
    text = "📋 Твои напоминания:" if reminders else "📭 У тебя нет активных напоминаний."
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

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
        await query.message.reply_text("♻️ История сброшена.")
    elif data == "set_reminder":
        active_count = count_active_reminders(user_id)
        if not is_subscribed(user_id) and active_count >= FREE_REMINDER_LIMIT:
            await query.message.reply_text(
                f"🚫 У тебя уже есть {FREE_REMINDER_LIMIT} бесплатное активное напоминание.\n"
                "Чтобы добавить больше — оформи подписку 💳: [Boosty](https://boosty.to/birukov-systems/posts/89b1960e-ceff-4f71-9b77-9040e631a7db?share=success_publish_link)",
                parse_mode="Markdown"
            )
            return
        REMINDER_STATE[user_id] = {"step": "date"}
        today = datetime.now().date()
        buttons = []
        for i in range(7):
            day = today + timedelta(days=i)
            buttons.append([InlineKeyboardButton(day.strftime("%Y-%m-%d"), callback_data=f"reminder_date_{day.isoformat()}")])
        await query.message.reply_text("📅 Выбери дату для напоминания:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data == "my_reminders":
        await show_reminders_list(user_id, query)
    elif data.startswith("reminder_date_"):
        date_str = data[len("reminder_date_"):]
        REMINDER_STATE[user_id]["date"] = date_str
        REMINDER_STATE[user_id]["step"] = "hour"
        hours = [InlineKeyboardButton(f"{h:02d}", callback_data=f"reminder_hour_{h}") for h in range(24)]
        keyboard = [hours[i:i+6] for i in range(0, 24, 6)]
        await query.message.reply_text("🕒 Выбери час:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("reminder_hour_"):
        hour = int(data[len("reminder_hour_"):])
        REMINDER_STATE[user_id]["hour"] = hour
        REMINDER_STATE[user_id]["step"] = "minute"
        minutes = [0, 15, 30, 45]
        buttons = [InlineKeyboardButton(f"{m:02d}", callback_data=f"reminder_minute_{m}") for m in minutes]
        await query.message.reply_text("⏱ Выбери минуты:", reply_markup=InlineKeyboardMarkup([buttons]))
    elif data.startswith("reminder_minute_"):
        minute = int(data[len("reminder_minute_"):])
        REMINDER_STATE[user_id]["minute"] = minute
        REMINDER_STATE[user_id]["step"] = 3
        await query.message.reply_text("💬 Что напомнить? Введи текст напоминания:")
    elif data.startswith("edit_reminder_"):
        rid = data[len("edit_reminder_"):]
        reminders = user_reminders.get(user_id, [])
        reminder = next((r for r in reminders if r["id"] == rid), None)
        if not reminder:
            await query.message.reply_text("Напоминание не найдено.")
            return
        keyboard = [
            [InlineKeyboardButton("Изменить текст", callback_data=f"edit_text_{rid}")],
            [InlineKeyboardButton("Изменить дату и время", callback_data=f"edit_datetime_{rid}")],
            [InlineKeyboardButton("Удалить напоминание", callback_data=f"delete_reminder_{rid}")],
            [InlineKeyboardButton("Назад", callback_data="my_reminders")]
        ]
        await query.message.reply_text(
            f"Напоминание:\n{reminder['datetime']}\n{reminder['text']}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif data.startswith("delete_reminder_"):
        rid = data[len("delete_reminder_"):]
        reminders = user_reminders.get(user_id, [])
        user_reminders[user_id] = [r for r in reminders if r["id"] != rid]
        save_reminders()
        await query.message.reply_text("✅ Напоминание удалено.")
        await show_reminders_list(user_id, query)
    elif data.startswith("edit_text_"):
        rid = data[len("edit_text_"):]
        REMINDER_STATE[user_id] = {"step": "edit_text", "reminder_id": rid}
        await query.message.reply_text("Введите новый текст напоминания:")
    elif data.startswith("edit_datetime_"):
        rid = data[len("edit_datetime_"):]
        REMINDER_STATE[user_id] = {"step": "edit_datetime_date", "reminder_id": rid}
        today = datetime.now().date()
        buttons = []
        for i in range(7):
            day = today + timedelta(days=i)
            buttons.append([InlineKeyboardButton(day.strftime("%Y-%m-%d"), callback_data=f"edit_date_{rid}_{day.isoformat()}")])
        await query.message.reply_text("Выберите новую дату:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("edit_date_"):
        parts = data.split("_")
        rid = parts[2]
        new_date = parts[3]
        REMINDER_STATE[user_id]["new_date"] = new_date
        REMINDER_STATE[user_id]["step"] = "edit_datetime_hour"
        hours = [InlineKeyboardButton(f"{h:02d}", callback_data=f"edit_hour_{rid}_{h}") for h in range(24)]
        keyboard = [hours[i:i+6] for i in range(0, 24, 6)]
        await query.message.reply_text("Выберите новый час:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("edit_hour_"):
        parts = data.split("_")
        rid = parts[2]
        new_hour = int(parts[3])
        REMINDER_STATE[user_id]["new_hour"] = new_hour
        REMINDER_STATE[user_id]["step"] = "edit_datetime_minute"
        minutes = [0, 15, 30, 45]
        buttons = [InlineKeyboardButton(f"{m:02d}", callback_data=f"edit_minute_{rid}_{m}") for m in minutes]
        await query.message.reply_text("Выберите новые минуты:", reply_markup=InlineKeyboardMarkup([buttons]))
    elif data.startswith("edit_minute_"):
        parts = data.split("_")
        rid = parts[2]
        new_minute = int(parts[3])
        state = REMINDER_STATE.get(user_id)
        new_date = state.get("new_date")
        new_hour = state.get("new_hour")
        dt_str = f"{new_date} {new_hour:02d}:{new_minute:02d}"
        reminders = user_reminders.get(user_id, [])
        for r in reminders:
            if r["id"] == rid:
                r["datetime"] = dt_str
                break
        save_reminders()
        REMINDER_STATE.pop(user_id, None)
        await query.message.reply_text("✅ Дата и время напоминания обновлены.")
        await show_reminders_list(user_id, query)
    elif data == "back_to_menu":
        await start(update, context)
    else:
        await query.message.reply_text(f"Неизвестная команда: {data}")

async def call_openai(history):
    def sync_call():
        return openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=history
        )
    return await asyncio.to_thread(sync_call)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    text = update.message.text.strip()

    # Проверяем, не находится ли пользователь в процессе создания напоминания (шаг 3 — ввод текста)
    state = REMINDER_STATE.get(user_id)
    if state and isinstance(state, dict) and state.get("step") == 3:
        date = state["date"]
        hour = state["hour"]
        minute = state["minute"]
        dt_str = f"{date} {hour:02d}:{minute:02d}"
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        delay = (dt - datetime.now()).total_seconds()

        if delay <= 0:
            await update.message.reply_text("❗ Дата и время уже прошли. Выберите будущее время.")
            return

        reminder = {
            "id": str(datetime.now().timestamp()),
            "datetime": dt_str,
            "text": text,
        }
        user_reminders.setdefault(user_id, []).append(reminder)
        save_reminders()

        # Запускаем отложенную отправку напоминания
        asyncio.create_task(send_reminder_later(context.bot, update.message.chat.id, text, delay, user_id, reminder["id"]))

        await update.message.reply_text(f"✅ Напоминание установлено на {dt_str}.")

        # Очищаем состояние напоминания для пользователя
        REMINDER_STATE.pop(user_id, None)
        return  # Прекращаем обработку, не идём дальше к OpenAI

    # Если не в процессе установки напоминания — работаем с историей и GPT

    history = user_history.get(user_id, [])
    if not isinstance(history, list):
        history = []

    history.append({"role": "user", "content": text})
    user_history[user_id] = history[-50:]

    # Проверяем лимит бесплатных сообщений
    if not is_subscribed(user_id) and len(history) >= FREE_MESSAGE_LIMIT:
        await update.message.reply_text(
            f"🚫 Бесплатный лимит сообщений ({FREE_MESSAGE_LIMIT}) исчерпан.\n"
            "Оформи подписку 💳, чтобы продолжить:\n"
            "[Boosty](https://boosty.to/birukov-systems/posts/89b1960e-ceff-4f71-9b77-9040e631a7db?share=success_publish_link)",
            parse_mode="Markdown"
        )
        return

    await update.message.chat.send_action("typing")

    try:
        response = await call_openai(history)
        reply = response.choices[0].message.content
    except Exception as e:
        logging.error(f"OpenAI API error: {e}")
        await update.message.reply_text(f"❗ Ошибка при обращении к OpenAI: {str(e)}")
        return

    history.append({"role": "assistant", "content": reply})
    user_history[user_id] = history[-50:]
    save_history()

    await update.message.reply_text(reply)


async def analyze_personality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    history = user_history.get(user_id, [])

    await query.answer()

    if not history:
        await query.message.reply_text(
            "Сначала напиши мне несколько сообщений — тогда я смогу что-то проанализировать 😌"
        )
        return

    await query.message.chat.send_action("typing")

    # ── Собираем только сообщения пользователя ──
    user_messages = [h["content"] for h in history if h["role"] == "user"]

    if not user_messages:
        await query.message.reply_text("Я не нашёл твоих сообщений для анализа… странно 🤔")
        return

    # Более точный и структурированный промпт
    prompt = """Ты психолог и типолог, работаешь в системе MBTI + немного Big Five и коучинговых паттернов.
Проанализируй сообщения пользователя ниже и дай структурированный разбор личности.

Сначала определи **наиболее вероятный тип MBTI** (4 буквы) и степень уверенности (высокая / средняя / низкая).

Затем кратко разбери по шкалам:
• I/E — 
• N/S — 
• T/F — 
• J/P — 

После этого дай:
1. Ключевые сильные стороны
2. Вероятные слабые стороны / слепые зоны
3. Типичные внутренние конфликты или "затыки", которые могут проявляться
4. 2–3 коротких совета, что можно улучшить / на что обратить внимание

Стиль ответа: честный, доброжелательный, без воды, по делу. Не льсти и не демонизируй.

Сообщения пользователя (самые свежие сверху):

""" + "\n".join(user_messages[-18:])   # берём последние ~18 сообщений — обычно достаточно

    messages_for_api = [
        {"role": "system", "content": "Ты точный, наблюдательный психолог-типолог."},
        {"role": "user",   "content": prompt}
    ]

    try:
        response = await call_openai(messages_for_api)
        analysis_text = response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Ошибка анализа личности: {e}", exc_info=True)
        analysis_text = "⚠️ Не удалось выполнить анализ — ошибка связи с моделью. Попробуй позже."

    await query.message.reply_text(analysis_text, parse_mode="Markdown")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if user_id in user_history:
        del user_history[user_id]
    await update.message.reply_text("История очищена.")

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def handle_webhook(request):
    data = await request.json()
    telegram_app = request.app['telegram_app']
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.update_queue.put(update)
    return web.Response(status=200)

async def handle(request):
    return web.Response(text="MindChewBot is running.")

async def main():
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    PORT = int(os.getenv("PORT", "10000"))

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Добавляем хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем приложение (инициализация + запуск update queue)
    await app.initialize()
    await app.start()
    #await app.updater.start_polling()  # Для webhook не нужен polling, можно не запускать

    # Устанавливаем webhook
    await app.bot.set_webhook(f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")

    # Создаём aiohttp веб-сервер
    web_app = web.Application()
    web_app['telegram_app'] = app
    web_app.router.add_post(f"/{TELEGRAM_TOKEN}", handle_webhook)
    web_app.router.add_get("/", handle)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

    print(f"🚀 Webhook бот запущен на порту {PORT}")

    # Чтобы приложение не завершилось
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass

    # Остановка и очистка
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
