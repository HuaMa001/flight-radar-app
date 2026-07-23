import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from FlightRadarAPI import FlightRadar24API

# 1. 讀取 GitHub Secrets 金鑰
DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL", os.getenv("DISCORD", "")
)

# 2. 動態讀取 GitHub Repository Variables (TARGET_PLANES)
raw_targets = os.getenv("TARGET_PLANES", "")

if raw_targets.strip():
    # 自動支援「換行分隔」或「逗號分隔」的飛機清單
    TARGETS = [
        t.strip().upper()
        for t in raw_targets.replace("\n", ",").split(",")
        if t.strip()
    ]
    print(f"📋 成功從 GitHub Variables 載入 {len(TARGETS)} 架目標飛機！")
else:
    # 若變數未設定，啟用備用清單
    print("ℹ️ 未偵測到 TARGET_PLANES 變數，啟用內建預設清單。")
    TARGETS = [
        "9M-LRU", "B-KQU", "B-LRJ", "B-LJE", "HL7628", "B-18918", "B-18311", "B-18007", "B-5390",
        "JA872A", "B-17812", "B-16715", "JA880A", "JA731A", "JA875A", "JA614A", "9V-SWI",
        "9V-SWJ", "B-2032", "B-6091", "B-6093", "HL7732", "HL8071", "HS-TKQ", "HL7783",
        "VN-A897", "VN-A327", "B-6538", "PK-GMH", "PH-BVD", "9V-OJJ", "JA73AB", "JA894A",
        "B-18101", "A6-EXR", "A6-EES", "A6-EET", "A6-EEP", "A6-DDE", "A6-BLV", "A6-BMH",
        "LX-NCL", "LX-VCF", "HL7423", "HL7419", "JA12KZ", "N771CK", "N454PA", "N249BA"
    ]

fr_api = FlightRadar24API()


def check_is_taiwan(text_or_code: str) -> bool:
    """精準判斷機場代碼或名稱是否屬於台灣 (已修復 URC 烏魯木齊誤報 Bug)"""
    if not text_or_code or text_or_code == "未知":
        return False
    s = str(text_or_code).upper().strip()

    tw_airport_codes = {
        # IATA (3碼)
        "TPE", "TSA", "KHH", "RMQ", "TNN", "HUN", "TTT", "MZG", "KIN", "CYI", "PIF", "LZN", "CMJ",
        # ICAO (4碼)
        "RCTP", "RCSS", "RCKH", "RCMQ", "RCNN", "RCHU", "RCFG", "RCBS", "RCFN", "RCKW", "RCMT", "RCLY"
    }

    # 1. 精準比對代碼
    if s in tw_airport_codes:
        return True

    # 2. 若為 4 碼 ICAO 代碼，且開頭必須是 RC (如 RCTP)
    if len(s) == 4 and s.startswith("RC"):
        return True

    # 3. 城市/國家名稱關鍵字比對
    tw_name_keywords = ["TAIPEI", "TAIWAN", "KAOHSIUNG", "TAICHUNG", "TAINAN", "台北", "台灣", "高雄", "台中", "台南"]
    return any(kw in s for kw in tw_name_keywords)


def fetch_direct_clickhandler(flight_obj_or_id) -> dict | None:
    """向 API 索取詳細飛行狀態與起降機場資訊"""
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

        ident = details.get("identification") or {}
        f_num = (
            (ident.get("number") or {}).get("default")
            or (ident.get("callsign") or {}).get("default")
            or "未知"
        )

        ac = details.get("aircraft") or {}
        f_reg = ac.get("registration") or "未知"

        return {
            "origin": origin,
            "destination": destination,
            "f_num": f_num,
            "f_reg": f_reg,
        }
    except Exception:
        return None


