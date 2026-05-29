import os
import re
import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

TOKEN = os.getenv("TOKEN")

GROUP_ID = -1003958494363

bot = Bot(token=TOKEN)
dp = Dispatcher()

deals = {}


def get_deal_rate(text):
    match = re.search(
        r"Цена за.*?1 USDT.*?([0-9]+(?:\.[0-9]+)?)\s*([A-Z]+)",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    if match:
        return f"{match.group(1)} {match.group(2)}"

    return "Не найден"


def take_keyboard():
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


@dp.message(F.chat.id == GROUP_ID)
async def deal_handler(message: Message):

    content = message.text or message.caption or ""

    if "Сделка #" not in content:
        return

    deal_rate = get_deal_rate(content)

    service = await message.reply(
        f"📋 Статус: Свободна\n\n"
        f"💱 Курс сделки: {deal_rate}",
        reply_markup=take_keyboard()
    )

    deals[service.message_id] = {
        "author_id": message.from_user.id,
        "author_name": message.from_user.full_name,
        "created": datetime.now(),
        "worker_id": None,
        "worker_name": None,
        "reaction": 0,
        "receipt": False,
        "fact_rate": None,
        "deal_rate": deal_rate,
        "deal_message_id": message.message_id,
    }


@dp.callback_query(F.data == "take")
async def take_deal(callback: CallbackQuery):

    deal = deals.get(callback.message.message_id)

    if not deal:
        return

    if deal["worker_id"]:
        await callback.answer(
            "Сделка уже взята",
            show_alert=True
        )
        return

    reaction = int(
        (datetime.now() - deal["created"]).total_seconds()
    )

    deal["worker_id"] = callback.from_user.id
    deal["worker_name"] = (
        callback.from_user.username
        or callback.from_user.full_name
    )
    deal["reaction"] = reaction

    await callback.message.edit_text(
        f"📋 Статус: В работе\n\n"
        f"👤 Исполнитель: {deal['worker_name']}\n"
        f"⚡ Реакция: {reaction} сек\n\n"
        f"💱 Курс сделки: {deal['deal_rate']}\n\n"
        f"📸 Квитанция: ожидается"
    )

    await callback.answer()


@dp.message()
async def receipt_handler(message: Message):

    print(
        "PHOTO:", bool(message.photo),
        "DOCUMENT:", bool(message.document),
        "TEXT:", bool(message.text),
        "REPLY:", bool(message.reply_to_message),
        "CAPTION:", message.caption
    )


@dp.callback_query(F.data == "close")
async def close_deal(callback: CallbackQuery):

    deal = deals.get(callback.message.message_id)

    if not deal:
        return

    await callback.answer()


async def main():
    print("WORK BOT STARTED")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
