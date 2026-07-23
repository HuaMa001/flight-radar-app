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
    print("⚠️ 未偵測到 TARGET_PLANES 變數，將不使用備用清單。")
    TARGETS = []

fr_api = FlightRadar24API()

# 建立全域 Session 以保持連線並模擬擬真瀏覽器請求
http_session = requests.Session()
http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.flightradar24.com/",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
})


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

    if s in tw_airport_codes:
        return True

    if len(s) == 4 and s.startswith("RC"):
        return True

    tw_name_keywords = ["TAIPEI", "TAIWAN", "KAOHSIUNG", "TAICHUNG", "TAINAN", "台北", "台灣", "高雄", "台中", "台南"]
    return any(kw in s for kw in tw_name_keywords)


def fetch_planespotters_image(registration: str) -> str | None:
    """【備用方案】從 Planespotters.net 免費 API 依據機身註冊號搜尋照片"""
    if not registration or registration == "未知":
        return None
    try:
        url = f"https://api.planespotters.net/pub/photos/reg/{registration}"
        res = http_session.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            photos = data.get("photos", [])
            if photos:
                # 優先拿大圖，沒有就拿縮圖
                return photos[0].get("thumbnail_large", {}).get("src") or photos[0].get("thumbnail", {}).get("src")
    except Exception:
        pass
    return None


def fetch_direct_clickhandler(flight_obj_or_id) -> dict | None:
    """向 API 索取詳細飛行狀態與起降機場資訊，並自動擷取飛機圖片"""
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

        # --- 🖼️ 抓取飛機照片邏輯 ---
        image_url = None
        images = ac.get("images") or {}
        large_images = images.get("large") or images.get("medium") or []
        
        # 1. 優先嘗試從 FR24 內建的 JetPhotos 獲取大圖
        if large_images and isinstance(large_images, list) and len(large_images) > 0:
            image_url = large_images[0].get("src")

        # 2. 若 FR24 沒有照片，向 Planespotters.net 免費 API 反查
        if not image_url and f_reg != "未知":
            image_url = fetch_planespotters_image(f_reg)

        return {
            "origin": origin,
            "destination": destination,
            "f_num": f_num,
            "f_reg": f_reg,
            "image_url": image_url,  # 回傳圖片網址
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
                    "image_url": details.get("image_url"),
                }

    # 階段 2：線上反查
    search_url = f"https://www.flightradar24.com/v1/search/web/find?query={target_raw}"

    try:
        res = http_session.get(search_url, timeout=5)
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
                                "image_url": details.get("image_url"),
                            }
    except Exception:
        pass

    return None


def send_discord_webhook(taiwan_flights: list):
    """專用 Discord 美化 Embed 警報推播函式 (支援大圖預覽)"""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未偵測到 DISCORD 金鑰設定，跳過推播發送。")
        return

    embeds = []
    
    # 為每一架符合條件的班機建立獨立的 Embed 卡片，這樣每架飛機都能顯示各自的照片
    for f in taiwan_flights:
        embed = {
            "title": f"🚨 彩繪機降落警報：{f['f_num']}",
            "color": 15158332,
            "fields": [
                {"name": "機身註冊號", "value": f"`{f['f_reg']}`", "inline": True},
                {"name": "航線狀況", "value": f"📍 **{f['route']}**", "inline": True},
            ],
            "footer": {"text": "FlightRadar24 智慧航班監測系統"},
        }

        # 如果有找到圖片，嵌入到 Embed 中
        if f.get("image_url"):
            embed["image"] = {"url": f["image_url"]}

        embeds.append(embed)

    payload = {"embeds": embeds}

    try:
        res = http_session.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if res.status_code in [200, 204]:
            print("✅ 成功發送 Discord Webhook 通知與圖片！")
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

        with ThreadPoolExecutor(max_workers=3) as executor:
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
