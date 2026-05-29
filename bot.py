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


def normalize_number(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text else "0"


def parse_first_number(text: str) -> float:
    match = re.search(r"([0-9]+(?:[.,][0-9]+)?)", text)
    if not match:
        return 0.0
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return 0.0


def load_logs():
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_log(author, worker, profit=0.0, pay_amount=0.0, deal_rate=0.0, fact_rate=0.0):
    try:
        data = load_logs()
        data.append(
            {
                "date": str(date.today()),
                "author": author,
                "worker": worker,
                "profit": round(float(profit), 2),
                "pay_amount": float(pay_amount),
                "deal_rate": float(deal_rate),
                "fact_rate": float(fact_rate),
            }
        )
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("SAVE LOG ERROR:", e)


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


def get_pay_amount(text: str) -> float:
    """
    Ищет сумму в строке 'Платите: 10,147 UAH'
    """
    match = re.search(
        r"Платите:\s*([0-9][0-9,.\s]*)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return 0.0

    value = match.group(1)
    value = value.replace(" ", "").replace(",", "")

    try:
        return float(value)
    except ValueError:
        return 0.0


def extract_fact_rate(text: str) -> str:
    """
    Достаёт фактический курс из ответа сотрудника.
    Примеры:
    - 44.2
    - 44,2
    - курс 44.2
    """
    value = parse_first_number(text)
    if value <= 0:
        return ""
    return normalize_number(value)


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


def stats_by_field(field: str, title: str) -> str:
    logs = load_logs()
    today = str(date.today())

    counter = Counter(
        item.get(field)
        for item in logs
        if item.get("date") == today and item.get(field)
    )

    if not counter:
        return f"{title}\n\nСегодня сделок нет"

    lines = [title, ""]
    for idx, (name, count) in enumerate(counter.most_common(), start=1):
        lines.append(f"{idx}. {name} — {count}")

    return "\n".join(lines)


def profit_stats() -> str:
    logs = load_logs()
    today = str(date.today())

    totals = {}

    for item in logs:
        if item.get("date") != today:
            continue

        author = item.get("author")
        if not author:
            continue

        try:
            profit = float(item.get("profit", 0))
        except Exception:
            profit = 0.0

        totals[author] = totals.get(author, 0.0) + profit

    if not totals:
        return "💰 Профит авторов за сегодня\n\nСегодня сделок нет"

    lines = ["💰 Профит авторов за сегодня", ""]
    total_sum = 0.0

    for idx, (author, value) in enumerate(
        sorted(totals.items(), key=lambda x: x[1], reverse=True),
        start=1,
    ):
        lines.append(f"{idx}. {author} — ${value:.2f}")
        total_sum += value

    lines.append("")
    lines.append(f"💵 Итого: ${total_sum:.2f}")

    return "\n".join(lines)


@dp.message()
async def handle_messages(message: Message):
    content = (message.text or message.caption or "").strip()

    if not content:
        return

    if content.startswith("/workers"):
        await message.answer(
            stats_by_field("worker", "👨‍💼 Исполнители за сегодня")
        )
        return

    if content.startswith("/authors"):
        await message.answer(
            stats_by_field("author", "📝 Авторы за сегодня")
        )
        return

    if content.startswith("/today"):
        logs = load_logs()
        today = str(date.today())
        count = sum(1 for item in logs if item.get("date") == today)

        await message.answer(
            f"📊 Сегодня\n\n✅ Завершено сделок: {count}"
        )
        return

    if content.startswith("/profit"):
        await message.answer(profit_stats())
        return

    # 1) Если это ответ на карточку "Ожидает курс" — принимаем фактический курс
    if message.reply_to_message:
        service_message_id = message.reply_to_message.message_id
        deal = deals.get(service_message_id)

        if deal and deal.get("state") == "awaiting_rate":
            if message.from_user.id != deal["worker_id"]:
                return

            fact_rate = extract_fact_rate(content)
            if not fact_rate:
                await message.reply("Отправь фактический курс цифрами, например: 44.2")
                return

            deal["fact_rate"] = fact_rate
            deal["fact_rate_value"] = parse_first_number(fact_rate)
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
        deal_rate_value = parse_first_number(deal_rate)
        pay_amount = get_pay_amount(content)

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
            "deal_rate_value": deal_rate_value,
            "fact_rate": "",
            "fact_rate_value": 0.0,
            "pay_amount": pay_amount,
            "profit": 0.0,
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

    deal_rate_value = float(deal.get("deal_rate_value", 0.0) or 0.0)
    fact_rate_value = float(deal.get("fact_rate_value", 0.0) or 0.0)
    pay_amount = float(deal.get("pay_amount", 0.0) or 0.0)

    profit = 0.0
    if pay_amount > 0 and deal_rate_value > 0 and fact_rate_value > 0:
        profit = round(
            (pay_amount / deal_rate_value) - (pay_amount / fact_rate_value),
            2
        )

    deal["profit"] = profit

    save_log(
        deal["author_name"],
        deal["worker_name"],
        profit=profit,
        pay_amount=pay_amount,
        deal_rate=deal_rate_value,
        fact_rate=fact_rate_value,
    )

    await callback.message.edit_text(
        f"✅ Сделка завершена\n\n"
        f"👤 Исполнитель: {deal['worker_name']}\n"
        f"📨 Автор: {deal['author_name']}\n\n"
        f"💱 Курс сделки: {deal['deal_rate']}\n"
        f"💸 Фактический курс: {deal['fact_rate']}\n"
        f"💰 Профит: ${profit:.2f}\n\n"
        f"⚡ Реакция: {deal['reaction']} сек",
        reply_markup=None
    )

    deals.pop(callback.message.message_id, None)

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
