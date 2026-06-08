import os
import re
import json
import asyncio
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

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

deals = {}

LOG_FILE = "deals_log.json"
TZ = ZoneInfo("Asia/Tbilisi")


def user_label(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"

    return user.full_name or f"id{user.id}"


def normalize_number(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text else "0"


def parse_first_number(text: str) -> float:
    match = re.search(r"([0-9]+(?:[.,][0-9]+)?)", text)

    if not match:
        return 0.0

    try:
        return float(match.group(1).replace(",", "."))
    except:
        return 0.0


def format_amount(value: float) -> str:
    try:
        value = float(value)
    except:
        return "0"

    if value.is_integer():
        return str(int(value))

    return normalize_number(value)


def current_date() -> date:
    return datetime.now(TZ).date()


def current_date_key() -> str:
    return str(current_date())


def parse_date_key(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except:
        return None


def is_within_last_days(date_str: str, days: int) -> bool:
    d = parse_date_key(date_str)

    if not d:
        return False

    return d >= (current_date() - timedelta(days=days))


def load_logs():
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

            if isinstance(data, list):
                return data

    except:
        pass

    return []


def save_logs(data):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =========================
# ЗАЩИТА ОТ ДУБЛЕЙ
# =========================
def save_log(
    deal_id="",
    author_id=0,
    author_name="",
    worker_id=0,
    worker_name="",
    profit=0.0,
):
    data = load_logs()

    # НЕ СОХРАНЯЕМ ДУБЛЬ
    for item in data:
        if item.get("deal_id") == deal_id:
            print(f"DUPLICATE DEAL SKIPPED: {deal_id}")
            return

    data.append(
        {
            "date": current_date_key(),

            "deal_id": deal_id,

            "author_id": author_id,
            "author_name": author_name,

            "worker_id": worker_id,
            "worker_name": worker_name,

            "profit": round(float(profit), 2),
        }
    )

    save_logs(data)


def get_deal_rate(text: str) -> str:
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


def get_deal_id(text: str) -> str:
    match = re.search(
        r"Сделка\s*#([^\s\n]+)",
        text,
        re.IGNORECASE,
    )

    if match:
        return match.group(1).strip()

    return "UNKNOWN"


def get_pay_amount(text: str) -> float:
    match = re.search(
        r"(?:Платите|Получаете):.*?([0-9][0-9,\s.]*)",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return 0.0

    value = match.group(1)

    value = value.replace(" ", "")
    value = value.replace(",", "")

    try:
        return float(value)
    except:
        return 0.0


def extract_fact_rate(text: str) -> str:
    value = parse_first_number(text)

    if value <= 0:
        return ""

    return normalize_number(value)


def calc_profit(pay_amount, deal_rate, fact_rate):
    try:
        return round(
            (pay_amount / deal_rate) -
            (pay_amount / fact_rate),
            2
        )
    except:
        return 0.0


def kb_take():
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


def kb_working():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Освободить сделку",
                    callback_data="release"
                )
            ]
        ]
    )


def kb_close():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Изменить курс",
                    callback_data="edit_rate"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Завершить",
                    callback_data="close"
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="cancel"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Освободить сделку",
                    callback_data="release"
                )
            ]
        ]
    )


def stats_by_field(field_id, field_name, title, days=0):
    logs = load_logs()

    if days == 0:
        filtered = [
            x for x in logs
            if x.get("date") == current_date_key()
        ]
    else:
        filtered = [
            x for x in logs
            if x.get("date")
            and is_within_last_days(x.get("date"), days)
        ]

    totals = {}

    for item in filtered:
        uid = item.get(field_id)

        if not uid:
            continue

        if uid not in totals:
            totals[uid] = {
                "name": item.get(field_name),
                "count": 0,
            }

        totals[uid]["count"] += 1

    if not totals:
        return f"{title}\n\nНет данных"

    lines = [title, ""]

    sorted_items = sorted(
        totals.values(),
        key=lambda x: x["count"],
        reverse=True
    )

    for idx, item in enumerate(sorted_items, start=1):
        lines.append(
            f"{idx}. {item['name']} — {item['count']}"
        )

    return "\n".join(lines)


def profit_stats(days=0):
    logs = load_logs()

    if days == 0:
        filtered = [
            x for x in logs
            if x.get("date") == current_date_key()
        ]

        title = "💰 Профит за сегодня"

    elif days == 1:
        target = str(current_date() - timedelta(days=1))

        filtered = [
            x for x in logs
            if x.get("date") == target
        ]

        title = "💰 Профит за вчера"

    else:
        filtered = [
            x for x in logs
            if x.get("date")
            and is_within_last_days(x.get("date"), days)
        ]

        title = "💰 Профит за неделю"

    totals = {}

    for item in filtered:
        uid = item.get("author_id")

        if not uid:
            continue

        if uid not in totals:
            totals[uid] = {
                "name": item.get("author_name"),
                "profit": 0.0,
                "count": 0,
            }

        totals[uid]["profit"] += float(item.get("profit", 0))
        totals[uid]["count"] += 1

    if not totals:
        return f"{title}\n\nНет данных"

    lines = [title, ""]

    total_profit = 0
    total_deals = 0

    sorted_items = sorted(
        totals.values(),
        key=lambda x: x["profit"],
        reverse=True
    )

    for idx, item in enumerate(sorted_items, start=1):
        lines.append(
            f"{idx}. {item['name']}\n"
            f"Сделок: {item['count']}\n"
            f"Профит: ${item['profit']:.2f}\n"
        )

        total_profit += item["profit"]
        total_deals += item["count"]

    lines.append(f"💵 Общий профит: ${total_profit:.2f}")
    lines.append(f"📊 Всего сделок: {total_deals}")

    return "\n".join(lines)


