from concurrent.futures import ThreadPoolExecutor, as_completed
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


# --- 2. 輔助函式定義 ---
def check_is_taiwan(text_or_code: str) -> bool:
    """精準判斷機場代碼或名稱是否屬於台灣 (已修復 URC 烏魯木齊誤報 Bug)"""
    if not text_or_code or text_or_code == "未知":
        return False
    s = str(text_or_code).upper().strip()

    # 台灣主要機場 IATA 與 ICAO 代碼清單 (精準比對)
    tw_airport_codes = {
        # IATA (3碼)
        "TPE",
        "TSA",
        "KHH",
        "RMQ",
        "TNN",
        "HUN",
        "TTT",
        "MZG",
        "KIN",
        "CYI",
        "PIF",
        "LZN",
        "CMJ",
        # ICAO (4碼)
        "RCTP",
        "RCSS",
        "RCKH",
        "RCMQ",
        "RCNN",
        "RCHU",
        "RCFG",
        "RCBS",
        "RCFN",
        "RCKW",
        "RCMT",
        "RCLY",
    }

    # 1. 精準比對代碼
    if s in tw_airport_codes:
        return True

    # 2. 若為 4 碼 ICAO 代碼，且開頭必須是 RC (例如 RCTP，不會誤抓 3 碼的 URC)
    if len(s) == 4 and s.startswith("RC"):
        return True

    # 3. 城市與國家名稱關鍵字比對
    tw_name_keywords = [
        "TAIPEI",
        "TAIWAN",
        "KAOHSIUNG",
        "TAICHUNG",
        "TAINAN",
        "台北",
        "台灣",
        "高雄",
        "台中",
        "台南",
    ]
    return any(kw in s for kw in tw_name_keywords)


def fetch_direct_clickhandler(flight_obj_or_id) -> dict | None:
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
        }
    except Exception:
        return None


