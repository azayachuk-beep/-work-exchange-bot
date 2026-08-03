import os
import re
import json
import ssl
import asyncio
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery


TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher()

TZ = ZoneInfo("Asia/Tbilisi")
pool: asyncpg.Pool | None = None
deals = {}


def get_dsn() -> str | None:
    if DATABASE_URL:
        return DATABASE_URL

    host = os.getenv("PGHOST")
    port = os.getenv("PGPORT")
    database = os.getenv("PGDATABASE")
    user = os.getenv("PGUSER")
    password = os.getenv("PGPASSWORD")

    if not all([host, port, database, user, password]):
        return None

    return f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}"


def get_ssl_setting():
    if not DATABASE_URL:
        return None

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


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


def format_money(value: float, currency: str = "") -> str:
    amount = format_amount(value)
    currency = (currency or "").strip()
    return f"{amount} {currency}".strip()


def current_date() -> date:
    return datetime.now(TZ).date()


def current_date_key() -> str:
    return str(current_date())


def parse_date_key(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except:
        return None


def parse_input_date(value: str):
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except:
            pass
    return None


def is_within_last_days(date_str: str, days: int) -> bool:
    d = parse_date_key(date_str)
    if not d:
        return False
    return d >= (current_date() - timedelta(days=days))


async def init_db():
    assert pool is not None
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_deals (
                chat_id BIGINT NOT NULL,
                service_message_id BIGINT NOT NULL,
                state TEXT NOT NULL,
                deal_id TEXT NOT NULL UNIQUE,
                author_id BIGINT NOT NULL,
                author_name TEXT NOT NULL,
                worker_id BIGINT NOT NULL DEFAULT 0,
                worker_name TEXT NOT NULL DEFAULT '',
                deal_rate TEXT NOT NULL,
                deal_rate_value DOUBLE PRECISION NOT NULL DEFAULT 0,
                fact_rate TEXT NOT NULL DEFAULT '',
                fact_rate_value DOUBLE PRECISION NOT NULL DEFAULT 0,
                pay_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                pay_currency TEXT NOT NULL DEFAULT '',
                profit DOUBLE PRECISION NOT NULL DEFAULT 0,
                closed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (chat_id, service_message_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS completed_deals (
                id BIGSERIAL PRIMARY KEY,
                date DATE NOT NULL,
                deal_id TEXT NOT NULL UNIQUE,
                author_id BIGINT NOT NULL,
                author_name TEXT NOT NULL,
                worker_id BIGINT NOT NULL,
                worker_name TEXT NOT NULL,
                profit DOUBLE PRECISION NOT NULL DEFAULT 0,
                pay_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                pay_currency TEXT NOT NULL DEFAULT '',
                deal_rate_value DOUBLE PRECISION NOT NULL DEFAULT 0,
                fact_rate_value DOUBLE PRECISION NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                closed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_completed_deals_date ON completed_deals(date);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_completed_deals_worker ON completed_deals(worker_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_completed_deals_author ON completed_deals(author_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_active_deals_state ON active_deals(state);")


async def migrate_legacy_json_logs():
    assert pool is not None
    legacy_path = "deals_log.json"
    if not os.path.exists(legacy_path):
        return

    try:
        with open(legacy_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("LEGACY JSON READ ERROR:", e)
        return

    if not isinstance(data, list) or not data:
        return

    imported = 0
    async with pool.acquire() as conn:
        for item in data:
            deal_id = str(item.get("deal_id", "")).strip()
            if not deal_id or deal_id == "UNKNOWN":
                continue

            date_str = str(item.get("date", "")).strip()
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d").date()
            except Exception:
                continue

            author_id = int(item.get("author_id", 0) or 0)
            worker_id = int(item.get("worker_id", 0) or 0)
            author_name = str(item.get("author_name", "") or "")
            worker_name = str(item.get("worker_name", "") or "")
            profit = float(item.get("profit", 0) or 0)

            await conn.execute(
                """
                INSERT INTO completed_deals
                (date, deal_id, author_id, author_name, worker_id, worker_name, profit, pay_amount, pay_currency, deal_rate_value, fact_rate_value)
                VALUES ($1, $2, $3, $4, $5, $6, $7, 0, '', 0, 0)
                ON CONFLICT (deal_id) DO NOTHING
                """,
                d, deal_id, author_id, author_name, worker_id, worker_name, profit
            )
            imported += 1

    print(f"LEGACY LOGS IMPORTED: {imported}")


def normalize_deal_row(row: dict) -> dict:
    deal = dict(row)
    deal["chat_id"] = int(deal["chat_id"])
    deal["service_message_id"] = int(deal["service_message_id"])
    deal["author_id"] = int(deal["author_id"])
    deal["worker_id"] = int(deal["worker_id"])
    deal["deal_rate_value"] = float(deal.get("deal_rate_value", 0) or 0)
    deal["fact_rate_value"] = float(deal.get("fact_rate_value", 0) or 0)
    deal["pay_amount"] = float(deal.get("pay_amount", 0) or 0)
    deal["profit"] = float(deal.get("profit", 0) or 0)
    deal["closed"] = bool(deal.get("closed", False))
    return deal


async def load_active_deals():
    assert pool is not None
    deals.clear()
    rows = await pool.fetch("SELECT * FROM active_deals")
    for row in rows:
        deal = normalize_deal_row(row)
        if deal.get("state") in {"closed", "cancelled"}:
            continue
        deals[(deal["chat_id"], deal["service_message_id"])] = deal


async def upsert_active_deal(deal: dict):
    assert pool is not None
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO active_deals
            (chat_id, service_message_id, state, deal_id, author_id, author_name, worker_id, worker_name,
             deal_rate, deal_rate_value, fact_rate, fact_rate_value, pay_amount, pay_currency, profit, closed)
            VALUES
            ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (chat_id, service_message_id) DO UPDATE SET
                state = EXCLUDED.state,
                deal_id = EXCLUDED.deal_id,
                author_id = EXCLUDED.author_id,
                author_name = EXCLUDED.author_name,
                worker_id = EXCLUDED.worker_id,
                worker_name = EXCLUDED.worker_name,
                deal_rate = EXCLUDED.deal_rate,
                deal_rate_value = EXCLUDED.deal_rate_value,
                fact_rate = EXCLUDED.fact_rate,
                fact_rate_value = EXCLUDED.fact_rate_value,
                pay_amount = EXCLUDED.pay_amount,
                pay_currency = EXCLUDED.pay_currency,
                profit = EXCLUDED.profit,
                closed = EXCLUDED.closed,
                updated_at = NOW()
            """,
            deal["chat_id"],
            deal["service_message_id"],
            deal["state"],
            deal["deal_id"],
            deal["author_id"],
            deal["author_name"],
            deal["worker_id"],
            deal["worker_name"],
            deal["deal_rate"],
            deal["deal_rate_value"],
            deal["fact_rate"],
            deal["fact_rate_value"],
            deal["pay_amount"],
            deal.get("pay_currency", ""),
            deal.get("profit", 0.0),
            deal.get("closed", False),
        )


async def delete_active_deal(chat_id: int, service_message_id: int):
    assert pool is not None
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM active_deals WHERE chat_id = $1 AND service_message_id = $2",
            chat_id,
            service_message_id,
        )


async def insert_completed_deal(deal: dict):
    assert pool is not None
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO completed_deals
                (date, deal_id, author_id, author_name, worker_id, worker_name, profit, pay_amount, pay_currency, deal_rate_value, fact_rate_value)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                ON CONFLICT (deal_id) DO NOTHING
                """,
                current_date(),
                deal["deal_id"],
                deal["author_id"],
                deal["author_name"],
                deal["worker_id"],
                deal["worker_name"],
                deal["profit"],
                deal["pay_amount"],
                deal.get("pay_currency", ""),
                deal["deal_rate_value"],
                deal["fact_rate_value"],
            )
            await conn.execute(
                "DELETE FROM active_deals WHERE chat_id = $1 AND service_message_id = $2",
                deal["chat_id"],
                deal["service_message_id"],
            )


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
    match = re.search(r"Сделка\s*#([^\s\n]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "UNKNOWN"


def get_deal_amount(text: str):
    match = re.search(
        r"(?:Платите|Получаете):.*?([0-9][0-9,\s.]*)\s*([A-Za-zА-Яа-я]{2,6})",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        amount_raw = match.group(1).replace(" ", "").replace(",", "")
        currency = match.group(2).upper()
        try:
            return float(amount_raw), currency
        except:
            return 0.0, currency

    match = re.search(
        r"(?:Платите|Получаете):.*?([0-9][0-9,\s.]*)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        amount_raw = match.group(1).replace(" ", "").replace(",", "")
        try:
            return float(amount_raw), ""
        except:
            return 0.0, ""

    return 0.0, ""


def extract_fact_rate(text: str) -> str:
    value = parse_first_number(text)
    if value <= 0:
        return ""
    return normalize_number(value)


def calc_profit(pay_amount, deal_rate, fact_rate):
    try:
        return round((pay_amount / deal_rate) - (pay_amount / fact_rate), 2)
    except:
        return 0.0


def deal_key(chat_id: int, message_id: int):
    return (chat_id, message_id)


def get_deal_from_message(message: Message):
    return deals.get(deal_key(message.chat.id, message.message_id))


def get_deal_from_reply(message: Message):
    if not message.reply_to_message:
        return None, None

    direct_key = deal_key(message.chat.id, message.reply_to_message.message_id)
    if direct_key in deals:
        return direct_key, deals[direct_key]

    nested = getattr(message.reply_to_message, "reply_to_message", None)
    if nested:
        nested_key = deal_key(message.chat.id, nested.message_id)
        if nested_key in deals:
            return nested_key, deals[nested_key]

    return None, None


def kb_take():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🛠 Взять в работу", callback_data="take")]]
    )


def kb_working():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить сделку", callback_data="cancel")],
            [InlineKeyboardButton(text="🔄 Освободить сделку", callback_data="release")],
        ]
    )


def kb_close():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить курс", callback_data="edit_rate")],
            [
                InlineKeyboardButton(text="✅ Завершить", callback_data="close"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="cancel"),
            ],
            [InlineKeyboardButton(text="🔄 Освободить сделку", callback_data="release")],
        ]
    )


def render_amount_line(deal) -> str:
    return format_money(deal["pay_amount"], deal.get("pay_currency", ""))


def render_free_card(deal) -> str:
    return (
        f"📋 Статус: Свободна\n\n"
        f"🆔 Сделка: {deal['deal_id']}\n\n"
        f"💱 Курс сделки: {deal['deal_rate']}\n"
        f"💵 Сумма сделки: {render_amount_line(deal)}"
    )


def render_working_card(deal) -> str:
    return (
        f"📋 Статус: В работе\n\n"
        f"🆔 Сделка: {deal['deal_id']}\n\n"
        f"👤 Исполнитель: {deal['worker_name']}\n\n"
        f"💱 Курс сделки: {deal['deal_rate']}\n"
        f"💵 Сумма сделки: {render_amount_line(deal)}\n\n"
        f"Ответьте на это сообщение фактическим курсом."
    )


def render_awaiting_close_card(deal) -> str:
    return (
        f"📋 Статус: Ожидает закрытия\n\n"
        f"🆔 Сделка: {deal['deal_id']}\n\n"
        f"👤 Исполнитель: {deal['worker_name']}\n\n"
        f"💱 Курс сделки: {deal['deal_rate']}\n"
        f"💸 Фактический курс: {deal['fact_rate']}\n"
        f"💵 Сумма сделки: {render_amount_line(deal)}\n"
        f"💰 Профит: ${deal['profit']:.2f}"
    )


def render_cancelled_card(deal) -> str:
    return (
        f"❌ Сделка отменена\n\n"
        f"🆔 Сделка: {deal['deal_id']}\n\n"
        f"👤 Исполнитель: {deal.get('worker_name', '-')}\n"
        f"📨 Автор: {deal.get('author_name', '-')}"
    )


def render_closed_card(deal) -> str:
    return (
        f"✅ Сделка завершена\n\n"
        f"🆔 Сделка: {deal['deal_id']}\n\n"
        f"👤 Исполнитель: {deal['worker_name']}\n"
        f"📨 Автор: {deal['author_name']}\n\n"
        f"💱 Курс сделки: {deal['deal_rate']}\n"
        f"💸 Фактический курс: {deal['fact_rate']}\n"
        f"💵 Сумма сделки: {render_amount_line(deal)}\n"
        f"💰 Профит: ${deal['profit']:.2f}"
    )


async def stats_by_field(field: str, title: str, start_date: date, end_date: date):
    assert pool is not None
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    if field == "worker":
        sql = """
            SELECT
                worker_id AS uid,
                COALESCE(MAX(NULLIF(worker_name, '')), 'id' || worker_id::text) AS name,
                COUNT(*) AS cnt
            FROM completed_deals
            WHERE date BETWEEN $1 AND $2
            GROUP BY worker_id
            ORDER BY cnt DESC, name ASC
        """
    else:
        sql = """
            SELECT
                author_id AS uid,
                COALESCE(MAX(NULLIF(author_name, '')), 'id' || author_id::text) AS name,
                COUNT(*) AS cnt
            FROM completed_deals
            WHERE date BETWEEN $1 AND $2
            GROUP BY author_id
            ORDER BY cnt DESC, name ASC
        """

    rows = await pool.fetch(sql, start_date, end_date)
    period_line = (
        f"📅 {start_date:%d.%m.%Y}"
        if start_date == end_date
        else f"📅 {start_date:%d.%m.%Y} — {end_date:%d.%m.%Y}"
    )

    if not rows:
        return f"{title}\n{period_line}\n\nНет данных"

    lines = [title, period_line, ""]
    for idx, row in enumerate(rows, start=1):
        lines.append(f"{idx}. {row['name']} — {row['cnt']}")

    return "\n".join(lines)


async def profit_report(start_date: date, end_date: date, title: str):
    assert pool is not None
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    rows = await pool.fetch(
        """
        SELECT
            author_id AS uid,
            COALESCE(MAX(NULLIF(author_name, '')), 'id' || author_id::text) AS name,
            COUNT(*) AS cnt,
            COALESCE(SUM(profit), 0) AS profit
        FROM completed_deals
        WHERE date BETWEEN $1 AND $2
        GROUP BY author_id
        ORDER BY profit DESC, cnt DESC, name ASC
        """,
        start_date,
        end_date,
    )

    period_line = (
        f"📅 {start_date:%d.%m.%Y}"
        if start_date == end_date
        else f"📅 {start_date:%d.%m.%Y} — {end_date:%d.%m.%Y}"
    )

    if not rows:
        return f"{title}\n{period_line}\n\nНет данных"

    lines = [title, period_line, ""]
    total_profit = 0.0
    total_deals = 0

    for idx, row in enumerate(rows, start=1):
        profit = float(row["profit"] or 0)
        cnt = int(row["cnt"] or 0)
        lines.append(
            f"{idx}. {row['name']}\n"
            f"Сделок: {cnt}\n"
            f"Профит: ${profit:.2f}\n"
        )
        total_profit += profit
        total_deals += cnt

    lines.append("────────────────")
    lines.append(f"💵 Общий профит: ${total_profit:.2f}")
    lines.append(f"📊 Всего сделок: {total_deals}")
    return "\n".join(lines)


async def count_completed_between(start_date: date, end_date: date) -> int:
    assert pool is not None
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return int(
        await pool.fetchval(
            "SELECT COUNT(*) FROM completed_deals WHERE date BETWEEN $1 AND $2",
            start_date,
            end_date
        ) or 0
    )


async def deal_exists(deal_id: str) -> bool:
    assert pool is not None
    active = await pool.fetchval("SELECT 1 FROM active_deals WHERE deal_id = $1", deal_id)
    if active:
        return True
    completed = await pool.fetchval("SELECT 1 FROM completed_deals WHERE deal_id = $1", deal_id)
    return bool(completed)


@dp.message()
async def handle_messages(message: Message):
    content = (message.text or message.caption or "").strip()
    if not content:
        return

    if content.startswith("/week_workers"):
        await message.answer(
            await stats_by_field("worker", "👨‍💼 Исполнители за неделю", current_date() - timedelta(days=6), current_date())
        )
        return

    if content.startswith("/week_authors"):
        await message.answer(
            await stats_by_field("author", "📝 Авторы за неделю", current_date() - timedelta(days=6), current_date())
        )
        return

    if content.startswith("/week_profit"):
        await message.answer(
            await profit_report(current_date() - timedelta(days=6), current_date(), "💰 Профит за неделю")
        )
        return

    if content.startswith("/yesterday_profit"):
        y = current_date() - timedelta(days=1)
        await message.answer(await profit_report(y, y, "💰 Профит за вчера"))
        return

    if content.startswith("/period"):
        parts = content.split()
        if len(parts) != 3:
            await message.answer("Используй: /period DD.MM.YYYY DD.MM.YYYY")
            return

        start_date = parse_input_date(parts[1])
        end_date = parse_input_date(parts[2])
        if not start_date or not end_date:
            await message.answer("Не удалось прочитать даты. Формат: DD.MM.YYYY")
            return

        await message.answer(await profit_report(start_date, end_date, "💰 Профит за период"))
        return

    if content.startswith("/workers"):
        await message.answer(
            await stats_by_field("worker", "👨‍💼 Исполнители за сегодня", current_date(), current_date())
        )
        return

    if content.startswith("/authors"):
        await message.answer(
            await stats_by_field("author", "📝 Авторы за сегодня", current_date(), current_date())
        )
        return

    if content.startswith("/profit"):
        await message.answer(
            await profit_report(current_date(), current_date(), "💰 Профит за сегодня")
        )
        return

    if content.startswith("/today"):
        await message.answer(
            f"📊 Сегодня\n\n✅ Завершено сделок: {await count_completed_between(current_date(), current_date())}"
        )
        return

    if content.startswith("/week"):
        await message.answer(
            f"📊 За неделю\n\n✅ Завершено сделок: {await count_completed_between(current_date() - timedelta(days=6), current_date())}"
        )
        return

    if message.reply_to_message:
        deal_message_id, deal = get_deal_from_reply(message)

        if deal and deal.get("state") in ("awaiting_rate", "editing_rate"):
            if message.from_user.id != deal["worker_id"]:
                return

            fact_rate = extract_fact_rate(content)
            if not fact_rate:
                await message.reply("Отправь курс цифрами. Например: 44.2")
                return

            deal["fact_rate"] = fact_rate
            deal["fact_rate_value"] = parse_first_number(fact_rate)
            deal["profit"] = calc_profit(
                deal["pay_amount"],
                deal["deal_rate_value"],
                deal["fact_rate_value"],
            )
            deal["state"] = "awaiting_close"
            deal["closed"] = False

            await upsert_active_deal(deal)

            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=deal_message_id[1],
                text=render_awaiting_close_card(deal),
                reply_markup=kb_close(),
            )
            return

    if "Сделка #" in content:
        deal_rate = get_deal_rate(content)
        deal_rate_value = parse_first_number(deal_rate)
        deal_id = get_deal_id(content)
        pay_amount, pay_currency = get_deal_amount(content)

        if deal_id == "UNKNOWN" or deal_rate == "Не найден" or pay_amount <= 0:
            await message.reply("Не удалось распознать сделку. Проверь формат сообщения.")
            return

        if await deal_exists(deal_id):
            await message.reply("Сделка уже зарегистрирована.")
            return

        service = await message.reply(
            f"📋 Статус: Свободна\n\n"
            f"🆔 Сделка: {deal_id}\n\n"
            f"💱 Курс сделки: {deal_rate}\n"
            f"💵 Сумма сделки: {format_money(pay_amount, pay_currency)}",
            reply_markup=kb_take(),
        )

        deal = {
            "chat_id": message.chat.id,
            "service_message_id": service.message_id,
            "state": "free",
            "deal_id": deal_id,
            "author_id": message.from_user.id,
            "author_name": user_label(message.from_user),
            "worker_id": 0,
            "worker_name": "",
            "deal_rate": deal_rate,
            "deal_rate_value": deal_rate_value,
            "fact_rate": "",
            "fact_rate_value": 0.0,
            "pay_amount": pay_amount,
            "pay_currency": pay_currency,
            "profit": 0.0,
            "closed": False,
        }

        deals[(message.chat.id, service.message_id)] = deal
        await upsert_active_deal(deal)
        return


@dp.callback_query(F.data == "take")
async def take_deal(callback: CallbackQuery):
    deal = get_deal_from_message(callback.message)
    if not deal:
        await callback.answer("Сделка уже завершена или отменена.", show_alert=True)
        return

    if deal["state"] != "free":
        await callback.answer("Сделка уже занята", show_alert=True)
        return

    deal["worker_id"] = callback.from_user.id
    deal["worker_name"] = user_label(callback.from_user)
    deal["state"] = "awaiting_rate"
    deal["closed"] = False

    await upsert_active_deal(deal)

    await callback.message.edit_text(
        render_working_card(deal),
        reply_markup=kb_working(),
    )

    await callback.answer()


@dp.callback_query(F.data == "edit_rate")
async def edit_rate(callback: CallbackQuery):
    deal = get_deal_from_message(callback.message)
    if not deal:
        await callback.answer("Сделка уже завершена или отменена.", show_alert=True)
        return

    if deal["state"] != "awaiting_close":
        await callback.answer("Изменить курс можно только после ввода фактического курса.", show_alert=True)
        return

    if callback.from_user.id != deal["worker_id"]:
        await callback.answer("❌ Только исполнитель может изменить курс.", show_alert=True)
        return

    deal["state"] = "editing_rate"
    await upsert_active_deal(deal)
    await callback.answer("Отправьте новый курс ответом на это сообщение.")


@dp.callback_query(F.data == "release")
async def release_deal(callback: CallbackQuery):
    deal = get_deal_from_message(callback.message)
    if not deal:
        await callback.answer("Сделка уже завершена или отменена.", show_alert=True)
        return

    if callback.from_user.id != deal["worker_id"]:
        await callback.answer("❌ Только исполнитель может освободить эту сделку.", show_alert=True)
        return

    deal["state"] = "free"
    deal["worker_id"] = 0
    deal["worker_name"] = ""
    deal["fact_rate"] = ""
    deal["fact_rate_value"] = 0.0
    deal["profit"] = 0.0
    deal["closed"] = False

    await upsert_active_deal(deal)

    await callback.message.edit_text(
        render_free_card(deal),
        reply_markup=kb_take(),
    )

    await callback.answer("Сделка освобождена")


@dp.callback_query(F.data == "cancel")
async def cancel_deal(callback: CallbackQuery):
    deal = get_deal_from_message(callback.message)
    if not deal:
        await callback.answer("Сделка уже завершена или отменена.", show_alert=True)
        return

    if callback.from_user.id != deal["author_id"]:
        await callback.answer("❌ Только автор может отменить эту сделку.", show_alert=True)
        return

    await delete_active_deal(deal["chat_id"], deal["service_message_id"])
    deals.pop((deal["chat_id"], deal["service_message_id"]), None)

    await callback.message.edit_text(render_cancelled_card(deal), reply_markup=None)
    await callback.answer("Сделка отменена")


@dp.callback_query(F.data == "close")
async def close_deal(callback: CallbackQuery):
    deal = get_deal_from_message(callback.message)
    if not deal:
        await callback.answer("Сделка уже завершена или отменена.", show_alert=True)
        return

    if deal["state"] != "awaiting_close":
        await callback.answer("Сначала укажите курс", show_alert=True)
        return

    if callback.from_user.id != deal["author_id"]:
        await callback.answer("❌ Только автор может завершить эту сделку.", show_alert=True)
        return

    if deal.get("closed"):
        await callback.answer("Сделка уже закрыта", show_alert=True)
        return

    deal["closed"] = True
    deal["state"] = "closed"
    deal["profit"] = calc_profit(
        deal["pay_amount"],
        deal["deal_rate_value"],
        deal["fact_rate_value"],
    )

    await insert_completed_deal(deal)
    await callback.message.edit_text(render_closed_card(deal), reply_markup=None)

    deals.pop((deal["chat_id"], deal["service_message_id"]), None)
    await callback.answer("Сделка закрыта")


async def main():
    global pool
    print("WORK BOT STARTED")

    dsn = get_dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL or PG* variables are not set")

    pool = await asyncpg.create_pool(
        dsn=dsn,
        ssl=get_ssl_setting(),
        min_size=1,
        max_size=5,
        command_timeout=60,
    )

    await init_db()
    await migrate_legacy_json_logs()
    await load_active_deals()

    await bot.delete_webhook(drop_pending_updates=True)

    me = await bot.get_me()
    print(f"BOT USERNAME: @{me.username}")
    print(f"BOT ID: {me.id}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