@dp.message()
async def handle_messages(message: Message):
    content = (message.text or message.caption or "").strip()

    if not content:
        return

    # =========================
    # КОМАНДЫ
    # =========================

    if content.startswith("/week_workers"):
        await message.answer(
            stats_by_field(
                "worker_id",
                "worker_name",
                "👨‍💼 Исполнители за неделю",
                days=6
            )
        )
        return

    if content.startswith("/week_authors"):
        await message.answer(
            stats_by_field(
                "author_id",
                "author_name",
                "📝 Авторы за неделю",
                days=6
            )
        )
        return

    if content.startswith("/week_profit"):
        await message.answer(
            profit_stats(days=6)
        )
        return

    if content.startswith("/yesterday_profit"):
        await message.answer(
            profit_stats(days=1)
        )
        return

    if content.startswith("/workers"):
        await message.answer(
            stats_by_field(
                "worker_id",
                "worker_name",
                "👨‍💼 Исполнители за сегодня"
            )
        )
        return

    if content.startswith("/authors"):
        await message.answer(
            stats_by_field(
                "author_id",
                "author_name",
                "📝 Авторы за сегодня"
            )
        )
        return

    if content.startswith("/profit"):
        await message.answer(
            profit_stats(days=0)
        )
        return

    if content.startswith("/today"):
        logs = load_logs()

        count = sum(
            1 for x in logs
            if x.get("date") == current_date_key()
        )

        await message.answer(
            f"📊 Сегодня\n\n✅ Завершено сделок: {count}"
        )
        return

    if content.startswith("/week"):
        logs = load_logs()

        count = sum(
            1 for x in logs
            if x.get("date")
            and is_within_last_days(x.get("date"), 6)
        )

        await message.answer(
            f"📊 За неделю\n\n✅ Завершено сделок: {count}"
        )
        return

    # =========================
    # ВВОД КУРСА
    # =========================

    if message.reply_to_message:
        service_message_id = message.reply_to_message.message_id

        deal = deals.get(service_message_id)

        if deal and deal.get("state") in [
            "awaiting_rate",
            "editing_rate"
        ]:

            if message.from_user.id != deal["worker_id"]:
                return

            fact_rate = extract_fact_rate(content)

            if not fact_rate:
                await message.reply(
                    "Отправь курс цифрами. Например: 44.2"
                )
                return

            deal["fact_rate"] = fact_rate
            deal["fact_rate_value"] = parse_first_number(fact_rate)

            deal["state"] = "awaiting_close"

            profit = calc_profit(
                deal["pay_amount"],
                deal["deal_rate_value"],
                deal["fact_rate_value"]
            )

            deal["profit"] = profit

            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=service_message_id,
                text=(
                    f"📋 Статус: Ожидает закрытия\n\n"

                    f"🆔 Сделка: {deal['deal_id']}\n\n"

                    f"👤 Исполнитель: {deal['worker_name']}\n"
                    f"⚡ Реакция: {deal['reaction']} сек\n\n"

                    f"💱 Курс сделки: {deal['deal_rate']}\n"
                    f"💸 Фактический курс: {deal['fact_rate']}\n"

                    f"💵 Сумма сделки: "
                    f"{format_amount(deal['pay_amount'])}\n"

                    f"💰 Профит: ${profit:.2f}"
                ),
                reply_markup=kb_close()
            )

            return

    # =========================
    # НОВАЯ СДЕЛКА
    # =========================

    if "Сделка #" in content:
        deal_rate = get_deal_rate(content)

        deal_rate_value = parse_first_number(deal_rate)

        deal_id = get_deal_id(content)

        pay_amount = get_pay_amount(content)

        service = await message.reply(
            f"📋 Статус: Свободна\n\n"

            f"🆔 Сделка: {deal_id}\n\n"

            f"💱 Курс сделки: {deal_rate}\n"
            f"💵 Сумма сделки: "
            f"{format_amount(pay_amount)}",

            reply_markup=kb_take()
        )

        deals[service.message_id] = {
            "state": "free",

            "deal_id": deal_id,

            "author_id": message.from_user.id,
            "author_name": user_label(message.from_user),

            "worker_id": 0,
            "worker_name": "",

            "created": datetime.now(),

            "reaction": 0,

            "deal_rate": deal_rate,
            "deal_rate_value": deal_rate_value,

            "fact_rate": "",
            "fact_rate_value": 0.0,

            "pay_amount": pay_amount,

            "profit": 0.0,

            "closed": False,
        }

        return


