import random
import time
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


# --- 2. 補充詳細資訊 (可選，僅對已成功的飛機打 API) ---
def fetch_direct_clickhandler(flight_id: str) -> dict | None:
    """僅對比對成功的目標抓取完整詳細資訊"""
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


# --- APP 介面與標題 ---
st.title("✈️ FlightRadar24 智慧航班與降落台灣監測 APP")
st.caption(
    "🟢 完全免費 / 免 API Key | 單次廣播高穩定度模式"
)

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
        [
            line.strip()
            for line in raw_default_flights.split("\n")
            if line.strip()
        ]
    )

    flight_input = st.text_area(
        "飛機代碼清單 (每行一班)", value=clean_default_flights, height=280
    )

    # 深度查詢開關：開啟時才會對找到的飛機補充 Clickhandler 資訊
    deep_fetch = st.checkbox("🔍 啟用深層航線補全 (對發現的目標補充詳細機場資訊)", value=True)

    st.divider()
    scan_button = st.button(
        "🔄 掃描空中即時動態", type="primary", use_container_width=True
    )

targets = [f.strip().upper() for f in flight_input.split("\n") if f.strip()]

# --- 主程式執行區塊 (單次廣播邏輯) ---
if scan_button or "scan_df" not in st.session_state:
    with st.spinner("正發送『單次廣播』抓取全球即時空域，並於記憶體中快速比對..."):
        try:
            # 【核心重點 1】：僅打 1 次 API，將全球所有在空中的飛機抓回記憶體
            all_active_flights = fr_api.get_flights()

            # 【核心重點 2】：建立快速索引清單
            targets_set = set(targets)
            matched_results = []

            # 遍歷一次全域資料，比對記憶體中的飛機 (速度約 0.01 秒)
            for flight in all_active_flights:
                f_num = (getattr(flight, "number", "") or "").upper()
                f_callsign = (getattr(flight, "callsign", "") or "").upper()
                f_reg = (getattr(flight, "registration", "") or "").upper()

                # 比對是否在目標清單中
                hit_target = None
                for t in [f_reg, f_num, f_callsign]:
                    if t and t in targets_set:
                        hit_target = t
                        break

                if hit_target:
                    f_id = getattr(flight, "id", "")
                    origin = getattr(flight, "origin_airport_iata", "未知") or "未知"
                    destination = getattr(flight, "destination_airport_iata", "未知") or "未知"
                    ac_code = getattr(flight, "aircraft_code", "未知") or "未知"

                    # 深度補充邏輯 (若開啟，只對這幾架 hit 的飛機發送請求)
                    if deep_fetch and f_id:
                        details = fetch_direct_clickhandler(f_id)
                        if details:
                            origin = details["origin"] if details["origin"] != "未知" else origin
                            destination = details["destination"] if details["destination"] != "未知" else destination
                            ac_code = details["ac_code"] if details["ac_code"] != "未知" else ac_code
                            f_num = details["f_num"] if details["f_num"] != "未知" else f_num
                            f_reg = details["f_reg"] if details["f_reg"] != "未知" else f_reg

                    is_taiwan = check_is_taiwan(destination)

                    matched_results.append({
                        "監控目標": hit_target,
                        "航班號": f_num if f_num else "未知",
                        "機身註冊號": f_reg if f_reg else "未知",
                        "機型": ac_code,
                        "航線 (出發➔到達)": f"{origin} ➔ {destination}",
                        "高度 (ft)": getattr(flight, "altitude", 0),
                        "地速 (kts)": getattr(flight, "ground_speed", 0),
                        "降落台灣": "🇹🇼 降落台灣" if is_taiwan else "否",
                        "資料來源": "📡 全域即時廣播",
                        "lat": getattr(flight, "latitude", 0.0),
                        "lon": getattr(flight, "longitude", 0.0),
                        "_is_taiwan": is_taiwan,
                    })

            st.session_state["scan_df"] = pd.DataFrame(matched_results)

        except Exception as e:
            st.error(f"單次廣播抓取失敗: {str(e)}")
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
