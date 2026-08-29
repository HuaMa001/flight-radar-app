from concurrent.futures import ThreadPoolExecutor, as_completed
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

    return list(dict.fromkeys(targets))


TARGETS = load_targets("targets.txt")

# 使用 Session pool 提升 HTTP 連線複用率
http_session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
http_session.mount("https://", adapter)


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
    if not ts:
        return "未知"
    try:
        tz_tw = timezone(timedelta(hours=8))
        dt = datetime.fromtimestamp(ts, tz=tz_tw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "未知"


def check_is_taiwan(text_or_code: str) -> bool:
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
    if not registration or registration == "未知":
        return None
    try:
        url = f"https://api.planespotters.net/pub/photos/reg/{registration}"
        res = http_session.get(url, headers=get_headers(), timeout=3)
        if res.status_code == 200:
            photos = res.json().get("photos", [])
            if photos:
                return photos[0].get("thumbnail_large", {}).get("src") or photos[0].get("thumbnail", {}).get("src")
    except Exception:
        pass
    return None


def fetch_direct_clickhandler(fr_api_inst, flight_obj_or_id) -> dict | None:
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

        # 抓取起飛時間 (Departure Time)
        time_data = details.get("time") or {}
        std_ts = (time_data.get("scheduled") or {}).get("departure")
        etd_ts = (time_data.get("estimated") or {}).get("departure")
        atd_ts = (time_data.get("real") or {}).get("departure")

        dep_ts = etd_ts or std_ts or atd_ts
        dep_full = format_full_datetime(dep_ts)

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
            "dep_ts": dep_ts,
            "dep_time": dep_full,
            "image_url": image_url,
        }
    except Exception:
        return None


