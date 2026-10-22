import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from hk_bus_eta import HKEta

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MAX_RESULTS = 8          # Max routes/stops to show
MAX_ETAS_PER_STOP = 3    # Max ETAs to display per stop
# ============================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Global data
hketa: HKEta = None
# Reverse index: stop_name_lower -> List[(route_id, seq, company)]
stop_to_routes: Dict[str, List[Tuple[str, int, str]]] = defaultdict(list)
# stop_id -> (zh_name, en_name)
stop_names: Dict[str, Tuple[str, str]] = {}

def build_reverse_index():
    """Build stop name → routes reverse index on startup."""
    global stop_to_routes, stop_names

    logger.info("Building reverse index for stops...")
    stop_to_routes.clear()
    stop_names.clear()

    for route_id, route in hketa.route_list.items():
        co_list = route.get("co", [])
        if not co_list:
            continue

        # Use the first company as the primary one for stop lookup
        company = co_list[0]
        stops = route.get("stops", {}).get(company, [])

        for seq, stop_id in enumerate(stops):
            stop_info = hketa.stop_list.get(stop_id)
            if not stop_info:
                continue

            name_zh = stop_info.get("name", {}).get("zh", "").strip()
            name_en = stop_info.get("name", {}).get("en", "").strip()

            if stop_id not in stop_names:
                stop_names[stop_id] = (name_zh, name_en)

            # Index both Chinese and English names
            for name in (name_zh, name_en):
                if name:
                    key = name.lower()
                    stop_to_routes[key].append((route_id, seq, company))

    logger.info(f"Reverse index built. Unique stop names: {len(stop_to_routes)}")

