from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import os
import time
import pandas as pd
import pydeck as pdk
import requests
import streamlit as st
from FlightRadarAPI import FlightRadar24API

# --- 1. 頁面基本設定與初始化 ---
st.set_page_config(
    page_title="FlightRadar24 智慧航班監測 APP",
    page_icon="✈️",
    layout="wide",
)


@st.cache_resource
def init_api():
    return FlightRadar24API()


fr_api = init_api()


@st.cache_data(ttl=15, show_spinner=False)
def fetch_all_active_flights():
    try:
        flights = fr_api.get_flights()
        if flights:
            return flights
    except Exception:
        pass
    return []


@st.cache_resource
def get_http_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    })
    return session


http_session = get_http_session()


# --- 2. 輔助函式定義 ---
def load_targets_from_txt(filepath: str = "targets.txt") -> list[str]:
    """從 txt 檔案讀取監控清單 (逐行讀取、轉大寫、去空白)"""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            targets = [
                line.strip().upper()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
            return targets
    except Exception:
        return []


def format_full_datetime(ts: int | None) -> str:
    """將 Unix Timestamp 轉為 UTC+8 時區的完整日期與時間 (YYYY-MM-DD HH:MM)"""
    if not ts:
        return "未知"
    try:
        tz_tw = timezone(timedelta(hours=8))
        dt = datetime.fromtimestamp(ts, tz=tz_tw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "未知"


def check_is_taiwan(text_or_code: str) -> bool:
    """精準判斷機場代碼或名稱是否屬於台灣"""
    if not text_or_code or text_or_code == "未知":
        return False
    s = str(text_or_code).upper().strip()

    tw_airport_codes = {
        "TPE", "TSA", "KHH", "RMQ", "TNN", "HUN", "TTT", "MZG", "KIN", "CYI", "PIF", "LZN", "CMJ",
        "RCTP", "RCSS", "RCKH", "RCMQ", "RCNN", "RCHU", "RCFG", "RCBS", "RCFN", "RCKW", "RCMT", "RCLY"
    }

    if s in tw_airport_codes:
        return True

    if len(s) == 4 and s.startswith("RC"):
        return True

    tw_name_keywords = [
        "TAIPEI", "TAIWAN", "KAOHSIUNG", "TAICHUNG", "TAINAN",
        "台北", "台灣", "高雄", "台中", "台南"
    ]
    return any(kw in s for kw in tw_name_keywords)


def fetch_planespotters_image(registration: str) -> str | None:
    """從 Planespotters.net 免費 API 依據機身註冊號獲取照片"""
    if not registration or registration == "未知":
        return None
    try:
        url = f"https://api.planespotters.net/pub/photos/reg/{registration}"
        res = http_session.get(url, timeout=4)
        if res.status_code == 200:
            data = res.json()
            photos = data.get("photos", [])
            if photos:
                return (
                    photos[0].get("thumbnail_large", {}).get("src")
                    or photos[0].get("thumbnail", {}).get("src")
                )
    except Exception:
        pass
    return None


def fetch_direct_clickhandler(flight_obj_or_id) -> dict | None:
    """索取詳細飛行狀態、起降機場資訊、起飛與抵達時間、飛機圖片"""
    try:
        if hasattr(flight_obj_or_id, "id"):
            details = fr_api.get_flight_details(flight_obj_or_id)
        else:
            class DummyFlight:
                def __init__(self, fid):
                    self.id = fid

            details = fr_api.get_flight_details(DummyFlight(flight_obj_or_id))

        if not details or not isinstance(details, dict):
            return None

        airport = details.get("airport") or {}
        orig_obj = (airport.get("origin") or {}).get("code") or {}
        dest_obj = (airport.get("destination") or {}).get("code") or {}

        origin = (
            orig_obj.get("iata")
            or orig_obj.get("icao")
            or (airport.get("origin") or {}).get("name")
            or "未知"
        )
        destination = (
            dest_obj.get("iata")
            or dest_obj.get("icao")
            or (airport.get("destination") or {})
            .get("pluginData", {})
            .get("details", {})
            .get("name")
            or "未知"
        )

        pos = details.get("position") or {}
        trail = details.get("trail") or []
        latest_trail = trail[0] if trail else {}

        alt = (
            pos.get("altitude", {}).get("feet")
            if isinstance(pos.get("altitude"), dict)
            else (latest_trail.get("alt") or 0)
        )
        spd = (
            pos.get("speed", {}).get("kts")
            if isinstance(pos.get("speed"), dict)
            else (latest_trail.get("spd") or 0)
        )
        lat = pos.get("latitude") or latest_trail.get("lat") or 0.0
        lon = pos.get("longitude") or latest_trail.get("lng") or 0.0

        ident = details.get("identification") or {}
        f_num = (
            (ident.get("number") or {}).get("default")
            or (ident.get("callsign") or {}).get("default")
            or "未知"
        )

        ac = details.get("aircraft") or {}
        f_reg = ac.get("registration") or "未知"
        ac_code = (ac.get("model") or {}).get("code") or "未知"

        # 抓取起飛與抵達時間
        time_data = details.get("time") or {}
        
        # 起飛時間 (實際 ATD -> 預計 ETD -> 排定 STD)
        std_ts = (time_data.get("scheduled") or {}).get("departure")
        etd_ts = (time_data.get("estimated") or {}).get("departure")
        atd_ts = (time_data.get("real") or {}).get("departure")
        dep_full = format_full_datetime(atd_ts or etd_ts or std_ts)

        # 抵達時間 (預計 ETA -> 實際 ATA -> 排定 STA)
        sta_ts = (time_data.get("scheduled") or {}).get("arrival")
        eta_ts = (time_data.get("estimated") or {}).get("arrival")
        ata_ts = (time_data.get("real") or {}).get("arrival")
        eta_full = format_full_datetime(eta_ts or ata_ts or sta_ts)

        # 照片抓取邏輯
        image_url = None
        images = ac.get("images") or {}
        large_images = images.get("large") or images.get("medium") or []

        if large_images and isinstance(large_images, list) and len(large_images) > 0:
            image_url = large_images[0].get("src")

        if not image_url and f_reg != "未知":
            image_url = fetch_planespotters_image(f_reg)

        return {
            "origin": origin,
            "destination": destination,
            "alt": alt if alt is not None else 0,
            "spd": spd if spd is not None else 0,
            "lat": lat,
            "lon": lon,
            "f_num": f_num,
            "f_reg": f_reg,
            "ac_code": ac_code,
            "dep_time": dep_full,
            "eta_time": eta_full,
            "image_url": image_url,
        }
    except Exception:
        return None


def search_single_target_worker(target_raw: str, all_flights: list) -> dict | None:
    """單目標查詢 Worker"""
    target_clean = target_raw.replace("-", "")

    flight_map_by_id = {
        getattr(f, "id", ""): f for f in all_flights if getattr(f, "id", "")
    }

    # 1. 廣播數據比對
    for flight in all_flights:
        f_num = (getattr(flight, "number", "") or "").upper()
        f_callsign = (getattr(flight, "callsign", "") or "").upper()
        f_reg = (getattr(flight, "registration", "") or "").upper()

        f_num_c = f_num.replace("-", "")
        f_callsign_c = f_callsign.replace("-", "")
        f_reg_c = f_reg.replace("-", "")

        matched = target_raw in [f_num, f_callsign, f_reg] or target_clean in [
            f_num_c,
            f_callsign_c,
            f_reg_c,
        ]

        if matched:
            details = fetch_direct_clickhandler(flight)
            if details:
                origin = details["origin"]
                destination = details["destination"]
                is_taiwan_dest = check_is_taiwan(destination)
                is_taiwan_orig = check_is_taiwan(origin)

                return {
                    "監控目標": target_raw,
                    "機身照片": details["image_url"],
                    "航班號": details["f_num"] if details["f_num"] != "未知" else (f_num or f_callsign),
                    "機身註冊號": details["f_reg"] if details["f_reg"] != "未知" else (f_reg or target_raw),
                    "機型": details["ac_code"],
                    "航線 (出發➔到達)": f"{origin} ➔ {destination}",
                    "起飛時間 (UTC+8)": details["dep_time"],
                    "預計抵達 (UTC+8)": details["eta_time"],
                    "高度 (ft)": details["alt"],
                    "地速 (kts)": details["spd"],
                    "台灣起飛": "🛫 台灣起飛" if is_taiwan_orig else "否",
                    "降落台灣": "🇹🇼 降落台灣" if is_taiwan_dest else "否",
                    "資料來源": "📡 直播廣播",
                    "lat": details["lat"],
                    "lon": details["lon"],
                    "_is_taiwan_orig": is_taiwan_orig,
                    "_is_taiwan_dest": is_taiwan_dest,
                    "_is_taiwan": is_taiwan_orig or is_taiwan_dest,
                }

    # 2. 線上反查
    search_url = f"https://www.flightradar24.com/v1/search/web/find?query={target_raw}"

    try:
        res = http_session.get(search_url, timeout=4)
        if res.status_code == 200:
            results = sorted(
                res.json().get("results", []),
                key=lambda x: (x.get("type") != "live", str(x.get("id", ""))),
            )
            for item in results:
                if item.get("type") == "live":
                    live_id = str(item.get("id", "")).strip()
                    if live_id:
                        target_obj = flight_map_by_id.get(live_id, live_id)
                        details = fetch_direct_clickhandler(target_obj)

                        if details:
                            origin = details["origin"]
                            destination = details["destination"]
                            is_taiwan_dest = check_is_taiwan(destination)
                            is_taiwan_orig = check_is_taiwan(origin)

                            return {
                                "監控目標": target_raw,
                                "機身照片": details["image_url"],
                                "航班號": details["f_num"] if details["f_num"] != "未知" else target_raw,
                                "機身註冊號": details["f_reg"] if details["f_reg"] != "未知" else target_raw,
                                "機型": details["ac_code"],
                                "航線 (出發➔到達)": f"{origin} ➔ {destination}",
                                "起飛時間 (UTC+8)": details["dep_time"],
                                "預計抵達 (UTC+8)": details["eta_time"],
                                "高度 (ft)": details["alt"],
                                "地速 (kts)": details["spd"],
                                "台灣起飛": "🛫 台灣起飛" if is_taiwan_orig else "否",
                                "降落台灣": "🇹🇼 降落台灣" if is_taiwan_dest else "否",
                                "資料來源": "🔍 Web API 詳細反查",
                                "lat": details["lat"],
                                "lon": details["lon"],
                                "_is_taiwan_orig": is_taiwan_orig,
                                "_is_taiwan_dest": is_taiwan_dest,
                                "_is_taiwan": is_taiwan_orig or is_taiwan_dest,
                            }
    except Exception:
        pass

    return None


# --- 3. UI 介面與側邊欄設定 ---
st.title("✈️ FlightRadar24 彩繪機降落台灣監測")

if "matched_dict" not in st.session_state:
    st.session_state["matched_dict"] = {}

# 從 targets.txt 讀取預設目標
DEFAULT_TARGETS = load_targets_from_txt("targets.txt")
default_text_value = "\n".join(DEFAULT_TARGETS)

with st.sidebar:
    st.header("⚙️ 監控清單")
    
    if DEFAULT_TARGETS:
        st.caption(f"📁 已從 `targets.txt` 載入 {len(DEFAULT_TARGETS)} 架預設目標")
    else:
        st.warning("⚠️ 未偵測到 `targets.txt` 或檔案內容為空白")

    st.info("💡 輸入「機身編號/註冊號」")

    flight_input = st.text_area(
        "飛機代碼清單 (每行一班)",
        value=default_text_value,
        height=280,
        placeholder="請輸入機號（每行一個，例如：\nB-KQU\nB-LRJ\nHL7628）"
    )

    targets = [f.strip().upper() for f in flight_input.split("\n") if f.strip()]

    currently_found = set(st.session_state["matched_dict"].keys())
    currently_unmatched = [t for t in targets if t not in currently_found]

    st.divider()

    full_search_button = st.button(
        "🔍 依輸入清單重新搜尋",
        type="primary",
        use_container_width=True,
    )

    unmatched_count = len(currently_unmatched)
    rescan_unmatched = st.button(
        f"⚡ 併行輪詢補查「未查到」 ({unmatched_count} 架)",
        type="secondary",
        use_container_width=True,
        disabled=(unmatched_count == 0),
    )


# 多執行緒輪詢邏輯
def run_scan_process_until_stable(
    all_targets: list[str],
    is_full_rescan: bool = False,
    stable_threshold: int = 10,
    max_workers: int = 8,
):
    if is_full_rescan:
        st.session_state["matched_dict"] = {}

    status_info = st.empty()
    progress_bar = st.progress(0)

    last_unmatched_count = -1
    stable_counter = 0
    current_round = 0

    while True:
        current_round += 1
        matched_keys = set(st.session_state["matched_dict"].keys())
        pending_targets = [t for t in all_targets if t not in matched_keys]
        current_unmatched_count = len(pending_targets)

        if current_unmatched_count == 0:
            status_info.success("🎉 所有監控目標皆已成功定位！")
            break

        if current_unmatched_count == last_unmatched_count:
            stable_counter += 1
        else:
            stable_counter = 1
            last_unmatched_count = current_unmatched_count

        if stable_counter >= stable_threshold:
            status_info.success(
                f"✅ 未查到數量已連續 {stable_threshold} 輪維持在 "
                f"{current_unmatched_count} 架，數據已達穩定狀態！"
            )
            time.sleep(1)
            break

        status_info.info(
            f"⚡ [併行加速中] 第 {current_round} 輪掃描... "
            f"（剩餘未查到：{current_unmatched_count} 架 | 穩定進度：{stable_counter}/{stable_threshold}）"
        )

        fetch_all_active_flights.clear()
        snapshot = fetch_all_active_flights()

        total_pending = len(pending_targets)
        completed_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_target = {
                executor.submit(
                    search_single_target_worker, target, snapshot
                ): target
                for target in pending_targets
            }

            for future in as_completed(future_to_target):
                target = future_to_target[future]
                try:
                    res = future.result()
                    if res:
                        st.session_state["matched_dict"][target] = res
                except Exception:
                    pass

                completed_count += 1
                progress_bar.progress(completed_count / total_pending)

        time.sleep(0.3)

    progress_bar.empty()
    status_info.empty()


# 觸發邏輯
if "has_run_once" not in st.session_state:
    st.session_state["has_run_once"] = True
    run_scan_process_until_stable(targets, is_full_rescan=True)
    st.rerun()

elif full_search_button:
    if "flight_table" in st.session_state:
        del st.session_state["flight_table"]

    run_scan_process_until_stable(targets, is_full_rescan=True)
    st.rerun()

elif rescan_unmatched and currently_unmatched:
    if "flight_table" in st.session_state:
        del st.session_state["flight_table"]

    run_scan_process_until_stable(currently_unmatched, is_full_rescan=False)
    st.rerun()


# --- 4. 數據彙整與畫面顯示區塊 ---
matched_list = list(st.session_state["matched_dict"].values())
df_matched = pd.DataFrame(matched_list) if matched_list else pd.DataFrame()

matched_targets_set = set(st.session_state["matched_dict"].keys())
unmatched_targets = [t for t in targets if t not in matched_targets_set]

taiwan_dest_count = (
    int(df_matched["_is_taiwan_dest"].sum())
    if (not df_matched.empty and "_is_taiwan_dest" in df_matched.columns)
    else 0
)

taiwan_orig_count = (
    int(df_matched["_is_taiwan_orig"].sum())
    if (not df_matched.empty and "_is_taiwan_orig" in df_matched.columns)
    else 0
)

# 頂部數據看板
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("監控目標總數", f"{len(targets)} 架")
col2.metric("在空中 / 飛行中", f"{len(df_matched)} 架")
col3.metric("🇹🇼 台灣起飛", f"{taiwan_orig_count} 架")
col4.metric("🇹🇼 降落台灣", f"{taiwan_dest_count} 架")
col5.metric("未查到 / 尚未起飛", f"{len(unmatched_targets)} 架")

if taiwan_dest_count > 0 or taiwan_orig_count > 0:
    st.success(
        f"### 🇹🇼 即時警報：共有 **{taiwan_dest_count}** 架預計/已降落台灣，"
        f"**{taiwan_orig_count}** 架從台灣起飛！"
    )

st.divider()

# --- 1. 在空中航班（地圖 + 表格 + 照片預覽） ---
if not df_matched.empty:
    df_sorted = (
        df_matched.sort_values(
            by=["_is_taiwan_orig", "_is_taiwan_dest", "監控目標"],
            ascending=[False, False, True],
        )
        .reset_index(drop=True)
    )

    # 地圖圓點顏色計算 (台灣起飛:綠色, 降落台灣:紅色, 其他:灰色)
    def assign_marker_color(row):
        if row.get("_is_taiwan_orig"):
            return [46, 204, 113, 230]  # 🟢 綠色 (台灣起飛)
        elif row.get("_is_taiwan_dest"):
            return [230, 57, 70, 230]   # 🔴 紅色 (降落台灣)
        return [148, 163, 184, 200]     # 🩶 灰色

    df_matched["marker_color"] = df_matched.apply(assign_marker_color, axis=1)

    center_lat = df_matched["lat"].mean()
    center_lon = df_matched["lon"].mean()
    zoom_level = 2.2
    selected_row = None

    if (
        "flight_table" in st.session_state
        and st.session_state["flight_table"].get("selection", {}).get("rows")
    ):
        selected_rows = st.session_state["flight_table"]["selection"]["rows"]
        if selected_rows:
            selected_idx = selected_rows[0]
            if selected_idx < len(df_sorted):
                selected_row = df_sorted.iloc[selected_idx]
                center_lat = selected_row["lat"]
                center_lon = selected_row["lon"]
                zoom_level = 7.5

    st.subheader("🗺️ 飛機即時位置雷達地圖")

    if selected_row is not None:
        st.info(f"🎯 **已定位至航班：{selected_row['航班號']} ({selected_row['機身註冊號']})**")

        detail_col1, detail_col2 = st.columns([1, 2])
        with detail_col1:
            if selected_row.get("機身照片"):
                st.image(selected_row["機身照片"], caption=f"機身註冊號：{selected_row['機身註冊號']}", use_container_width=True)
            else:
                st.warning("📷 尚無此機身之公開照片庫資料")

        with detail_col2:
            st.markdown(
                f"- **航班號**：`{selected_row.get('航班號', '未知')}`\n"
                f"- **機身註冊號**：`{selected_row.get('機身註冊號', '未知')}`\n"
                f"- **機型**：`{selected_row.get('機型', '未知')}`\n"
                f"- **航線**：**{selected_row.get('航線 (出發➔到達)', '未知')}**\n"
                f"- **起飛時間 (UTC+8)**：`{selected_row.get('起飛時間 (UTC+8)', '未知')}`\n"
                f"- **預計抵達時間 (UTC+8)**：`{selected_row.get('預計抵達 (UTC+8)', '未知')}`\n"
                f"- **即時高度/速度**：`{selected_row.get('高度 (ft)', 0)} ft` / `{selected_row.get('地速 (kts)', 0)} kts`\n"
                f"- **台灣起飛狀態**：{selected_row.get('台灣起飛', '否')}\n"
                f"- **降落台灣狀態**：{selected_row.get('降落台灣', '否')}"
            )
        st.divider()

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=df_matched,
        get_position=["lon", "lat"],
        get_color="marker_color",
        get_radius=60000,
        pickable=True,
        auto_highlight=True,
    )

    view_state = pdk.ViewState(
        latitude=center_lat,
        longitude=center_lon,
        zoom=zoom_level,
        pitch=0,
    )

    hover_tooltip = {
        "html": (
            "<b>✈️ {航班號}</b> ({機身註冊號})<br/>"
            "<b>📍 航線:</b> {航線 (出發➔到達)}<br/>"
            "<b>🛫 起飛時間:</b> {起飛時間 (UTC+8)}<br/>"
            "<b>🕒 預計抵達:</b> {預計抵達 (UTC+8)}<br/>"
            "<b>🛩️ 機型:</b> {機型}<br/>"
            "<b>📏 高度:</b> {高度 (ft)} ft | <b>⚡ 地速:</b> {地速 (kts)} kts<br/>"
            "<b>🛫 台灣起飛:</b> {台灣起飛} | <b>🇹🇼 降落台灣:</b> {降落台灣}"
        ),
        "style": {
            "backgroundColor": "rgba(15, 23, 42, 0.90)",
            "color": "white",
            "borderRadius": "8px",
            "boxShadow": "0px 4px 12px rgba(0,0,0,0.4)",
            "fontSize": "12px",
        },
    }

    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            tooltip=hover_tooltip,
        )
    )

    st.subheader("🟢 在空中/飛行中航班詳細清單")
    st.info("💡 **綠色底代表從台灣起飛航班** | 點擊表格任意航班可於上方查看照片與地圖定位")

    ordered_cols = [
        "機身照片",
        "台灣起飛",
        "降落台灣",
        "監控目標",
        "航班號",
        "機身註冊號",
        "起飛時間 (UTC+8)",
        "預計抵達 (UTC+8)",
        "機型",
        "航線 (出發➔到達)",
        "資料來源",
    ]

    # 防禦機制：確保舊 Session 數據也能補齊欄位
    for col in ordered_cols:
        if col not in df_sorted.columns:
            df_sorted[col] = "未知"

    display_df = df_sorted[ordered_cols].copy()
    display_df.insert(0, "編號", range(1, len(display_df) + 1))

    matched_col_config = {
        "編號": st.column_config.NumberColumn("編號", width=50, format="%d"),
        "機身照片": st.column_config.ImageColumn("機身照片", width="small"),
        "台灣起飛": st.column_config.TextColumn("台灣起飛", width="small"),
        "降落台灣": st.column_config.TextColumn("降落台灣", width="small"),
        "監控目標": st.column_config.TextColumn("監控目標", width="small"),
        "航班號": st.column_config.TextColumn("航班號", width="small"),
        "機身註冊號": st.column_config.TextColumn("機身註冊號", width="small"),
        "起飛時間 (UTC+8)": st.column_config.TextColumn("起飛時間 (UTC+8)", width="medium"),
        "預計抵達 (UTC+8)": st.column_config.TextColumn("預計抵達 (UTC+8)", width="medium"),
        "機型": st.column_config.TextColumn("機型", width="small"),
        "航線 (出發➔到達)": st.column_config.TextColumn("航線 (出發➔到達)", width="medium"),
        "資料來源": st.column_config.TextColumn("資料來源", width="small"),
    }

    # 行高亮邏輯：台灣起飛套用綠色底樣式
    def style_taiwan_orig_rows(row):
        if row.get("台灣起飛") == "🛫 台灣起飛":
            return ["background-color: #d4edda; color: #155724; font-weight: bold;"] * len(row)
        return [""] * len(row)

    styled_df = display_df.style.apply(style_taiwan_orig_rows, axis=1)

    st.dataframe(
        styled_df,
        column_config=matched_col_config,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="flight_table",
    )

# --- 2. 未查到航班清單 ---
if unmatched_targets:
    st.subheader("🔴 未查到 / 尚未起飛目標")
    st.caption("以下監控目標目前未在空中廣播訊號中偵測到，可能尚未起飛、已降落或暫無訊號：")
    df_unmatched = pd.DataFrame(
        {
            "編號": range(1, len(unmatched_targets) + 1),
            "監控目標代碼": unmatched_targets,
            "狀態": ["🔴 尚未起飛 / 暫無訊號"] * len(unmatched_targets),
        }
    )
    st.dataframe(df_unmatched, use_container_width=True, hide_index=True)