def search_single_target_worker(
    target_raw: str, all_flights: list
) -> dict | None:
    """支援 worker 的單目標查詢函式"""
    target_clean = target_raw.replace("-", "")

    flight_map_by_id = {
        getattr(f, "id", ""): f for f in all_flights if getattr(f, "id", "")
    }

    # 1. 廣播數據直接比對
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
                is_taiwan = check_is_taiwan(destination)

                return {
                    "監控目標": target_raw,
                    "航班號": (
                        details["f_num"]
                        if details["f_num"] != "未知"
                        else (f_num or f_callsign)
                    ),
                    "機身註冊號": (
                        details["f_reg"]
                        if details["f_reg"] != "未知"
                        else (f_reg or target_raw)
                    ),
                    "機型": details["ac_code"],
                    "航線 (出發➔到達)": f"{origin} ➔ {destination}",
                    "高度 (ft)": details["alt"],
                    "地速 (kts)": details["spd"],
                    "降落台灣": "🇹🇼 降落台灣" if is_taiwan else "否",
                    "資料來源": "📡 直播廣播",
                    "lat": details["lat"],
                    "lon": details["lon"],
                    "_is_taiwan": is_taiwan,
                }

    # 2. 若廣播數據未抓到，才使用 Web API 反查
    search_url = (
        f"https://www.flightradar24.com/v1/search/web/find?query={target_raw}"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(search_url, headers=headers, timeout=4)
        if res.status_code == 200:
            results = sorted(
                res.json().get("results", []),
                key=lambda x: (
                    x.get("type") != "live",
                    str(x.get("id", "")),
                ),
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
                            is_taiwan = check_is_taiwan(destination)

                            return {
                                "監控目標": target_raw,
                                "航班號": (
                                    details["f_num"]
                                    if details["f_num"] != "未知"
                                    else target_raw
                                ),
                                "機身註冊號": (
                                    details["f_reg"]
                                    if details["f_reg"] != "未知"
                                    else target_raw
                                ),
                                "機型": details["ac_code"],
                                "航線 (出發➔到達)": (
                                    f"{origin} ➔ {destination}"
                                ),
                                "高度 (ft)": details["alt"],
                                "地速 (kts)": details["spd"],
                                "降落台灣": (
                                    "🇹🇼 降落台灣"
                                    if is_taiwan
                                    else "否"
                                ),
                                "資料來源": "🔍 Web API 詳細反查",
                                "lat": details["lat"],
                                "lon": details["lon"],
                                "_is_taiwan": is_taiwan,
                            }
    except Exception:
        pass

    return None


# --- 3. UI 介面與側邊欄設定 ---
st.title("✈️ FlightRadar24 彩繪機降落台灣監測")

if "matched_dict" not in st.session_state:
    st.session_state["matched_dict"] = {}

raw_default_flights = """B-KQU
B-LRJ
B-LJE
HL7628
B-18918
B-18311
B-18007
B-5390
JA872A
B-17812
B-16715
JA880A
JA731A
JA875A
JA614A
9V-SWI
9V-SWJ
B-2032
B-6091
B-6093
HL7732
HL8071
HS-TKQ
HL7783
VN-A897
VN-A327
B-6538
PK-GMH
PH-BVD
9V-OJJ
JA73AB
JA894A
B-18101
A6-EXR
A6-EES
A6-EET
A6-EEP
A6-DDE
A6-BLV
A6-BMH
LX-NCL
LX-VCF
HL7423
HL7419
JA12KZ
N771CK
N454PA
N249BA"""

clean_default_flights = "\n".join(
    [line.strip() for line in raw_default_flights.split("\n") if line.strip()]
)

with st.sidebar:
    st.header("⚙️ 監控清單")
    st.info("💡 輸入「機身編號/註冊號」")

    flight_input = st.text_area(
        "飛機代碼清單 (每行一班)", value=clean_default_flights, height=280
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


# 🔄 多執行緒併行 + 動態刷新數據邏輯
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

        # 1. 完全查完提前結束
        if current_unmatched_count == 0:
            status_info.success("🎉 所有監控目標皆已成功定位！")
            break

        # 2. 判斷穩定度
        if current_unmatched_count == last_unmatched_count:
            stable_counter += 1
        else:
            stable_counter = 1
            last_unmatched_count = current_unmatched_count

        if stable_counter >= stable_threshold:
            status_info.success(
                f"✅ 未查到數量已連續 {stable_threshold} 輪維持在 {current_unmatched_count} 架，數據已達穩定狀態！"
            )
            time.sleep(1)
            break

        status_info.info(
            f"⚡ [併行加速中] 第 {current_round} 輪掃描... "
            f"（剩餘未查到：{current_unmatched_count} 架 | 穩定進度：{stable_counter}/{stable_threshold}）"
        )

        # 每輪重新取得最新的全球空域快照
        fetch_all_active_flights.clear()
        snapshot = fetch_all_active_flights()

        total_pending = len(pending_targets)
        completed_count = 0

        # 🚀 使用 ThreadPoolExecutor 同時併行查詢所有待查目標
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


# 觸發邏輯處理
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

taiwan_count = (
    int(df_matched["_is_taiwan"].sum())
    if (not df_matched.empty and "_is_taiwan" in df_matched.columns)
    else 0
)

# 頂部數據看板
col1, col2, col3, col4 = st.columns(4)
col1.metric("監控目標總數", f"{len(targets)} 架")
col2.metric("在空中 / 飛行中", f"{len(df_matched)} 架")
col3.metric("🇹🇼 預計/已降落台灣", f"{taiwan_count} 架")
col4.metric("未查到 / 尚未起飛", f"{len(unmatched_targets)} 架")

if taiwan_count > 0:
    st.success(
        f"### 🇹🇼 即時警報：共有 **{taiwan_count}** 架目標班機預計或已經降落台灣！"
    )

st.divider()

# --- 1. 在空中航班（地圖 + 表格互動） ---
if not df_matched.empty:
    df_sorted = (
        df_matched.sort_values(
            by=["_is_taiwan", "監控目標"], ascending=[False, True]
        )
        .reset_index(drop=True)
    )

    center_lat = df_matched["lat"].mean()
    center_lon = df_matched["lon"].mean()
    zoom_level = 2.2
    selected_flight_number = None

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
                selected_flight_number = selected_row["航班號"]

    st.subheader("🗺️ 飛機即時位置雷達地圖")
    if selected_flight_number:
        st.success(
            f"🎯 **地圖已自動定位至航班：{selected_flight_number}** (座標:"
            f" {center_lat:.2f}, {center_lon:.2f})"
        )

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=df_matched,
        get_position=["lon", "lat"],
        get_color="[230, 57, 70, 220]",
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
        "html": """
        <div style="font-family: Arial, sans-serif; padding: 6px 10px; line-height: 1.5;">
            <span style="font-size: 14px; font-weight: bold; color: #ff4b4b;">✈️ {航班號}</span> 
            <span style="font-size: 12px; color: #aaa;">({機身註冊號})</span><br/>
            <b>📍 航線:</b> {航線 (出發➔到達)}<br/>
            <b>🛩️ 機型:</b> {機型}<br/>
            <b>📏 高度:</b> {高度 (ft)} ft | <b>⚡ 地速:</b> {地速 (kts)} kts<br/>
            <b>🇹🇼 降落台灣:</b> {降落台灣}<br/>
            <span style="font-size: 10px; color: #888;">來源: {資料來源}</span>
        </div>
        """,
        "style": {
            "backgroundColor": "rgba(15, 23, 42, 0.90)",
            "color": "white",
            "borderRadius": "8px",
            "boxShadow": "0px 4px 12px rgba(0,0,0,0.4)",
            "fontSize": "12px",
        },
    }

    map_key = f"map_{center_lat:.4f}_{center_lon:.4f}_{zoom_level}"

    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            tooltip=hover_tooltip,
        ),
        key=map_key,
    )

    st.subheader("🟢 在空中/飛行中航班詳細清單")
    st.info("💡 **點擊下方清單中任意一列航班，地圖會自動飛過去並鎖定該飛機！**")

    ordered_cols = [
        "降落台灣",
        "監控目標",
        "航班號",
        "機身註冊號",
        "機型",
        "航線 (出發➔到達)",
        "資料來源",
    ]
    display_df = df_sorted[ordered_cols].copy()
    display_df.insert(0, "編號", range(1, len(display_df) + 1))

    matched_col_config = {
        "編號": st.column_config.NumberColumn("編號", width=60, format="%d"),
        "降落台灣": st.column_config.TextColumn("降落台灣", width=130),
        "監控目標": st.column_config.TextColumn("監控目標", width=120),
        "航班號": st.column_config.TextColumn("航班號", width=120),
        "機身註冊號": st.column_config.TextColumn("機身註冊號", width=130),
        "機型": st.column_config.TextColumn("機型", width=100),
        "航線 (出發➔到達)": st.column_config.TextColumn(
            "航線 (出發➔到達)", width=240
        ),
        "資料來源": st.column_config.TextColumn("資料來源", width=180),
    }

    def color_taiwan_col(val):
        if "🇹🇼" in str(val):
            return "background-color: #28a745; color: #ffffff; font-weight: bold;"
        return "color: #888888;"

    styled_display_df = display_df.style.map(
        color_taiwan_col, subset=["降落台灣"]
    )

    st.dataframe(
        styled_display_df,
        use_container_width=True,
        hide_index=True,
        column_config=matched_col_config,
        key="flight_table",
        on_select="rerun",
        selection_mode="single-row",
    )

