import os
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

    if not message.text:
        return

    if "Сделка #" not in message.text:
        return

    service = await message.reply(
        "📋 Статус: Свободна",
        reply_markup=take_keyboard()
    )

    deals[service.message_id] = {
        "author_id": message.from_user.id,
        "author_name": message.from_user.full_name,
        "created": datetime.now(),
        "worker_id": None,
        "worker_name": None,
        "reaction": 0,
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

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Сделка завершена",
                    callback_data="close"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        f"📋 Статус: В работе\n\n"
        f"👤 Исполнитель: {deal['worker_name']}\n"
        f"⚡ Реакция: {reaction} сек",
        reply_markup=keyboard
    )

    await callback.answer()


@dp.callback_query(F.data == "close")
async def close_deal(callback: CallbackQuery):

    deal = deals.get(callback.message.message_id)

    if not deal:
        return

    if callback.from_user.id != deal["author_id"]:
        await callback.answer(
            "Закрыть может только автор сделки",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        f"✅ Сделка завершена\n\n"
        f"👤 Исполнитель: {deal['worker_name']}\n"
        f"📨 Автор: {deal['author_name']}\n"
        f"⚡ Реакция: {deal['reaction']} сек"
    )

    await callback.answer()


async def main():
    print("WORK BOT STARTED")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
