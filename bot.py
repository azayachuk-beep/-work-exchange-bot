import asyncio
from aiogram import Bot, Dispatcher

TOKEN = "PASTE_YOUR_NEW_TOKEN_HERE"

bot = Bot(token=TOKEN)
dp = Dispatcher()

async def main():
    print("WORK BOT STARTED")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