# --- 2. 確定未在空中的飛機清單 ---
if unmatched_targets:
    st.subheader(
        f"🔴 確定未在空中 / 未起飛之目標清單 ({len(unmatched_targets)} 架)"
    )

    df_unmatched = pd.DataFrame({
        "編號": list(range(1, len(unmatched_targets) + 1)),
        "目標編號": unmatched_targets,
        "當前狀態": "未在空中飛行 / 尚未起飛 / 應答機未開啟",
    })

    unmatched_col_config = {
        "編號": st.column_config.NumberColumn("編號", width=60, format="%d"),
        "目標編號": st.column_config.TextColumn("目標編號", width=120),
        "當前狀態": st.column_config.TextColumn("當前狀態", width=1000),
    }

    st.dataframe(
        df_unmatched,
        use_container_width=True,
        hide_index=True,
        column_config=unmatched_col_config,
    )
ble_counter = 0
    current_round = 0

    while True:
        current_round += 1
        matched_keys = set(st.session_state["matched_dict"].keys())
        pending_targets = [t for t in all_targets if t not in matched_keys]
        current_unmatched_count = len(pending_targets)

        # 1. 完全查完提前結束
        if current_unmatched_count == 0:
            status_info.success("🎉 所有監控目標皆已成功定位！")
            break

        # 2. 判斷穩定度
        if current_unmatched_count == last_unmatched_count:
            stable_counter += 1
        else:
            stable_counter = 1
            last_unmatched_count = current_unmatched_count

        if stable_counter >= stable_threshold:
            status_info.success(
                f"✅ 未查到數量已連續 {stable_threshold} 輪維持在 {current_unmatched_count} 架，數據已達穩定狀態！"
            )
            time.sleep(1)
            break

        status_info.info(
            f"⚡ [併行加速中] 第 {current_round} 輪掃描... "
            f"（剩餘未查到：{current_unmatched_count} 架 | 穩定進度：{stable_counter}/{stable_threshold}）"
        )

        # 每輪重新取得最新的全球空域快照
        fetch_all_active_flights.clear()
        snapshot = fetch_all_active_flights()

        total_pending = len(pending_targets)
        completed_count = 0

        # 🚀 使用 ThreadPoolExecutor 同時併行查詢所有待查目標
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


# 觸發邏輯處理
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

taiwan_count = (
    int(df_matched["_is_taiwan"].sum())
    if (not df_matched.empty and "_is_taiwan" in df_matched.columns)
    else 0
)

# 頂部數據看板
col1, col2, col3, col4 = st.columns(4)
col1.metric("監控目標總數", f"{len(targets)} 架")
col2.metric("在空中 / 飛行中", f"{len(df_matched)} 架")
col3.metric("🇹🇼 預計/已降落台灣", f"{taiwan_count} 架")
col4.metric("未查到 / 尚未起飛", f"{len(unmatched_targets)} 架")