def search_single_target(target_raw: str, all_flights: list, flight_map_by_id: dict) -> dict | None:
    """雙階段搜尋 Worker (階段 1: 廣播直連 / 階段 2: Web API 線上反查)"""
    target_clean = target_raw.replace("-", "")

    # 階段 1：直播廣播數據直接比對
    for flight in all_flights:
        f_num = (getattr(flight, "number", "") or "").upper()
        f_callsign = (getattr(flight, "callsign", "") or "").upper()
        f_reg = (getattr(flight, "registration", "") or "").upper()

        matched = target_raw in [f_num, f_callsign, f_reg] or target_clean in [
            f_num.replace("-", ""),
            f_callsign.replace("-", ""),
            f_reg.replace("-", ""),
        ]

        if matched:
            details = fetch_direct_clickhandler(flight)
            if details:
                dest = details["destination"]
                is_tw = check_is_taiwan(dest)
                return {
                    "f_num": (
                        details["f_num"]
                        if details["f_num"] != "未知"
                        else (f_num or f_callsign)
                    ),
                    "f_reg": (
                        details["f_reg"]
                        if details["f_reg"] != "未知"
                        else (f_reg or target_raw)
                    ),
                    "route": f"{details['origin']} ➔ {dest}",
                    "is_taiwan": is_tw,
                }

    # 階段 2：若廣播數據未抓到，調用 Web API 進行線上反查
    search_url = f"https://www.flightradar24.com/v1/search/web/find?query={target_raw}"
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
                            dest = details["destination"]
                            is_tw = check_is_taiwan(dest)
                            return {
                                "f_num": (
                                    details["f_num"]
                                    if details["f_num"] != "未知"
                                    else target_raw
                                ),
                                "f_reg": (
                                    details["f_reg"]
                                    if details["f_reg"] != "未知"
                                    else target_raw
                                ),
                                "route": f"{details['origin']} ➔ {dest}",
                                "is_taiwan": is_tw,
                            }
    except Exception:
        pass

    return None


def send_discord_webhook(taiwan_flights: list):
    """專用 Discord 美化 Embed 警報推播函式"""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未偵測到 DISCORD 金鑰設定，跳過推播發送。")
        return

    fields = []
    for f in taiwan_flights:
        fields.append({
            "name": f"✈️ 航班：{f['f_num']} (機身號: {f['f_reg']})",
            "value": f"📍 **航線：** {f['route']}",
            "inline": False,
        })

    payload = {
        "embeds": [{
            "title": "🚨 FlightRadar24 彩繪機降落台灣警報",
            "description": (
                f"當前共有 **{len(taiwan_flights)}** 架目標班機預計或已降落台灣！"
            ),
            "color": 15158332,  # 警報紅色
            "fields": fields,
            "footer": {"text": "FlightRadar24 智慧航班監測系統"},
        }]
    }

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if res.status_code in [200, 204]:
            print("✅ 成功發送 Discord Webhook 通知！")
        else:
            print(f"❌ Discord 發送失敗，HTTP 狀態碼: {res.status_code}")
    except Exception as e:
        print(f"❌ Discord 發送異常: {e}")


