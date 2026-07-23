import concurrent.futures
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


# --- 架構一：建立索引 Dictionary ---
def build_flight_index(all_flights):
    """將全域航班列表轉為 Hash Map，提供 O(1) 快速查詢"""
    index = {}
    for flight in all_flights:
        number = (getattr(flight, "number", "") or "").upper()
        callsign = (getattr(flight, "callsign", "") or "").upper()
        registration = (getattr(flight, "registration", "") or "").upper()

        if number:
            index[number] = flight
        if callsign:
            index[callsign] = flight
        if registration:
            index[registration] = flight

    return index


# --- 架構三： Clickhandler 加上快取 (TTL=300s) ---
@st.cache_data(ttl=300)
def fetch_direct_clickhandler(flight_id: str) -> dict | None:
    """快取的 Clickhandler，5分鐘內重複存取不重抓"""
    if not flight_id:
        return None

    url = f"https://data-live.flightradar24.com/clickhandler/?flight={flight_id}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        res = requests.get(url, headers=headers, timeout=3)
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
                    "f_num": f_num,
                    "f_reg": f_reg,
                    "ac_code": ac_code,
                }
    except Exception:
        pass
    return None


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


# --- 架構二 & 四：搜尋單一目標 + 優先使用 Broadcast 資料 ---
def search_single_target(target: str, flight_index: dict, deep_fetch: bool) -> dict | None:
    target_clean = target.strip().upper()
    flight = flight_index.get(target_clean)

    if not flight:
        return None

    # 原生 Broadcast 資料提取
    f_num = (getattr(flight, "number", "") or target_clean).upper()
    f_reg = (getattr(flight, "registration", "") or target_clean).upper()
    ac_code = getattr(flight, "aircraft_code", "未知") or "未知"
    origin = getattr(flight, "origin_airport_iata", "未知") or "未知"
    destination = getattr(flight, "destination_airport_iata", "未知") or "未知"
    f_id = getattr(flight, "id", "")

    # 架構四：只有在需要補全資訊（如缺航線）且開啟 deep_fetch 時才調用 Clickhandler
    need_clickhandler = deep_fetch and f_id and (origin == "未知" or destination == "未知")

    if need_clickhandler:
        details = fetch_direct_clickhandler(f_id)
        if details:
            origin = details["origin"] if details["origin"] != "未知" else origin
            destination = details["destination"] if details["destination"] != "未知" else destination
            ac_code = details["ac_code"] if details["ac_code"] != "未知" else ac_code
            f_num = details["f_num"] if details["f_num"] != "未知" else f_num
            f_reg = details["f_reg"] if details["f_reg"] != "未知" else f_reg

    is_taiwan = check_is_taiwan(destination)

    return {
        "監控目標": target_clean,
        "航班號": f_num,
        "機身註冊號": f_reg,
        "機型": ac_code,
        "航線 (出發➔到達)": f"{origin} ➔ {destination}",
        "高度 (ft)": getattr(flight, "altitude", 0),
        "地速 (kts)": getattr(flight, "ground_speed", 0),
        "降落台灣": "🇹🇼 降落台灣" if is_taiwan else "否",
        "資料來源": "📡 Broadcast + Clickhandler" if need_clickhandler else "📡 Broadcast",
        "lat": getattr(flight, "latitude", 0.0),
        "lon": getattr(flight, "longitude", 0.0),
        "_is_taiwan": is_taiwan,
    }


# --- APP 介面與標題 ---
st.title("✈️ FlightRadar24 智慧航班與降落台灣監測 APP")
st.caption("🟢 高效能字典索引模式 | 免 API Key | 快取優化")

# --- 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 監控清單")
    st.info("💡 支援輸入「航班號」(如 BR197) 或「機身編號/註冊號」(如 A6-EXR)")

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

    flight_input = st.text_area("飛機代碼清單 (每行一班)", value=clean_default_flights, height=280)

    # 深度查詢開關
    deep_fetch = st.checkbox("🔍 啟用深層航線補全 (缺航線時才補充詳細資訊)", value=True)

    st.divider()
    scan_button = st.button("🔄 掃描空中即時動態", type="primary", use_container_width=True)

targets = [f.strip().upper() for f in flight_input.split("\n") if f.strip()]

# --- 主程式執行區塊 ---
if scan_button or "scan_df" not in st.session_state:
    with st.spinner("正獲取全球航班資料並建立 Hash 索引..."):
        try:
            # 1. 抓取全球飛機
            all_active_flights = fr_api.get_flights()

            # 2. 建立 Dictionary 索引
            flight_index = build_flight_index(all_active_flights)

            matched_results = []

            # 架構五 & 六：針對命中且需要打 API 的項目進行極簡平行處理或單線程查找
            # 由於多數比對僅需記憶體查詢 (O(1))，平行優化主要加速罕見的 Clickhandler HTTP 請求
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(search_single_target, target, flight_index, deep_fetch)
                    for target in targets
                ]
                for future in concurrent.futures.as_completed(futures):
                    res = future.result()
                    if res:
                        matched_results.append(res)

            st.session_state["scan_df"] = pd.DataFrame(matched_results)

        except Exception as e:
            st.error(f"掃描失敗: {str(e)}")
            st.session_state["scan_df"] = pd.DataFrame()

df = st.session_state.get("scan_df", pd.DataFrame())

# --- 頂部數據看板 ---
col1, col2, col3 = st.columns(3)
col1.metric("監控目標總數", f"{len(targets)} 架")
col2.metric("當前空中/剛降落", f"{len(df)} 架")
col3.metric(
    "預計/已降落台灣",
    f"{df['_is_taiwan'].sum() if not df.empty and '_is_taiwan' in df.columns else 0} 架",
)

st.divider()

# --- 地圖與清單呈現 ---
if not df.empty:
    st.subheader("🗺️ 飛機即時位置雷達地圖")

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=df,
        get_position=["lon", "lat"],
        get_color="[230, 57, 70, 210]",
        get_radius=70000,
        pickable=True,
        auto_highlight=True,
    )

    center_lat = df["lat"].mean() if not df.empty else 23.5
    center_lon = df["lon"].mean() if not df.empty else 121.0

    view_state = pdk.ViewState(
        latitude=center_lat,
        longitude=center_lon,
        zoom=2.2,
        pitch=0,
    )

    hover_tooltip = {
        "html": """
        <div style="font-family: Arial, sans-serif; padding: 6px 10px; line-height: 1.5;">
            <span style="font-size: 14px; font-weight: bold; color: #ff4b4b;">✈️ {航班號}</span> 
            <span style="font-size: 12px; color: #aaa;">({機身註冊號})</span><br/>
            <b>📍 航線:</b> {航線 (出發➔到達)}<br/>
            <b>🛩️ 機型:</b> {機型}<br/>            
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
            map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            tooltip=hover_tooltip,
        )
    )

    st.subheader("📋 空中即時動態詳細清單")
    display_df = df.drop(columns=["lat", "lon", "_is_taiwan"], errors="ignore")
    st.dataframe(display_df, use_container_width=True, hide_index=True)
else:
    st.warning("⚠️ 目前清單中的飛機皆「不在空中飛行」。")
