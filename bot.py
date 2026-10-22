def format_eta(eta_dict: dict) -> str:
    eta_str = eta_dict.get("eta", "")
    remark = eta_dict.get("remark", {}).get("zh") or eta_dict.get("remark", {}).get("en", "")
    co = eta_dict.get("co", "").upper()

    try:
        dt = datetime.fromisoformat(eta_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        mins = max(0, int((dt - now).total_seconds() // 60))
        if mins == 0:
            time_display = "即將到站"
        elif mins < 60:
            time_display = f"{mins} 分鐘"
        else:
            time_display = dt.strftime("%H:%M")
    except Exception:
        time_display = "—"

    remark_part = f" {remark}" if remark else ""
    return f"{time_display}{remark_part}"

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
    route_num = route.get("route", "")

    lines = [f"<b>{route_num}</b> {orig} → {dest}\n"]

    # Only show first 4 stops by default
    for seq in range(min(4, len(stops))):
        stop_id = stops[seq]
        zh, en = stop_names.get(stop_id, ("", ""))
        stop_name = zh or en or f"站 {seq+1}"

        try:
            etas = hketa.getEtas(route_id=route_id, seq=seq, language="zh")
            if etas:
                eta_lines = []
                for e in etas[:2]:  # only 2 ETAs per stop
                    eta_lines.append(format_eta(e))
                eta_str = "  |  ".join(eta_lines)
            else:
                eta_str = "暫無班次"
        except Exception:
            eta_str = "查詢失敗"

        lines.append(f"<b>{seq+1}.</b> {stop_name}\n{eta_str}")

    text = "\n".join(lines)
    await query.edit_message_text(text, parse_mode="HTML")

async def show_stop_etas(query, stop_id: str):
    zh, en = stop_names.get(stop_id, ("", ""))
    stop_name = zh or en or stop_id

    lines = [f"<b>{stop_name}</b>\n"]

    found = []
    for entries in stop_to_routes.values():
        for route_id, seq, company in entries:
            stops = hketa.route_list.get(route_id, {}).get("stops", {}).get(company, [])
            if seq < len(stops) and stops[seq] == stop_id:
                found.append((route_id, seq, company))

    unique = list({(r, s, c) for r, s, c in found})[:10]

    if not unique:
        await query.edit_message_text(f"{stop_name}\n\n暫無路線資料。")
        return

    for route_id, seq, company in unique:
        route = hketa.route_list.get(route_id, {})
        route_num = route.get("route", "?")
        dest = route.get("dest", {}).get("zh", "")

        try:
            etas = hketa.getEtas(route_id=route_id, seq=seq, language="zh")
            if etas:
                eta_lines = [format_eta(e) for e in etas[:2]]
                eta_str = "  |  ".join(eta_lines)
            else:
                eta_str = "暫無班次"
        except Exception:
            eta_str = "失敗"

        lines.append(f"<b>{route_num}</b> → {dest}\n{eta_str}\n")

    text = "\n".join(lines).strip()
    if len(text) > 4000:
        text = text[:3990] + "\n..."

    await query.edit_message_text(text, parse_mode="HTML")
