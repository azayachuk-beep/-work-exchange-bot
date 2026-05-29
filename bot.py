import os
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

TOKEN = os.getenv("TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start_command(message: Message):
    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Бот работает на Railway.\n"
        "Команда /start успешно получена."
    )

@dp.message()
async def echo(message: Message):
    await message.answer(
        f"Вы написали:\n{message.text}"
    )

async def main():
    print("WORK BOT STARTED")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