@dp.callback_query(F.data == "take")
async def take_deal(callback: CallbackQuery):
    deal = deals.get(callback.message.message_id)

    if not deal:
        await callback.answer()
        return

    if deal["state"] != "free":
        await callback.answer(
            "Сделка уже занята",
            show_alert=True
        )
        return

    reaction = int(
        (datetime.now() - deal["created"]).total_seconds()
    )

    deal["worker_id"] = callback.from_user.id
    deal["worker_name"] = user_label(callback.from_user)

    deal["reaction"] = reaction

    deal["state"] = "awaiting_rate"

    await callback.message.edit_text(
        f"📋 Статус: В работе\n\n"

        f"🆔 Сделка: {deal['deal_id']}\n\n"

        f"👤 Исполнитель: {deal['worker_name']}\n"
        f"⚡ Реакция: {reaction} сек\n\n"

        f"💱 Курс сделки: {deal['deal_rate']}\n"
        f"💵 Сумма сделки: "
        f"{format_amount(deal['pay_amount'])}\n\n"

        f"Ответьте на это сообщение фактическим курсом.",

        reply_markup=kb_working()
    )

    await callback.answer()


@dp.callback_query(F.data == "edit_rate")
async def edit_rate(callback: CallbackQuery):
    deal = deals.get(callback.message.message_id)

    if not deal:
        return

    deal["state"] = "editing_rate"

    await callback.answer(
        "Отправьте новый курс ответом на сообщение"
    )


@dp.callback_query(F.data == "release")
async def release_deal(callback: CallbackQuery):
    deal = deals.get(callback.message.message_id)

    if not deal:
        return

    deal["state"] = "free"

    deal["worker_id"] = 0
    deal["worker_name"] = ""

    deal["reaction"] = 0

    deal["fact_rate"] = ""
    deal["fact_rate_value"] = 0.0

    deal["profit"] = 0.0

    await callback.message.edit_text(
        f"📋 Статус: Свободна\n\n"

        f"🆔 Сделка: {deal['deal_id']}\n\n"

        f"💱 Курс сделки: {deal['deal_rate']}\n"
        f"💵 Сумма сделки: "
        f"{format_amount(deal['pay_amount'])}",

        reply_markup=kb_take()
    )

    await callback.answer(
        "Сделка освобождена"
    )


@dp.callback_query(F.data == "cancel")
async def cancel_deal(callback: CallbackQuery):
    deal = deals.get(callback.message.message_id)

    if not deal:
        return

    deal["state"] = "cancelled"

    await callback.message.edit_text(
        f"❌ Сделка отменена\n\n"

        f"🆔 Сделка: {deal['deal_id']}\n\n"

        f"👤 Исполнитель: "
        f"{deal.get('worker_name', '-')}\n"

        f"📨 Автор: "
        f"{deal.get('author_name', '-')}",
    )

    deals.pop(callback.message.message_id, None)

    await callback.answer(
        "Сделка отменена"
    )


# =========================
# ЗАКРЫТИЕ БЕЗ ДУБЛЕЙ
# =========================
@dp.callback_query(F.data == "close")
async def close_deal(callback: CallbackQuery):
    deal = deals.get(callback.message.message_id)

    if not deal:
        return

    if deal["state"] != "awaiting_close":
        await callback.answer(
            "Сначала укажите курс",
            show_alert=True
        )
        return

    # БЛОК ОТ ДУБЛЕЙ
    if deal.get("closed"):
        await callback.answer(
            "Сделка уже закрыта",
            show_alert=True
        )
        return

    deal["closed"] = True

    profit = calc_profit(
        deal["pay_amount"],
        deal["deal_rate_value"],
        deal["fact_rate_value"]
    )

    save_log(
        deal_id=deal["deal_id"],

        author_id=deal["author_id"],
        author_name=deal["author_name"],

        worker_id=deal["worker_id"],
        worker_name=deal["worker_name"],

        profit=profit,
    )

    await callback.message.edit_text(
        f"✅ Сделка завершена\n\n"

        f"🆔 Сделка: {deal['deal_id']}\n\n"

        f"👤 Исполнитель: {deal['worker_name']}\n"
        f"📨 Автор: {deal['author_name']}\n\n"

        f"💱 Курс сделки: {deal['deal_rate']}\n"
        f"💸 Фактический курс: {deal['fact_rate']}\n"

        f"💵 Сумма сделки: "
        f"{format_amount(deal['pay_amount'])}\n"

        f"💰 Профит: ${profit:.2f}\n\n"

        f"⚡ Реакция: {deal['reaction']} сек",
    )

    deals.pop(callback.message.message_id, None)

    await callback.answer(
        "Сделка закрыта"
    )


async def main():
    print("WORK BOT STARTED")

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    me = await bot.get_me()

    print(f"BOT USERNAME: @{me.username}")
    print(f"BOT ID: {me.id}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
