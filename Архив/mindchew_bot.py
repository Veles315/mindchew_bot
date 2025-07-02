import os
import json
import openai
import asyncio
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv




# 🔑 ВСТАВЬ СВОИ КЛЮЧИ
load_dotenv()  # загружает переменные из .env в окружение

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
openai.api_key = os.getenv("OPENAI_API_KEY")

HISTORY_FILE = "user_history.json"
REMINDERS_FILE = "user_reminders.json"  # Файл с напоминаниями
FREE_MESSAGE_LIMIT = 5
FREE_REMINDER_LIMIT = 1  # Разрешено 1 бесплатное активное напоминание
REMINDER_STATE = {}  # Для диалогов установки напоминаний: user_id: {step, date, time, text}

# Загрузка истории и напоминаний
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        user_history = json.load(f)
else:
    user_history = {}

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

# --- Функция сохранения напоминаний ---
def save_reminders():
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(user_reminders, f, ensure_ascii=False, indent=2)

# --- Проверка количества активных напоминаний ---
def count_active_reminders(user_id):
    now_ts = datetime.now().timestamp()
    reminders = user_reminders.get(user_id, [])
    # Учитываем только напоминания с временем в будущем
    active = [r for r in reminders if datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M").timestamp() > now_ts]
    return len(active)

# --- Отправка отложенного напоминания ---
async def send_reminder_later(context, chat_id, text, delay, user_id, reminder_id):
    await asyncio.sleep(delay)
    await context.bot.send_message(chat_id=chat_id, text=f"🔔 Напоминание:\n{text}")
    # После отправки — удаляем напоминание из списка
    if user_id in user_reminders:
        user_reminders[user_id] = [r for r in user_reminders[user_id] if r["id"] != reminder_id]
        save_reminders()

# --- Стартовое меню ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🧠 Анализ личности", callback_data="analyze_personality")],
        [InlineKeyboardButton("⏰ Установить напоминание", callback_data="set_reminder")],
        [InlineKeyboardButton("📝 Мои напоминания", callback_data="my_reminders")],
        [InlineKeyboardButton("💳 Подписка на Boosty", url="https://boosty.to/birukov-systems/posts/89b1960e-ceff-4f71-9b77-9040e631a7db?share=success_publish_link")],
        [InlineKeyboardButton("♻️ Сбросить историю", callback_data="reset_history")]
    ]
    await update.message.reply_text(
        """🧠 Привет! Я — MindChewBot.
Напиши, что у тебя в голове — всё, что тревожит или просто крутится в мыслях.

Я помогу навести порядок, покажу твой тип личности и подскажу, где могут быть внутренние затыки.

✍ Просто начни писать — как другу.""",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# --- Обработчик кнопок ---
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
        # Проверяем лимит бесплатных напоминаний
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
        reminders = user_reminders.get(user_id, [])
        if not reminders:
            await query.message.reply_text("У тебя нет активных напоминаний.")
            return

        keyboard = []
        for r in reminders:
            dt = r["datetime"]
            txt = r["text"]
            rid = r["id"]
            keyboard.append([InlineKeyboardButton(f"{dt} — {txt[:20]}...", callback_data=f"edit_reminder_{rid}")])
        keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_menu")])
        await query.message.reply_text("Твои напоминания:", reply_markup=InlineKeyboardMarkup(keyboard))

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

        # Меню редактирования напоминания
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
        await query.message.reply_text("Напоминание удалено.")

        # Показываем обновлённый список напоминаний
        reminders = user_reminders.get(user_id, [])
        if not reminders:
            await query.message.reply_text("У тебя нет активных напоминаний.")
            return

        keyboard = []
        for r in reminders:
            dt = r["datetime"]
            txt = r["text"]
            rid = r["id"]
            keyboard.append([InlineKeyboardButton(f"{dt} — {txt[:20]}...", callback_data=f"edit_reminder_{rid}")])
        keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_menu")])
        await query.message.reply_text("Твои напоминания:", reply_markup=InlineKeyboardMarkup(keyboard))

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
        state = REMINDER_STATE[user_id]

        # Обновляем напоминание
        reminders = user_reminders.get(user_id, [])
        reminder = next((r for r in reminders if r["id"] == rid), None)
        if not reminder:
            await query.message.reply_text("Напоминание не найдено.")
            return

        new_dt_str = f"{state['new_date']} {state['new_hour']:02d}:{new_minute:02d}"
        try:
            dt_obj = datetime.strptime(new_dt_str, "%Y-%m-%d %H:%M")
            if dt_obj < datetime.now():
                await query.message.reply_text("Время уже прошло. Попробуйте снова.")
                return
        except:
            await query.message.reply_text("Неверный формат даты/времени.")
            return

        reminder["datetime"] = new_dt_str
        save_reminders()
        REMINDER_STATE.pop(user_id, None)
        await query.message.reply_text("Дата и время напоминания обновлены.")
        # Показываем меню напоминаний
        await button_handler(update, context)

    elif data == "back_to_menu":
        await start(update, context)

# --- Обработка сообщений пользователя ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    msg = update.message.text.strip()

    # Проверяем, есть ли состояние для установки или редактирования напоминания
    if user_id in REMINDER_STATE:
        state = REMINDER_STATE[user_id]

        # Установка нового напоминания (шаги выбора даты/времени и текста)
        if state.get("step") == 3:
            state["text"] = msg
            dt_str = f"{state['date']} {state['hour']:02d}:{state['minute']:02d}"
            try:
                remind_at = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                delay = (remind_at - datetime.now()).total_seconds()
                if delay < 0:
                    await update.message.reply_text("⏳ Время уже прошло, попробуйте снова.")
                    return

                # Сохраняем напоминание с уникальным ID
                rid = f"{int(datetime.now().timestamp())}_{user_id}"
                reminder = {
                    "id": rid,
                    "datetime": dt_str,
                    "text": state["text"]
                }
                if user_id not in user_reminders:
                    user_reminders[user_id] = []
                user_reminders[user_id].append(reminder)
                save_reminders()

                # Запускаем отложенную отправку
                asyncio.create_task(send_reminder_later(context, update.effective_chat.id, state["text"], delay, user_id, rid))

                await update.message.reply_text("✅ Напоминание установлено!")
                REMINDER_STATE.pop(user_id, None)
                return
            except Exception as e:
                await update.message.reply_text(f"⚠️ Ошибка: {e}")
                return

        # Редактирование текста напоминания
        elif state.get("step") == "edit_text":
            rid = state.get("reminder_id")
            reminders = user_reminders.get(user_id, [])
            reminder = next((r for r in reminders if r["id"] == rid), None)
            if reminder:
                reminder["text"] = msg
                save_reminders()
                await update.message.reply_text("Текст напоминания обновлен.")
            else:
                await update.message.reply_text("Напоминание не найдено.")
            REMINDER_STATE.pop(user_id, None)
            return

    # Иначе — обрабатываем обычное сообщение с GPT (без изменений)
    if user_id not in user_history:
        user_history[user_id] = {"messages": [], "count": 0}
    history = user_history[user_id]

    if not is_subscribed(user_id) and history["count"] >= FREE_MESSAGE_LIMIT:
        await update.message.reply_text(
            "🚫 Доступ ограничен. Чтобы продолжить — оформи подписку 💳: [Boosty](https://boosty.to/birukov-systems/posts/89b1960e-ceff-4f71-9b77-9040e631a7db?share=success_publish_link)",
            parse_mode="Markdown"
        )
        return

    history["messages"].append({"role": "user", "content": msg})
    history["count"] += 1

    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Ты — помощник для структурирования мыслей пользователя."},
                *history["messages"][-10:]
            ],
            temperature=0.7,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()
        await update.message.reply_text(reply)
        history["messages"].append({"role": "assistant", "content": reply})
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(user_history, f, ensure_ascii=False, indent=2)

