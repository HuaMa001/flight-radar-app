from datetime import datetime, timedelta, timezone
import os
import random
import time
import requests
from FlightRadarAPI import FlightRadar24API

# --- 1. 環境變數與 targets.txt 讀取邏輯 ---
DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL", os.getenv("DISCORD", "")
)


def load_targets(filepath: str = "targets.txt") -> list[str]:
    """從 targets.txt 讀取監控清單，若不存在則嘗試讀取環境變數 TARGET_PLANES"""
    targets = []

    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                targets = [
                    line.strip().upper()
                    for line in f
                    if line.strip() and not line.strip().startswith("#")
                ]
            print(f"📁 成功從 `{filepath}` 載入 {len(targets)} 架目標飛機！")
        except Exception as e:
            print(f"⚠️ 讀取 `{filepath}` 失敗: {e}")

    if not targets:
        raw_targets = os.getenv("TARGET_PLANES", "")
        if raw_targets and raw_targets.strip():
            cleaned_raw = (
                raw_targets.replace("\r", "")
                .replace("\n", ",")
                .replace("，", ",")
                .replace('"', "")
                .replace("'", "")
            )
            targets = [
                t.strip().upper() for t in cleaned_raw.split(",") if t.strip()
            ]
            print(f"📋 成功從環境變數載入 {len(targets)} 架目標飛機！")

    # 去重並保持順序
    return list(dict.fromkeys(targets))


TARGETS = load_targets("targets.txt")

http_session = requests.Session()


def get_headers():
    """產生擬真瀏覽器請求 Header"""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    ]
    return {
        "User-Agent": random.choice(user_agents),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.flightradar24.com/",
        "Origin": "https://www.flightradar24.com",
    }


# --- 2. 輔助與 API 查詢函式 ---
def format_full_datetime(ts: int | None) -> str:
    """Unix Timestamp 轉 UTC+8 字串 (YYYY-MM-DD HH:MM)"""
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
        "RCTP", "RCSS", "RCKH", "RCMQ", "RCNN", "RCHU", "RCFG", "RCBS", "RCFN", "RCKW", "RCMT", "RCLY",
    }

    if s in tw_airport_codes or (len(s) == 4 and s.startswith("RC")):
        return True

    tw_name_keywords = [
        "TAIPEI", "TAIWAN", "KAOHSIUNG", "TAICHUNG", "TAINAN",
        "台北", "台灣", "高雄", "台中", "台南",
    ]
    return any(kw in s for kw in tw_name_keywords)


def fetch_planespotters_image(registration: str) -> str | None:
    """備用圖片 API"""
    if not registration or registration == "未知":
        return None
    try:
        url = f"https://api.planespotters.net/pub/photos/reg/{registration}"
        res = http_session.get(url, headers=get_headers(), timeout=4)
        if res.status_code == 200:
            photos = res.json().get("photos", [])
            if photos:
                return photos[0].get("thumbnail_large", {}).get("src") or photos[0].get("thumbnail", {}).get("src")
    except Exception:
        pass
    return None


def fetch_direct_clickhandler(fr_api_inst, flight_obj_or_id) -> dict | None:
    """向 FR24 取得單一航班詳細資訊"""
    try:
        if hasattr(flight_obj_or_id, "id"):
            details = fr_api_inst.get_flight_details(flight_obj_or_id)
        else:
            class DummyFlight:
                def __init__(self, fid):
                    self.id = fid
            details = fr_api_inst.get_flight_details(DummyFlight(flight_obj_or_id))

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
        ac_code = (ac.get("model") or {}).get("code") or "未知"

        time_data = details.get("time") or {}
        sta_ts = (time_data.get("scheduled") or {}).get("arrival")
        eta_ts = (time_data.get("estimated") or {}).get("arrival")
        ata_ts = (time_data.get("real") or {}).get("arrival")

        eta_full = format_full_datetime(eta_ts or ata_ts or sta_ts)

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
            "f_num": f_num,
            "f_reg": f_reg,
            "ac_code": ac_code,
            "eta_time": eta_full,
            "image_url": image_url,
        }
    except Exception:
        return None