def main():
    print("🚀 開始執行多輪穩定度巡檢機制...")

    matched_dict = {}  # 存放已查到的飛機數據
    stable_threshold = 10  # 連續 10 輪未查到數量無變化時停止
    last_unmatched_count = -1
    stable_counter = 0
    current_round = 0

    while True:
        current_round += 1
        pending_targets = [t for t in TARGETS if t not in matched_dict]
        current_unmatched_count = len(pending_targets)

        # 1. 若所有目標皆已找到，提前結束輪詢
        if current_unmatched_count == 0:
            print("🎉 所有監控目標皆已成功定位！")
            break

        # 2. 統計未查到的數量是否維持穩定
        if current_unmatched_count == last_unmatched_count:
            stable_counter += 1
        else:
            stable_counter = 1
            last_unmatched_count = current_unmatched_count

        # 3. 連續 10 輪穩定判定達成，結束輪詢
        if stable_counter >= stable_threshold:
            print(
                f"\n✅ 穩定度達成！未查到數量已連續 {stable_threshold} 輪維持在 {current_unmatched_count} 架，結束輪詢。"
            )
            break

        print(
            f"⚡ 第 {current_round:02d} 輪掃描... "
            f"（待查：{current_unmatched_count} 架 | 穩定進度：{stable_counter}/{stable_threshold}）"
        )

        # 每輪獲取最新的全球空域快照
        snapshot = fr_api.get_flights() or []
        flight_map_by_id = {
            getattr(f, "id", ""): f for f in snapshot if getattr(f, "id", "")
        }

        # 開啟 8 個線程併行掃描當輪未找到的目標
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_target = {
                executor.submit(
                    search_single_target, target, snapshot, flight_map_by_id
                ): target
                for target in pending_targets
            }

            for future in as_completed(future_to_target):
                target = future_to_target[future]
                try:
                    res = future.result()
                    if res:
                        matched_dict[target] = res
                except Exception:
                    pass

        time.sleep(1)

    # 結算降落台灣的航班
    taiwan_flights = [f for f in matched_dict.values() if f["is_taiwan"]]

    print(
        f"\n📊 掃描總結：共在空中抓到 {len(matched_dict)} 架目標，"
        f"其中 {len(taiwan_flights)} 架預計或已降落台灣。"
    )

    # 只有真正有抓到預計/已降落台灣的目標時才會觸發 Discord 通報
    if taiwan_flights:
        send_discord_webhook(taiwan_flights)


if __name__ == "__main__":
    main()
    }

    # 1. 檢查是否完全符合台灣機場代碼
    if s in tw_airport_codes:
        return True

    # 2. 若為 4 碼 ICAO 代碼，且開頭必須是 RC (例如 RCTP，不會誤抓 3 碼的 URC)
    if len(s) == 4 and s.startswith("RC"):
        return True

    # 3. 城市名稱關鍵字比對
    tw_name_keywords = ["TAIPEI", "TAIWAN", "KAOHSIUNG", "TAICHUNG", "TAINAN", "台北", "台灣", "高雄", "台中", "台南"]
    return any(kw in s for kw in tw_name_keywords)
)


def fetch_direct_clickhandler(flight_obj_or_id) -> dict | None:
    """向 API 索取詳細飛行狀態與起降機場資訊"""
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

        ident = details.get("identification") or {}
        f_num = (
            (ident.get("number") or {}).get("default")
            or (ident.get("callsign") or {}).get("default")
            or "未知"
        )

        ac = details.get("aircraft") or {}
        f_reg = ac.get("registration") or "未知"

        return {
            "origin": origin,
            "destination": destination,
            "f_num": f_num,
            "f_reg": f_reg,
        }
    except Exception:
        return None


def search_single_target(target_raw: str, all_flights: list, flight_map_by_id: dict) -> dict | None:
    """移植自 app.py 的雙階段搜尋 Worker"""
    target_clean = target_raw.replace("-", "")

    # 階段 1：直播廣播數據直接比對 (比對呼號、機號、航班號)
    for flight in all_flights:
        f_num = (getattr(flight, "number", "") or "").upper()
        f_callsign = (getattr(flight, "callsign", "") or "").upper()
        f_reg = (getattr(flight, "registration", "") or "").upper()

        matched = target_raw in [f_num, f_callsign, f_reg] or target_clean in [
            f_num.replace("-", ""),
            f_callsign.replace("-", ""),
            f_reg.replace("-", ""),
        ]

        if matched:
            details = fetch_direct_clickhandler(flight)
            if details:
                dest = details["destination"]
                is_tw = check_is_taiwan(dest)
                return {
                    "f_num": (
                        details["f_num"]
                        if details["f_num"] != "未知"
                        else (f_num or f_callsign)
                    ),
                    "f_reg": (
                        details["f_reg"]
                        if details["f_reg"] != "未知"
                        else (f_reg or target_raw)
                    ),
                    "route": f"{details['origin']} ➔ {dest}",
                    "is_taiwan": is_tw,
                }

    # 階段 2：若廣播數據未抓到，調用 Web API 進行線上反查
    search_url = f"https://www.flightradar24.com/v1/search/web/find?query={target_raw}"
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
                            dest = details["destination"]
                            is_tw = check_is_taiwan(dest)
                            return {
                                "f_num": (
                                    details["f_num"]
                                    if details["f_num"] != "未知"
                                    else target_raw
                                ),
                                "f_reg": (
                                    details["f_reg"]
                                    if details["f_reg"] != "未知"
                                    else target_raw
                                ),
                                "route": f"{details['origin']} ➔ {dest}",
                                "is_taiwan": is_tw,
                            }
    except Exception:
        pass

    return None