# --- Анализ личности (как есть) ---
async def analyze_personality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id if update.message else update.callback_query.from_user.id)

    if user_id not in user_history or not user_history[user_id]["messages"]:
        text = "ℹ️ Нет истории сообщений для анализа."
        if update.message:
            await update.message.reply_text(text)
        else:
            await update.callback_query.message.reply_text(text)
        return

    if "mbti" in user_history[user_id]:
        text = f"🧠 Вот твой сохранённый анализ личности:\n\n{user_history[user_id]['mbti']}"
        if update.message:
            await update.message.reply_text(text)
        else:
            await update.callback_query.message.reply_text(text)
        return

    user_msgs = [msg["content"] for msg in user_history[user_id]["messages"] if msg["role"] == "user"]
    total_chars = sum(len(m) for m in user_msgs)
    MIN_CHARS = 600

    if total_chars < MIN_CHARS:
        text = f"⚠️ Недостаточно данных для точного анализа.\nПожалуйста, напиши ещё минимум {MIN_CHARS} символов суммарно."
        if update.message:
            await update.message.reply_text(text)
        else:
            await update.callback_query.message.reply_text(text)
        return

    prompt = "Определи тип личности MBTI и дай краткий отчет, не длиннее 450 символов, основываясь на этих сообщениях:\n\n"
    for i, m in enumerate(user_msgs, 1):
        prompt += f"{i}. {m}\n"

    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — эксперт по типам личности MBTI. Напиши развернутый и дружелюбный анализ личности пользователя, объёмом примерно 1300-1500 символов. В анализе обязательно:\n"
                        "1. Кратко опиши основной тип личности (MBTI).\n"
                        "2. Найди и подробно опиши тёмные зоны — сложности и внутренние препятствия, с которыми может сталкиваться человек с этим типом.\n"
                        "3. Определи зоны личностного роста — что важно развивать, чтобы улучшить качество жизни, повысить комфорт и эффективность.\n"
                        "4. Объясни, зачем пользователю важно понимать свой тип личности, как это знание поможет в личностном развитии, построении отношений и повышении общего благополучия.\n"
                        "Начни с заголовка \"🧠 Анализ MBTI:\" и пиши структурировано, с понятными предложениями и дружелюбным тоном."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=500
        )

        answer = response.choices[0].message.content.strip()

        MAX_RESPONSE_CHARS = 3500
        if len(answer) > MAX_RESPONSE_CHARS:
            answer = answer[:MAX_RESPONSE_CHARS].rstrip() + "\n\n… (ответ сокращён)"

        user_history[user_id]["mbti"] = answer
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(user_history, f, ensure_ascii=False, indent=2)

        text = answer
        if update.message:
            await update.message.reply_text(text)
        else:
            await update.callback_query.message.reply_text(text)

    except Exception as e:
        text = f"⚠️ Ошибка при анализе: {e}"
        if update.message:
            await update.message.reply_text(text)
        else:
            await update.callback_query.message.reply_text(text)


# --- Сброс истории ---
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in user_history:
        del user_history[user_id]
        await update.message.reply_text("♻️ История очищена.")
    else:
        await update.message.reply_text("Нет истории.")

# --- Запуск бота ---
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 MindChewBot запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