def search_single_target(target_raw: str, all_flights: list, flight_map_by_id: dict, fr_api_inst) -> dict | None:
    """雙階段搜尋 Worker (單線程版)"""
    target_clean = target_raw.replace("-", "")

    # 階段 1：直播廣播數據記憶體快速比對
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
            details = fetch_direct_clickhandler(fr_api_inst, flight)
            if details:
                dest = details["destination"]
                is_tw = check_is_taiwan(dest)
                return {
                    "target": target_raw,
                    "f_num": details["f_num"] if details["f_num"] != "未知" else (f_num or f_callsign),
                    "f_reg": details["f_reg"] if details["f_reg"] != "未知" else (f_reg or target_raw),
                    "ac_code": details["ac_code"],
                    "route": f"{details['origin']} ➔ {dest}",
                    "eta_time": details["eta_time"],
                    "is_taiwan": is_tw,
                    "image_url": details["image_url"],
                    "source": "📡 直播廣播",
                }

    # 階段 2： Web API 反查（加入 0.4 秒間隔防 Rate Limit）
    time.sleep(0.4)
    search_url = f"https://www.flightradar24.com/v1/search/web/find?query={target_raw}"

    try:
        res = http_session.get(search_url, headers=get_headers(), timeout=5)
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
                        details = fetch_direct_clickhandler(fr_api_inst, target_obj)

                        if details:
                            dest = details["destination"]
                            is_tw = check_is_taiwan(dest)
                            return {
                                "target": target_raw,
                                "f_num": details["f_num"] if details["f_num"] != "未知" else target_raw,
                                "f_reg": details["f_reg"] if details["f_reg"] != "未知" else target_raw,
                                "ac_code": details["ac_code"],
                                "route": f"{details['origin']} ➔ {dest}",
                                "eta_time": details["eta_time"],
                                "is_taiwan": is_tw,
                                "image_url": details["image_url"],
                                "source": "🔍 Web API 反查",
                            }
    except Exception:
        pass

    return None


# --- 3. Discord 推播發送 ---
def send_discord_webhook(taiwan_flights: list):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 DISCORD Webhook URL，跳過推播。")
        return

    embeds = []
    for f in taiwan_flights:
        embed = {
            "title": f"🚨 彩繪機降落台灣警報：{f['f_num']}",
            "color": 15158332,
            "fields": [
                {"name": "機身註冊號", "value": f"`{f['f_reg']}` ({f['ac_code']})", "inline": True},
                {"name": "航線狀況", "value": f"📍 **{f['route']}**", "inline": True},
                {"name": "預計抵達 (UTC+8)", "value": f"🕒 `{f['eta_time']}`", "inline": False},
            ],
            "footer": {"text": f"FR24 智慧航班監測系統 • 來源：{f['source']}"},
        }
        if f.get("image_url"):
            embed["image"] = {"url": f["image_url"]}

        embeds.append(embed)

    for i in range(0, len(embeds), 10):
        batch = embeds[i : i + 10]
        payload = {"embeds": batch}
        try:
            res = http_session.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
            if res.status_code in [200, 204]:
                print(f"✅ 成功推播第 {i//10 + 1} 批共 {len(batch)} 架降落台灣航班！")
            else:
                print(f"❌ Discord 發送失敗，HTTP 狀態碼: {res.status_code}")
        except Exception as e:
            print(f"❌ Discord 發送異常: {e}")


# --- 4. 主程序執行 ---
def main():
    if not TARGETS:
        print("🛑 沒有偵測到任何監控目標，程式結束。")
        return

    print(f"🚀 開始監控 {len(TARGETS)} 架目標航班...")

    fr_api_inst = FlightRadar24API()

    # 1. 一次性獲取全球廣播快照
    try:
        snapshot = fr_api_inst.get_flights() or []
    except Exception:
        snapshot = []

    print(f"📡 成功獲取全球廣播快照，包含 {len(snapshot)} 架即時航班資訊。")

    flight_map_by_id = {
        getattr(f, "id", ""): f for f in snapshot if getattr(f, "id", "")
    }

    matched_dict = {}

    # 2. 單線程依序查詢，確保絕不觸發 Cloudflare 風控
    for idx, target in enumerate(TARGETS, start=1):
        res = search_single_target(target, snapshot, flight_map_by_id, fr_api_inst)
        if res:
            matched_dict[target] = res
            print(f"  [{idx:02d}/{len(TARGETS)}] 🟢 抓到目標 {target} -> {res['f_num']} ({res['route']})")
        else:
            print(f"  [{idx:02d}/{len(TARGETS)}] 🔴 未在空中：{target}")

    taiwan_flights = [f for f in matched_dict.values() if f["is_taiwan"]]

    print(
        f"\n📊 掃描結果總結：\n"
        f" • 監控目標數：{len(TARGETS)} 架\n"
        f" • 在空中抓到：{len(matched_dict)} 架\n"
        f" • 🇹🇼 預計/已降落台灣：{len(taiwan_flights)} 架\n"
        f" • 尚未起飛/未在空中：{len(TARGETS) - len(matched_dict)} 架"
    )

    if taiwan_flights:
        send_discord_webhook(taiwan_flights)
    else:
        print("ℹ️ 目前沒有目標班機降落台灣，不發送 Discord 警報。")


if __name__ == "__main__":
    main()