def format_eta(eta_dict: dict) -> str:
    """Format a single ETA entry nicely."""
    eta_str = eta_dict.get("eta", "")
    remark = eta_dict.get("remark", {}).get("zh") or eta_dict.get("remark", {}).get("en", "")
    co = eta_dict.get("co", "").upper()

    try:
        # Try to parse and show minutes remaining
        dt = datetime.fromisoformat(eta_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        mins = max(0, int((dt - now).total_seconds() // 60))
        time_display = f"{mins} 分鐘" if mins > 0 else "即將到站"
    except Exception:
        time_display = eta_str

    remark_part = f" ({remark})" if remark else ""
    return f"{time_display}{remark_part} [{co}]"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "香港交通 ETA Bot\n\n"
        "直接輸入：\n"
        "• 路線號碼（例如：1A、690、TCL）\n"
        "• 站名（例如：尖沙咀碼頭、Central、銅鑼灣）\n\n"
        "我會回傳即將到站的班次。"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
        return

    query_lower = query.lower()

    # 1. Try exact / partial route number match first
    matching_routes = []
    for rid in hketa.route_list:
        route_num = hketa.route_list[rid].get("route", "")
        if query.upper() == route_num.upper() or query.upper() in rid.upper():
            matching_routes.append(rid)
        if len(matching_routes) >= MAX_RESULTS:
            break

    if matching_routes:
        await show_routes(update, matching_routes, query)
        return

    # 2. Stop name search (using reverse index)
    matching_stops = set()
    for name_key, entries in stop_to_routes.items():
        if query_lower in name_key:
            matching_stops.add(name_key)
        if len(matching_stops) >= MAX_RESULTS:
            break

    if matching_stops:
        await show_stops(update, list(matching_stops), query)
        return

    await update.message.reply_text(f"找不到與「{query}」相關的路線或站點。")

async def show_routes(update: Update, route_ids: List[str], query: str):
    lines = [f"找到路線：{query}\n"]
    keyboard = []

    for rid in route_ids[:MAX_RESULTS]:
        route = hketa.route_list[rid]
        orig = route.get("orig", {}).get("zh", "")
        dest = route.get("dest", {}).get("zh", "")
        co = "/".join(route.get("co", [])).upper()

        lines.append(f"• {route.get('route')} {orig} → {dest} [{co}]")
        keyboard.append([
            InlineKeyboardButton(
                f"{route.get('route')} {orig[:8]}→{dest[:8]}",
                callback_data=f"route:{rid}"
            )
        ])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text("\n".join(lines), reply_markup=reply_markup)

async def show_stops(update: Update, name_keys: List[str], query: str):
    lines = [f"找到站點：{query}\n"]
    keyboard = []

    seen_stops = set()
    for name_key in name_keys[:MAX_RESULTS]:
        entries = stop_to_routes.get(name_key, [])
        if not entries:
            continue

        # Pick the first occurrence to get a representative stop_id
        route_id, seq, company = entries[0]
        stops = hketa.route_list[route_id].get("stops", {}).get(company, [])
        if seq >= len(stops):
            continue
        stop_id = stops[seq]
        if stop_id in seen_stops:
            continue
        seen_stops.add(stop_id)

        zh, en = stop_names.get(stop_id, (name_key, name_key))
        display = zh or en
        lines.append(f"• {display}")
        keyboard.append([
            InlineKeyboardButton(display, callback_data=f"stop:{stop_id}")
        ])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text("\n".join(lines), reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("route:"):
        route_id = data.split(":", 1)[1]
        await show_route_etas(query, route_id)
    elif data.startswith("stop:"):
        stop_id = data.split(":", 1)[1]
        await show_stop_etas(query, stop_id)

async def show_route_etas(query, route_id: str):
    route = hketa.route_list.get(route_id)
    if not route:
        await query.edit_message_text("路線資料已過期，請重新查詢。")
        return

    co_list = route.get("co", [])
    company = co_list[0] if co_list else ""
    stops = route.get("stops", {}).get(company, [])
    orig = route.get("orig", {}).get("zh", "")
    dest = route.get("dest", {}).get("zh", "")

    lines = [f"{route.get('route')} {orig} → {dest}\n"]

    # Show first 6 stops with ETAs (to avoid too long message)
    for seq in range(min(6, len(stops))):
        stop_id = stops[seq]
        zh, en = stop_names.get(stop_id, ("", ""))
        stop_name = zh or en or f"Stop {seq+1}"

        try:
            etas = hketa.getEtas(route_id=route_id, seq=seq, language="zh")
            eta_texts = [format_eta(e) for e in etas[:MAX_ETAS_PER_STOP]]
            eta_str = " | ".join(eta_texts) if eta_texts else "暫無資料"
        except Exception as e:
            eta_str = "查詢失敗"

        lines.append(f"{seq+1}. {stop_name}\n   {eta_str}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "\n..."

    await query.edit_message_text(text)

async def show_stop_etas(query, stop_id: str):
    zh, en = stop_names.get(stop_id, ("", ""))
    stop_name = zh or en or stop_id

    lines = [f"站點：{stop_name}\n"]

    # Find all routes that stop here
    found = []
    for name_key, entries in stop_to_routes.items():
        for route_id, seq, company in entries:
            stops = hketa.route_list.get(route_id, {}).get("stops", {}).get(company, [])
            if seq < len(stops) and stops[seq] == stop_id:
                found.append((route_id, seq, company))

    # Deduplicate
    unique = list({(r, s, c) for r, s, c in found})[:12]

    if not unique:
        await query.edit_message_text(f"{stop_name}\n\n暫無路線資料。")
        return

    for route_id, seq, company in unique:
        route = hketa.route_list.get(route_id, {})
        route_num = route.get("route", "?")
        dest = route.get("dest", {}).get("zh", "")

        try:
            etas = hketa.getEtas(route_id=route_id, seq=seq, language="zh")
            eta_texts = [format_eta(e) for e in etas[:MAX_ETAS_PER_STOP]]
            eta_str = " | ".join(eta_texts) if eta_texts else "暫無"
        except Exception:
            eta_str = "失敗"

        lines.append(f"• {route_num} → {dest}\n  {eta_str}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "\n..."

    await query.edit_message_text(text)

def main():
    global hketa
    logger.info("Loading HKEta data...")
    hketa = HKEta()
    build_reverse_index()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
