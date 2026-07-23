from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import pydeck as pdk
import requests
import streamlit as st
from FlightRadarAPI import FlightRadar24API

# --- 頁面基本設定 ---
st.set_page_config(
    page_title="FlightRadar24 智慧航班監測 APP",
    page_icon="✈️",
    layout="wide",
)


# 初始化 API 物件
@st.cache_resource
def init_api():
    return FlightRadar24API()


fr_api = init_api()


# --- 1. 智慧判讀降落台灣 ---
def check_is_taiwan(text_or_code: str) -> bool:
    """全面支援 ICAO (RC**)、IATA (TPE, TSA, KHH...) 與中文/英文城市名稱"""
    if not text_or_code or text_or_code == "未知":
        return False
    s = str(text_or_code).upper()
    tw_keywords = [
        "RC",
        "TPE",
        "TSA",
        "KHH",
        "RMQ",
        "TNN",
        "HUN",
        "TTT",
        "MZG",
        "KIN",
        "TAIPEI",
        "TAIWAN",
        "KAOHSIUNG",
    ]
    return any(kw in s for kw in tw_keywords)


# --- 2. 直接請求 FR24 底層 Clickhandler 獲取完整數據包 ---
def fetch_direct_clickhandler(flight_id: str) -> dict | None:
    """直接對 FR24 API 抓取最完整的詳細資訊 (含已降落班機動態)"""
    if not flight_id:
        return None

    url = (
        f"https://data-live.flightradar24.com/clickhandler/?flight={flight_id}"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(url, headers=headers, timeout=4)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict) and "airport" in data:
                airport = data.get("airport") or {}
                orig_obj = (airport.get("origin") or {}).get("code") or {}
                dest_obj = (airport.get("destination") or {}).get("code") or {}

                origin = (
                    orig_obj.get("iata")
                    or orig_obj.get("icao")
                    or (airport.get("origin") or {})
                    .get("pluginData", {})
                    .get("details", {})
                    .get("name")
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

                pos = data.get("position") or {}
                trail = data.get("trail") or []
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
                lat = (
                    pos.get("latitude")
                    or latest_trail.get("lat")
                    or pos.get("lat")
                    or 0.0
                )
                lon = (
                    pos.get("longitude")
                    or latest_trail.get("lng")
                    or pos.get("lon")
                    or 0.0
                )

                ident = data.get("identification") or {}
                f_num = (
                    (ident.get("number") or {}).get("default")
                    or (ident.get("callsign") or {}).get("default")
                    or "未知"
                )

                ac = data.get("aircraft") or {}
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
        pass
    return None


# --- 3. 核心邏輯：單一目標智慧反查 ---
def search_single_target(target: str, all_flights: list) -> dict | None:
    """單一目標智慧反查邏輯"""
    target = target.strip().upper()
    if not target:
        return None

    # 步驟 1: 全域廣播池比對
    for flight in all_flights:
        f_num = (getattr(flight, "number", "") or "").upper()
        f_callsign = (getattr(flight, "callsign", "") or "").upper()
        f_reg = (getattr(flight, "registration", "") or "").upper()

        if target in [f_num, f_callsign, f_reg] and f_reg != "":
            f_id = getattr(flight, "id", "")
            details = fetch_direct_clickhandler(f_id)

            if details:
                origin = details["origin"]
                destination = details["destination"]
                is_taiwan = check_is_taiwan(destination)

                return {
                    "監控目標": target,
                    "航班號": (
                        details["f_num"]
                        if details["f_num"] != "未知"
                        else (f_num or f_callsign)
                    ),
                    "機身註冊號": f_reg,
                    "機型": (
                        details["ac_code"]
                        if details["ac_code"] != "未知"
                        else getattr(flight, "aircraft_code", "未知")
                    ),
                    "航線 (出發➔到達)": f"{origin} ➔ {destination}",
                    "高度 (ft)": details["alt"],
                    "地速 (kts)": details["spd"],
                    "降落台灣": "🇹🇼 降落台灣" if is_taiwan else "否",
                    "資料來源": "📡 直播廣播",
                    "lat": details["lat"],
                    "lon": details["lon"],
                    "_is_taiwan": is_taiwan,
                }

    # 步驟 2: Web Search API + 底層 Clickhandler 反查
    search_url = (
        f"https://www.flightradar24.com/v1/search/web/find?query={target}"
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
            results = res.json().get("results", [])

            for item in results:
                if item.get("type") == "live":
                    live_id = str(item.get("id", "")).strip()

                    if live_id:
                        details = fetch_direct_clickhandler(live_id)
                        if details:
                            origin = details["origin"]
                            destination = details["destination"]
                            is_taiwan = check_is_taiwan(destination)

                            return {
                                "監控目標": target,
                                "航班號": (
                                    details["f_num"]
                                    if details["f_num"] != "未知"
                                    else target
                                ),
                                "機身註冊號": (
                                    details["f_reg"]
                                    if details["f_reg"] != "未知"
                                    else target
                                ),
                                "機型": details["ac_code"],
                                "航線 (出發➔到達)": (
                                    f"{origin} ➔ {destination}"
                                ),
                                "高度 (ft)": details["alt"],
                                "地速 (kts)": details["spd"],
                                "降落台灣": (
                                    "🇹🇼 降落台灣" if is_taiwan else "否"
                                ),
                                "資料來源": "🔍 Web API 詳細反查",
                                "lat": details["lat"],
                                "lon": details["lon"],
                                "_is_taiwan": is_taiwan,
                            }
    except Exception:
        pass

    return None


# --- APP 介面與標題 ---
st.title("✈️ FlightRadar24 智慧航班與降落台灣監測 APP")
st.caption(
    "🟢 完全免費 / 免 API Key | 支援機身編號雙重反查、互動地圖與多線程爆速查詢"
)

# --- 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 監控清單")
    st.info(
        "💡 支援輸入「航班號」(如 BR197) 或「機身編號/註冊號」(如 A6-EXR,"
        " N130FE)"
    )

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

    # 自動清理縮排與空白
    clean_default_flights = "\n".join(
        [
            line.strip()
            for line in raw_default_flights.split("\n")
            if line.strip()
        ]
    )

    flight_input = st.text_area(
        "飛機代碼清單 (每行一班)", value=clean_default_flights, height=300
    )

    st.divider()
    scan_button = st.button(
        "🔄 掃描空中即時動態", type="primary", use_container_width=True
    )

targets = [f.strip().upper() for f in flight_input.split("\n") if f.strip()]

# --- 主程式執行區塊 ---
if scan_button or "first_run" not in st.session_state:
    st.session_state["first_run"] = True

    with st.spinner(
        "正從 FlightRadar24 抓取全球即時空域數據並進行多線程比對..."
    ):
        try:
            # 1. 先抓取全球廣播資料 (一次性請求)
            all_active_flights = fr_api.get_flights()
            matched_results = []

            # 2. 使用 ThreadPoolExecutor 多線程平行加速 48 架飛機的反查
            def worker(t):
                return search_single_target(t, all_active_flights)

            progress_bar = st.progress(0)
            completed_count = 0

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(worker, t) for t in targets]
                for future in as_completed(futures):
                    res = future.result()
                    if res:
                        matched_results.append(res)
                    completed_count += 1
                    progress_bar.progress(completed_count / len(targets))

            progress_bar.empty()

            # 轉為 DataFrame
            df = pd.DataFrame(matched_results)

            # 頂部數據看板
            col1, col2, col3 = st.columns(3)
            col1.metric("監控目標總數", f"{len(targets)} 架")
            col2.metric("當前空中/剛降落", f"{len(df)} 架")
            col3.metric(
                "預計/已降落台灣",
                f"{df['_is_taiwan'].sum() if not df.empty else 0} 架",
            )

            st.divider()

            if not df.empty:
                # 3. 升級版 PyDeck 互動地圖 (含懸浮工具提示 Hover Tooltip)
                st.subheader(
                    "🗺️ 飛機即時位置雷達地圖 (將滑鼠移至點上可查看詳情)"
                )

                # 定義 PyDeck 繪圖圖層
                layer = pdk.Layer(
                    "ScatterplotLayer",
                    data=df,
                    get_position=["lon", "lat"],
                    get_color="[230, 57, 70, 210]",  # 鮮艷紅點
                    get_radius=70000,  # 點大小 (70 公里半徑)
                    pickable=True,  # 開啟滑鼠互動選擇
                    auto_highlight=True,  # 懸浮自動亮起
                )

                # 地圖初始中心點設為數據平均經緯度 (若為空設為台灣)
                center_lat = df["lat"].mean() if not df.empty else 23.5
                center_lon = df["lon"].mean() if not df.empty else 121.0

                view_state = pdk.ViewState(
                    latitude=center_lat,
                    longitude=center_lon,
                    zoom=2.2,
                    pitch=0,
                )

                # 自訂 Hover 懸浮提示框 HTML
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

                st.pydeck_chart(
                    pdk.Deck(
                        layers=[layer],
                        initial_view_state=view_state,
                        map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",  # 高質感深色地圖
                        tooltip=hover_tooltip,
                    )
                )

                # 4. 詳細清單表格
                st.subheader("📋 空中即時動態詳細清單")
                display_df = df.drop(columns=["lat", "lon", "_is_taiwan"])

                st.dataframe(
                    display_df, use_container_width=True, hide_index=True
                )
            else:
                st.warning(
                    "⚠️ 目前清單中的飛機皆「不在空中飛行」（可能尚未起飛、已降落過久，或應答機未開啟）。"
                )

        except Exception as e:
            st.error(f"執行監測時發生錯誤: {str(e)}")