def send_discord_webhook(taiwan_flights: list):
    """專用 Discord 美化警報推播函式"""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未偵測到 DISCORD 金鑰設定，跳過推播。")
        return

    fields = []
    for f in taiwan_flights:
        fields.append({
            "name": f"✈️ 航班：{f['f_num']} (機身號: {f['f_reg']})",
            "value": f"📍 **航線：** {f['route']}",
            "inline": False,
        })

    payload = {
        "embeds": [{
            "title": "🚨 FlightRadar24 彩繪機降落台灣警報",
            "description": (
                f"當前共有 **{len(taiwan_flights)}** 架目標班機預計或已降落台灣！"
            ),
            "color": 15158332,  # 警報紅色
            "fields": fields,
            "footer": {"text": "FlightRadar24 智慧航班監測系統"},
        }]
    }

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if res.status_code in [200, 204]:
            print("✅ 成功發送 Discord Webhook 通知！")
        else:
            print(f"❌ Discord 發送失敗，HTTP 狀態碼: {res.status_code}")
    except Exception as e:
        print(f"❌ Discord 發送異常: {e}")


def main():
    print("🚀 開始執行多輪穩定度巡檢機制...")

    matched_dict = {}  # 存放已查到的飛機數據 {target: result_dict}
    stable_threshold = 10  # 連續 10 輪未查到數量無變化時停止
    last_unmatched_count = -1
    stable_counter = 0
    current_round = 0

    while True:
        current_round += 1
        pending_targets = [t for t in TARGETS if t not in matched_dict]
        current_unmatched_count = len(pending_targets)

        # 1. 如果所有目標皆已找到，提前完成退出
        if current_unmatched_count == 0:
            print("🎉 所有監控目標皆已成功定位！")
            break

        # 2. 統計未查到的數量是否維持穩定
        if current_unmatched_count == last_unmatched_count:
            stable_counter += 1
        else:
            stable_counter = 1
            last_unmatched_count = current_unmatched_count

        # 3. 連續 10 輪穩定判定
        if stable_counter >= stable_threshold:
            print(
                f"\n✅ 穩定度達成！未查到數量已連續 {stable_threshold} 輪維持在 {current_unmatched_count} 架，結束輪詢。"
            )
            break

        print(
            f"⚡ 第 {current_round:02d} 輪掃描... "
            f"（待查：{current_unmatched_count} 架 | 穩定進度：{stable_counter}/{stable_threshold}）"
        )

        # 每輪獲取最新的全球空域快照
        snapshot = fr_api.get_flights() or []
        flight_map_by_id = {
            getattr(f, "id", ""): f for f in snapshot if getattr(f, "id", "")
        }

        # 開啟 8 個線程併行掃描當輪未找到的目標
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_target = {
                executor.submit(
                    search_single_target, target, snapshot, flight_map_by_id
                ): target
                for target in pending_targets
            }

            for future in as_completed(future_to_target):
                target = future_to_target[future]
                try:
                    res = future.result()
                    if res:
                        matched_dict[target] = res
                except Exception:
                    pass

        # 稍作休息避開過度頻繁的 API 請求
        time.sleep(1)

    # 結算降落台灣的航班
    taiwan_flights = [f for f in matched_dict.values() if f["is_taiwan"]]

    print(
        f"\n📊 掃描總結：共在空中抓到 {len(matched_dict)} 架目標，"
        f"其中 {len(taiwan_flights)} 架預計或已降落台灣。"
    )

    if taiwan_flights:
        send_discord_webhook(taiwan_flights)


if __name__ == "__main__":
    main()
