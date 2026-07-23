from concurrent.futures import ThreadPoolExecutor, as_completed
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


# 快取全球廣播資料 30 秒
@st.cache_data(ttl=30, show_spinner=False)
def fetch_all_active_flights():
    try:
        return fr_api.get_flights()
    except Exception:
        return []


# --- 2. 輔助函式定義 ---
def check_is_taiwan(text_or_code: str) -> bool:
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


def search_single_target(target: str, all_flights: list) -> dict | None:
    target_raw = target.strip().upper()
    if not target_raw:
        return None

    # 乾淨無槓版本 (如 B-18101 -> B18101)
    target_clean = target_raw.replace("-", "")

    flight_map_by_id = {
        getattr(f, "id", ""): f for f in all_flights if getattr(f, "id", "")
    }

    # 步驟 1: 全域廣播池雙重格式比對
    for flight in all_flights:
        f_num = (getattr(flight, "number", "") or "").upper()
        f_callsign = (getattr(flight, "callsign", "") or "").upper()
        f_reg = (getattr(flight, "registration", "") or "").upper()

        f_num_c = f_num.replace("-", "")
        f_callsign_c = f_callsign.replace("-", "")
        f_reg_c = f_reg.replace("-", "")

        matched = (
            target_raw in [f_num, f_callsign, f_reg]
            or target_clean in [f_num_c, f_callsign_c, f_reg_c]
        )

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

    # 步驟 2: Web API 詳細反查 (優先帶入原生的 Flight 物件)
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
            results = res.json().get("results", [])
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
st.title("✈️ FlightRadar24 智慧航班與降落台灣監測 APP")
st.caption("🟢 完全免費 / 免 API Key | 雙重檢核完整掃描機制")

with st.sidebar:
    st.header("⚙️ 監控清單")
    st.info("💡 支援輸入「航班號」或「機身編號/註冊號」")

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
        "飛機代碼清單 (每行一班)", value=clean_default_flights, height=300
    )

    st.divider()

    scan_button = st.button(
        "🔄 執行雙重檢核掃描", type="primary", use_container_width=True
    )

targets = [f.strip().upper() for f in flight_input.split("\n") if f.strip()]


# --- 4. 主程式執行邏輯 (雙重掃描) ---
if scan_button or "first_run" not in st.session_state:
    st.session_state["first_run"] = True

    status_info = st.empty()
    progress_bar = st.progress(0)

    try:
        all_active_flights = fetch_all_active_flights()
        matched_results = []

        # ------------------ 第 1 輪全清單掃描 ------------------
        status_info.info(f"🔄 [第 1/2 輪掃描] 正在初次比對全部 {len(targets)} 個目標...")

        completed_1 = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures_1 = {
                executor.submit(search_single_target, t, all_active_flights): t
                for t in targets
            }
            for future in as_completed(futures_1):
                res = future.result()
                if res:
                    matched_results.append(res)
                completed_1 += 1
                progress_bar.progress((completed_1 / len(targets)) * 0.5)

        # 找出第 1 輪未命中的目標
        found_targets_1 = {r["監控目標"] for r in matched_results}
        unmatched_targets_1 = [t for t in targets if t not in found_targets_1]

        # ------------------ 第 2 輪複查掃描 (僅複查未命中的目標) ------------------
        if unmatched_targets_1:
            status_info.warning(
                f"⚡ [第 2/2 輪掃描] 正對第 1 輪未命中的 {len(unmatched_targets_1)} 個目標進行二次補查驗證..."
            )

            completed_2 = 0
            # 重新更新快取廣播資料
            all_active_flights_retry = fetch_all_active_flights()

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures_2 = {
                    executor.submit(
                        search_single_target, t, all_active_flights_retry
                    ): t
                    for t in unmatched_targets_1
                }
                for future in as_completed(futures_2):
                    res = future.result()
                    if res:
                        matched_results.append(res)
                    completed_2 += 1
                    progress_bar.progress(
                        0.5 + (completed_2 / len(unmatched_targets_1)) * 0.5
                    )

        progress_bar.progress(1.0)
        progress_bar.empty()
        status_info.empty()

        # 將成果寫入 session_state
        st.session_state["final_df"] = pd.DataFrame(matched_results)

    except Exception as e:
        st.error(f"執行監測時發生錯誤: {str(e)}")


# --- 5. 畫面顯示區塊 ---
if "final_df" in st.session_state:
    df_matched = st.session_state["final_df"]

    # 計算最終的命中與未命中清單
    matched_targets = (
        set(df_matched["監控目標"].tolist()) if not df_matched.empty else set()
    )
    unmatched_targets = [t for t in targets if t not in matched_targets]

    # 頂部數據看板
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("監控目標總數", f"{len(targets)} 架")
    col2.metric("雙掃完成度", "100% (已複查)")
    col3.metric("在空中 / 飛行中", f"{len(df_matched)} 架")
    col4.metric("未查到 / 尚未起飛", f"{len(unmatched_targets)} 架")

    st.divider()

    # --- 1. 成功查到的飛機（地圖 + 表格） ---
    if not df_matched.empty:
        st.subheader("🗺️ 飛機即時位置雷達地圖")

        layer = pdk.Layer(
            "ScatterplotLayer",
            data=df_matched,
            get_position=["lon", "lat"],
            get_color="[230, 57, 70, 210]",
            get_radius=70000,
            pickable=True,
            auto_highlight=True,
        )

        center_lat = df_matched["lat"].mean()
        center_lon = df_matched["lon"].mean()

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
                map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
                tooltip=hover_tooltip,
            )
        )

        st.subheader("🟢 在空中/飛行中航班詳細清單")

        display_df = df_matched.drop(
            columns=["lat", "lon", "_is_taiwan"]
        ).copy()
        display_df.insert(0, "編號", range(1, len(display_df) + 1))

        matched_col_config = {
            "編號": st.column_config.NumberColumn(
                "編號", width=60, format="%d"
            ),
            "監控目標": st.column_config.TextColumn("監控目標", width=110),
            "航班號": st.column_config.TextColumn("航班號", width=100),
            "機身註冊號": st.column_config.TextColumn("機身註冊號", width=120),
            "機型": st.column_config.TextColumn("機型", width=90),
            "航線 (出發➔到達)": st.column_config.TextColumn(
                "航線 (出發➔到達)", width=220
            ),
            "高度 (ft)": st.column_config.NumberColumn("高度 (ft)", width=100),
            "地速 (kts)": st.column_config.NumberColumn("地速 (kts)", width=100),
            "降落台灣": st.column_config.TextColumn("降落台灣", width=120),
            "資料來源": st.column_config.TextColumn("資料來源", width=160),
        }

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config=matched_col_config,
        )

    # --- 2. 獨立表格顯示：二次複查後依然未查到的飛機 ---
    if unmatched_targets:
        st.subheader("🔴 經過雙重複查「確定未在空中/無訊號」之目標清單")

        df_unmatched = pd.DataFrame({
            "編號": list(range(1, len(unmatched_targets) + 1)),
            "目標編號": unmatched_targets,
            "當前狀態": "未在空中飛行 / 尚未起飛 / 應答機未開啟",
        })

        unmatched_col_config = {
            "編號": st.column_config.NumberColumn(
                "編號", width=60, format="%d"
            ),
            "目標編號": st.column_config.TextColumn("目標編號", width=120),
            "當前狀態": st.column_config.TextColumn("當前狀態", width=1000),
        }

        st.dataframe(
            df_unmatched,
            use_container_width=True,
            hide_index=True,
            column_config=unmatched_col_config,
        )
