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

if raw_targets and raw_targets.strip():
    TARGETS = [
        t.strip().upper()
        for t in raw_targets.replace("\n", ",").split(",")
        if t.strip()
    ]
    print(f"📋 成功從 GitHub Variables 載入 {len(TARGETS)} 架目標飛機！")
else:
    print("⚠️ 未偵測到 TARGET_PLANES 變數或內容為空，將不使用備用清單。")
    TARGETS = []

fr_api = FlightRadar24API()


def check_is_taiwan(text_or_code: str) -> bool:
    """精準判斷機場代碼或名稱是否屬於台灣 (已修復 URC 烏魯木齊誤報 Bug)"""
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
    """雙階段搜尋 Worker (加入完整的防封鎖 Header 模擬)"""
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

    # 階段 2：若廣播數據未抓到，調用 Web API 進行線上反查 (加入擬真 Header)
    search_url = f"https://www.flightradar24.com/v1/search/web/find?query={target_raw}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.flightradar24.com/",
    }

    try:
        res = requests.get(search_url, headers=headers, timeout=5)
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
            "color": 15158332,
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
    if not TARGETS:
        print("🛑 尚未在 GitHub Settings -> Variables 設定 TARGET_PLANES，程式停止執行。")
        return

    print("🚀 開始執行多輪穩定度巡檢機制...")

    matched_dict = {}
    stable_threshold = 10
    last_unmatched_count = -1
    stable_counter = 0
    current_round = 0

    while True:
        current_round += 1
        pending_targets = [t for t in TARGETS if t not in matched_dict]
        current_unmatched_count = len(pending_targets)

        if current_unmatched_count == 0:
            print("🎉 所有監控目標皆已成功定位！")
            break

        if current_unmatched_count == last_unmatched_count:
            stable_counter += 1
        else:
            stable_counter = 1
            last_unmatched_count = current_unmatched_count

        if stable_counter >= stable_threshold:
            print(
                f"\n✅ 穩定度達成！未查到數量已連續 {stable_threshold} 輪維持在 {current_unmatched_count} 架，結束輪詢。"
            )
            break

        print(
            f"⚡ 第 {current_round:02d} 輪掃描... "
            f"（待查：{current_unmatched_count} 架 | 穩定進度：{stable_counter}/{stable_threshold}）"
        )

        snapshot = fr_api.get_flights() or []
        flight_map_by_id = {
            getattr(f, "id", ""): f for f in snapshot if getattr(f, "id", "")
        }

        # 降低線程數至 4，減緩 HTTP 請求頻率
        with ThreadPoolExecutor(max_workers=4) as executor:
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

    taiwan_flights = [f for f in matched_dict.values() if f["is_taiwan"]]

    print(
        f"\n📊 掃描總結：共在空中抓到 {len(matched_dict)} 架目標，"
        f"其中 {len(taiwan_flights)} 架預計或已降落台灣。"
    )

    if taiwan_flights:
        send_discord_webhook(taiwan_flights)


if __name__ == "__main__":
    main()
    if s in tw_airport_codes:
        return True

    if len(s) == 4 and s.startswith("RC"):
        return True

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
    """雙階段搜尋 Worker (加入完整的防封鎖 Header 模擬)"""
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

    # 階段 2：若廣播數據未抓到，調用 Web API 進行線上反查 (加入擬真 Header)
    search_url = f"https://www.flightradar24.com/v1/search/web/find?query={target_raw}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.flightradar24.com/",
    }

    try:
        res = requests.get(search_url, headers=headers, timeout=5)
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
            "color": 15158332,
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

    matched_dict = {}
    stable_threshold = 10
    last_unmatched_count = -1
    stable_counter = 0
    current_round = 0

    while True:
        current_round += 1
        pending_targets = [t for t in TARGETS if t not in matched_dict]
        current_unmatched_count = len(pending_targets)

        if current_unmatched_count == 0:
            print("🎉 所有監控目標皆已成功定位！")
            break

        if current_unmatched_count == last_unmatched_count:
            stable_counter += 1
        else:
            stable_counter = 1
            last_unmatched_count = current_unmatched_count

        if stable_counter >= stable_threshold:
            print(
                f"\n✅ 穩定度達成！未查到數量已連續 {stable_threshold} 輪維持在 {current_unmatched_count} 架，結束輪詢。"
            )
            break

        print(
            f"⚡ 第 {current_round:02d} 輪掃描... "
            f"（待查：{current_unmatched_count} 架 | 穩定進度：{stable_counter}/{stable_threshold}）"
        )

        snapshot = fr_api.get_flights() or []
        flight_map_by_id = {
            getattr(f, "id", ""): f for f in snapshot if getattr(f, "id", "")
        }

        # 降低線程數至 4，減緩 HTTP 請求頻率，大幅降低 GitHub Actions 被阻擋的機率
        with ThreadPoolExecutor(max_workers=4) as executor:
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

    taiwan_flights = [f for f in matched_dict.values() if f["is_taiwan"]]

    print(
        f"\n📊 掃描總結：共在空中抓到 {len(matched_dict)} 架目標，"
        f"其中 {len(taiwan_flights)} 架預計或已降落台灣。"
    )

    if taiwan_flights:
        send_discord_webhook(taiwan_flights)


if __name__ == "__main__":
    main()
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
    
