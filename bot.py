import os
import re
import json
import asyncio
from collections import Counter
from datetime import datetime, date

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

TOKEN = os.getenv("TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Храним все сделки в памяти
# key = service_message_id (сообщение бота с карточкой сделки)
deals = {}

LOG_FILE = "deals_log.json"


def user_label(user) -> str:
    """Красивое имя пользователя."""
    if getattr(user, "username", None):
        return f"@{user.username}"
    return user.full_name or "Неизвестно"
def save_log(author, worker):

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        data = []

    data.append({
        "date": str(date.today()),
        "author": author,
        "worker": worker
    })

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

def get_deal_rate(text: str) -> str:
    """
    Ищет курс сделки в тексте.
    Примеры:
    - Цена за 1 USDT: 43.6 UAH
    - Цена за 1 USDT 43.6 UAH
    """
    match = re.search(
        r"Цена за.*?1 USDT.*?([0-9]+(?:[.,][0-9]+)?)\s*([A-Za-zА-Яа-я]+)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        rate = match.group(1).replace(",", ".")
        currency = match.group(2).upper()
        return f"{rate} {currency}"

    return "Не найден"


def extract_fact_rate(text: str) -> str:
    """
    Достаёт фактический курс из ответа сотрудника.
    Примеры:
    - 44.2
    - 44,2
    - курс 44.2
    """
    match = re.search(r"([0-9]+(?:[.,][0-9]+)?)", text)
    if match:
        return match.group(1).replace(",", ".")
    return ""


def kb_take() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛠 Взять в работу",
                    callback_data="take"
                )
            ]
        ]
    )


def kb_receipt() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📸 Квитанция загружена",
                    callback_data="receipt_loaded"
                )
            ]
        ]
    )


def kb_close() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Завершить сделку",
                    callback_data="close"
                )
            ]
        ]
    )


@dp.message()
async def handle_messages(message: Message):
    content = message.text or message.caption or ""
    if not content:
        return

    # 1) Если это ответ на карточку "Ожидает курс" — принимаем фактический курс
    if message.reply_to_message:
        service_message_id = message.reply_to_message.message_id
        deal = deals.get(service_message_id)

        if deal and deal.get("state") == "awaiting_rate":
            # Курс должен прислать только исполнитель
            if message.from_user.id != deal["worker_id"]:
                return

            fact_rate = extract_fact_rate(content)
            if not fact_rate:
                await message.reply("Отправь фактический курс цифрами, например: 44.2")
                return

            deal["fact_rate"] = fact_rate
            deal["state"] = "awaiting_close"

            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=service_message_id,
                text=(
                    f"📋 Статус: Ожидает закрытия\n\n"
                    f"👤 Исполнитель: {deal['worker_name']}\n"
                    f"⚡ Реакция: {deal['reaction']} сек\n\n"
                    f"💱 Курс сделки: {deal['deal_rate']}\n"
                    f"💸 Фактический курс: {deal['fact_rate']}\n\n"
                    f"📸 Квитанция загружена"
                ),
                reply_markup=kb_close()
            )
            return

    # 2) Новая сделка
    if "Сделка #" in content:
        deal_rate = get_deal_rate(content)

        service = await message.reply(
            f"📋 Статус: Свободна\n\n"
            f"💱 Курс сделки: {deal_rate}",
            reply_markup=kb_take()
        )

        deals[service.message_id] = {
            "state": "free",
            "author_id": message.from_user.id,
            "author_name": user_label(message.from_user),
            "created": datetime.now(),
            "worker_id": None,
            "worker_name": None,
            "reaction": 0,
            "deal_rate": deal_rate,
            "fact_rate": "",
            "service_message_id": service.message_id,
        }
        return


@dp.callback_query(F.data == "take")
async def take_deal(callback: CallbackQuery):
    deal = deals.get(callback.message.message_id)
    if not deal:
        await callback.answer()
        return

    if deal["state"] != "free":
        await callback.answer("Сделка уже в работе", show_alert=True)
        return

    reaction = int((datetime.now() - deal["created"]).total_seconds())

    deal["state"] = "working"
    deal["worker_id"] = callback.from_user.id
    deal["worker_name"] = user_label(callback.from_user)
    deal["reaction"] = reaction

    await callback.message.edit_text(
        f"📋 Статус: В работе\n\n"
        f"👤 Исполнитель: {deal['worker_name']}\n"
        f"⚡ Реакция: {reaction} сек\n\n"
        f"💱 Курс сделки: {deal['deal_rate']}\n\n"
        f"📸 Квитанция: ожидается",
        reply_markup=kb_receipt()
    )

    await callback.answer()


@dp.callback_query(F.data == "receipt_loaded")
async def receipt_loaded(callback: CallbackQuery):
    deal = deals.get(callback.message.message_id)
    if not deal:
        await callback.answer()
        return

    if deal["state"] != "working":
        await callback.answer("Сначала возьмите сделку в работу", show_alert=True)
        return

    if callback.from_user.id != deal["worker_id"]:
        await callback.answer("Только исполнитель может отметить квитанцию", show_alert=True)
        return

    deal["state"] = "awaiting_rate"

    await callback.message.edit_text(
        f"📋 Статус: Ожидает курс\n\n"
        f"👤 Исполнитель: {deal['worker_name']}\n\n"
        f"💱 Курс сделки: {deal['deal_rate']}\n\n"
        f"Ответьте на это сообщение фактическим курсом.",
        reply_markup=None
    )

    await callback.answer()


@dp.callback_query(F.data == "close")
async def close_deal(callback: CallbackQuery):
    deal = deals.get(callback.message.message_id)
    if not deal:
        await callback.answer()
        return

    if callback.from_user.id != deal["author_id"]:
        await callback.answer("Закрыть может только автор сделки", show_alert=True)
        return

    if deal["state"] != "awaiting_close":
        await callback.answer("Сначала нужно указать фактический курс", show_alert=True)
        return

    deal["state"] = "closed"

save_log(
    deal["author_name"],
    deal["worker_name"]
)

await callback.message.edit_text(
        f"✅ Сделка завершена\n\n"
        f"👤 Исполнитель: {deal['worker_name']}\n"
        f"📨 Автор: {deal['author_name']}\n\n"
        f"💱 Курс сделки: {deal['deal_rate']}\n"
        f"💸 Фактический курс: {deal['fact_rate']}\n\n"
        f"⚡ Реакция: {deal['reaction']} сек",
        reply_markup=None
    )

    await callback.answer()


async def main():
    print("WORK BOT STARTED")

    await bot.delete_webhook(drop_pending_updates=True)

    me = await bot.get_me()
    print(f"BOT USERNAME: @{me.username}")
    print(f"BOT ID: {me.id}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