def search_single_target(target_raw: str, all_flights: list, flight_map_by_id: dict, fr_api_inst, is_retry: bool = False) -> dict | None:
    """單目標搜尋 Worker"""
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
                orig = details["origin"]
                dest = details["destination"]
                is_tw_origin = check_is_taiwan(orig)

                current_ts = int(time.time())
                dep_ts = details["dep_ts"]
                is_future = bool(dep_ts and int(dep_ts) > current_ts)

                return {
                    "target": target_raw,
                    "f_num": details["f_num"] if details["f_num"] != "未知" else (f_num or f_callsign),
                    "f_reg": details["f_reg"] if details["f_reg"] != "未知" else (f_reg or target_raw),
                    "ac_code": details["ac_code"],
                    "route": f"{orig} ➔ {dest}",
                    "dep_time": details["dep_time"],
                    "dep_ts": dep_ts,
                    "is_taiwan_origin": is_tw_origin,
                    "is_future": is_future,
                    "image_url": details["image_url"],
                    "source": "📡 直播廣播",
                }

    # 階段 2：Web API 反查
    delay = random.uniform(0.2, 0.4) if is_retry else random.uniform(0.05, 0.15)
    time.sleep(delay)
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
                            orig = details["origin"]
                            dest = details["destination"]
                            is_tw_origin = check_is_taiwan(orig)

                            current_ts = int(time.time())
                            dep_ts = details["dep_ts"]
                            is_future = bool(dep_ts and int(dep_ts) > current_ts)

                            return {
                                "target": target_raw,
                                "f_num": details["f_num"] if details["f_num"] != "未知" else target_raw,
                                "f_reg": details["f_reg"] if details["f_reg"] != "未知" else target_raw,
                                "ac_code": details["ac_code"],
                                "route": f"{orig} ➔ {dest}",
                                "dep_time": details["dep_time"],
                                "dep_ts": dep_ts,
                                "is_taiwan_origin": is_tw_origin,
                                "is_future": is_future,
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
            "title": f"🚨 彩繪機台灣起飛警報：{f['f_num']}",
            "color": 3447003,
            "fields": [
                {"name": "機身註冊號", "value": f"`{f['f_reg']}` ({f['ac_code']})", "inline": True},
                {"name": "航線狀況", "value": f"📍 **{f['route']}**", "inline": True},
                {"name": "預計起飛 (UTC+8)", "value": f"🕒 `{f['dep_time']}`", "inline": False},
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
                print(f"✅ 成功推播第 {i//10 + 1} 批共 {len(batch)} 架台灣起飛航班！")
            else:
                print(f"❌ Discord 發送失敗，HTTP 狀態碼: {res.status_code}")
        except Exception as e:
            print(f"❌ Discord 發送異常: {e}")


# --- 4. 主程序執行 ---
def main():
    if not TARGETS:
        print("🛑 沒有偵測到任何監控目標，程式結束。")
        return

    MAX_WORKERS = min(10, os.cpu_count() * 2 if os.cpu_count() else 8)
    print(f"🚀 開始監控 {len(TARGETS)} 架目標航班（開啟 {MAX_WORKERS} 線程加速）...")

    fr_api_inst = FlightRadar24API()
    matched_dict = {}

    # === 第一階段：高速並行掃描 ===
    stable_threshold_phase1 = 15
    last_unmatched_count = -1
    stable_counter = 0
    current_round = 0

    while True:
        current_round += 1
        pending_targets = [t for t in TARGETS if t not in matched_dict]
        current_unmatched_count = len(pending_targets)

        if current_unmatched_count == 0:
            print("\n🎉 第一階段：所有目標皆已順利定位！")
            break

        if current_unmatched_count == last_unmatched_count:
            stable_counter += 1
        else:
            stable_counter = 1
            last_unmatched_count = current_unmatched_count

        if stable_counter >= stable_threshold_phase1:
            print(
                f"\n✅ 第一階段數據已穩定！未查到數量連續 {stable_threshold_phase1} 輪維持在 "
                f"{current_unmatched_count} 架。"
            )
            break

        print(
            f"\n⚡ [第一階段] 第 {current_round:02d} 輪掃描... "
            f"（待查未飛：{current_unmatched_count} 架 | 穩定進度：{stable_counter}/{stable_threshold_phase1}）"
        )

        try:
            snapshot = fr_api_inst.get_flights() or []
        except Exception:
            snapshot = []

        flight_map_by_id = {
            getattr(f, "id", ""): f for f in snapshot if getattr(f, "id", "")
        }

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_target = {
                executor.submit(
                    search_single_target, target, snapshot, flight_map_by_id, fr_api_inst, False
                ): target
                for target in pending_targets
            }

            for future in as_completed(future_to_target):
                target = future_to_target[future]
                try:
                    res = future.result()
                    if res:
                        matched_dict[target] = res
                        print(
                            f"  └─ 🟢 [階段1] 抓到目標：{target} "
                            f"-> {res['f_num']} ({res['route']})"
                        )
                except Exception:
                    pass

        time.sleep(0.5)

    # === 第二階段：未查到目標二次深層核實 (含 Stable 驗證機制) ===
    unmatched_targets = [t for t in TARGETS if t not in matched_dict]
    if unmatched_targets:
        RETRY_WAIT_SEC = 10
        print(f"\n⏳ 進入二次核實階段... 暫停 {RETRY_WAIT_SEC} 秒以解除 API 頻率限制（剩餘未查到：{len(unmatched_targets)} 架）")
        time.sleep(RETRY_WAIT_SEC)

        stable_threshold_phase2 = 10
        retry_last_unmatched = -1
        retry_stable_counter = 0
        retry_round = 0

        while True:
            retry_round += 1
            retry_pending = [t for t in TARGETS if t not in matched_dict]
            retry_unmatched_count = len(retry_pending)

            if retry_unmatched_count == 0:
                print("\n🎉 二次核實：所有剩餘目標皆已全數定位補齊！")
                break

            if retry_unmatched_count == retry_last_unmatched:
                retry_stable_counter += 1
            else:
                retry_stable_counter = 1
                retry_last_unmatched = retry_unmatched_count

            if retry_stable_counter >= stable_threshold_phase2:
                print(
                    f"\n✅ 二次核實數據已完全穩定！未查到數量連續 {stable_threshold_phase2} 輪維持在 "
                    f"{retry_unmatched_count} 架，結束核實。"
                )
                break

            print(
                f"🔍 [二次核實] 第 {retry_round:02d} 輪深度掃描... "
                f"（剩餘未查：{retry_unmatched_count} 架 | 穩定進度：{retry_stable_counter}/{stable_threshold_phase2}）"
            )

            try:
                snapshot = fr_api_inst.get_flights() or []
            except Exception:
                snapshot = []

            flight_map_by_id = {
                getattr(f, "id", ""): f for f in snapshot if getattr(f, "id", "")
            }

            with ThreadPoolExecutor(max_workers=4) as executor:
                future_to_target = {
                    executor.submit(
                        search_single_target, target, snapshot, flight_map_by_id, fr_api_inst, True
                    ): target
                    for target in retry_pending
                }

                for future in as_completed(future_to_target):
                    target = future_to_target[future]
                    try:
                        res = future.result()
                        if res:
                            matched_dict[target] = res
                            print(
                                f"  └─ 🟢 [二次核實成功] 補抓到目標：{target} "
                                f"-> {res['f_num']} ({res['route']})"
                            )
                    except Exception:
                        pass

            time.sleep(1)

    # === 最終統計與推播 ===
    now_ts = int(time.time())-10*60
    limit_ts = now_ts + 10 * 60  # 目前時間 + 10 分鐘

    # 篩選條件：台灣起飛 且 起飛時間介於「現在」至「10分鐘以內」
    taiwan_departures = [
        f for f in matched_dict.values()
        if f["is_taiwan_origin"] and f["dep_ts"] and now_ts <= int(f["dep_ts"]) < limit_ts
    ]

    final_unmatched = len(TARGETS) - len(matched_dict)

    print(
        f"\n📊 掃描結果總結：\n"
        f" • 監控目標數：{len(TARGETS)} 架\n"
        f" • 成功定位（系統/空中）：{len(matched_dict)} 架\n"
        f" • 🛫 30分鐘內即將自台灣起飛：{len(taiwan_departures)} 架\n"
        f" • ❌ 未在空中/無資料：{final_unmatched} 架"
    )

    if taiwan_departures:
        send_discord_webhook(taiwan_departures)
    else:
        print("ℹ️ 目前沒有目標班機將在 30 分鐘內自台灣起飛，不發送 Discord 警報。")


if __name__ == "__main__":
    main()
        