if taiwan_count > 0:
    st.success(
        f"### 🇹🇼 即時警報：共有 **{taiwan_count}** 架目標班機預計或已經降落台灣！"
    )

st.divider()

# --- 1. 在空中航班（地圖 + 表格互動） ---
if not df_matched.empty:
    df_sorted = (
        df_matched.sort_values(
            by=["_is_taiwan", "監控目標"], ascending=[False, True]
        )
        .reset_index(drop=True)
    )

    center_lat = df_matched["lat"].mean()
    center_lon = df_matched["lon"].mean()
    zoom_level = 2.2
    selected_flight_number = None

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
                selected_flight_number = selected_row["航班號"]

    st.subheader("🗺️ 飛機即時位置雷達地圖")
    if selected_flight_number:
        st.success(
            f"🎯 **地圖已自動定位至航班：{selected_flight_number}** (座標:"
            f" {center_lat:.2f}, {center_lon:.2f})"
        )

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=df_matched,
        get_position=["lon", "lat"],
        get_color="[230, 57, 70, 220]",
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
        "html": """
        <div style="font-family: Arial, sans-serif; padding: 6px 10px; line-height: 1.5;">
            <span style="font-size: 14px; font-weight: bold; color: #ff4b4b;">✈️ {航班號}</span> 
            <span style="font-size: 12px; color: #aaa;">({機身註冊號})</span><br/>
            <b>📍 航線:</b> {航線 (出發➔到達)}<br/>
            <b>🛩️ 機型:</b> {機型}<br/>
            <b>📏 高度:</b> {高度 (ft)} ft | <b>⚡ 地速:</b> {地速 (kts)} kts<br/>
            <b>🇹🇼 降落台灣:</b> {降落台灣}<br/>
            <span style="font-size: 10px; color: #888;">來源: {資料來源}</span>
        </div>
        """,
        "style": {
            "backgroundColor": "rgba(15, 23, 42, 0.90)",
            "color": "white",
            "borderRadius": "8px",
            "boxShadow": "0px 4px 12px rgba(0,0,0,0.4)",
            "fontSize": "12px",
        },
    }

    map_key = f"map_{center_lat:.4f}_{center_lon:.4f}_{zoom_level}"

    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            tooltip=hover_tooltip,
        ),
        key=map_key,
    )

    st.subheader("🟢 在空中/飛行中航班詳細清單")
    st.info("💡 **點擊下方清單中任意一列航班，地圖會自動飛過去並鎖定該飛機！**")

    ordered_cols = [
        "降落台灣",
        "監控目標",
        "航班號",
        "機身註冊號",
        "機型",
        "航線 (出發➔到達)",
        "資料來源",
    ]
    display_df = df_sorted[ordered_cols].copy()
    display_df.insert(0, "編號", range(1, len(display_df) + 1))

    matched_col_config = {
        "編號": st.column_config.NumberColumn("編號", width=60, format="%d"),
        "降落台灣": st.column_config.TextColumn("降落台灣", width=130),
        "監控目標": st.column_config.TextColumn("監控目標", width=120),
        "航班號": st.column_config.TextColumn("航班號", width=120),
        "機身註冊號": st.column_config.TextColumn("機身註冊號", width=130),
        "機型": st.column_config.TextColumn("機型", width=100),
        "航線 (出發➔到達)": st.column_config.TextColumn(
            "航線 (出發➔到達)", width=240
        ),
        "資料來源": st.column_config.TextColumn("資料來源", width=180),
    }

    def color_taiwan_col(val):
        if "🇹🇼" in str(val):
            return "background-color: #28a745; color: #ffffff; font-weight: bold;"
        return "color: #888888;"

    styled_display_df = display_df.style.map(
        color_taiwan_col, subset=["降落台灣"]
    )

    st.dataframe(
        styled_display_df,
        use_container_width=True,
        hide_index=True,
        column_config=matched_col_config,
        key="flight_table",
        on_select="rerun",
        selection_mode="single-row",
    )

# --- 2. 確定未在空中的飛機清單 ---
if unmatched_targets:
    st.subheader(
        f"🔴 確定未在空中 / 未起飛之目標清單 ({len(unmatched_targets)} 架)"
    )

    df_unmatched = pd.DataFrame({
        "編號": list(range(1, len(unmatched_targets) + 1)),
        "目標編號": unmatched_targets,
        "當前狀態": "未在空中飛行 / 尚未起飛 / 應答機未開啟",
    })

    unmatched_col_config = {
        "編號": st.column_config.NumberColumn("編號", width=60, format="%d"),
        "目標編號": st.column_config.TextColumn("目標編號", width=120),
        "當前狀態": st.column_config.TextColumn("當前狀態", width=1000),
    }

    st.dataframe(
        df_unmatched,
        use_container_width=True,
        hide_index=True,
        column_config=unmatched_col_config,
        )
                
