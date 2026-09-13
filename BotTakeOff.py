from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import os
import random
import time
import requests
from FlightRadarAPI import FlightRadar24API


# ============================================================
# 1. 環境變數與 targets.txt
# ============================================================

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    os.getenv("DISCORD", "")
)


def load_targets(filepath: str = "targets.txt") -> list[str]:
    """
    從 targets.txt 讀取監控清單。
    如果不存在或沒有內容，則嘗試讀取 TARGET_PLANES 環境變數。
    """

    targets = []

    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                targets = [
                    line.strip().upper()
                    for line in f
                    if line.strip()
                    and not line.strip().startswith("#")
                ]

            print(
                f"📁 成功從 `{filepath}` 載入 "
                f"{len(targets)} 架目標飛機！"
            )

        except Exception as e:
            print(f"⚠️ 讀取 `{filepath}` 失敗: {e}")

    if not targets:
        raw_targets = os.getenv("TARGET_PLANES", "")

        if raw_targets and raw_targets.strip():

            cleaned_raw = (
                raw_targets
                .replace("\r", "")
                .replace("\n", ",")
                .replace("，", ",")
                .replace('"', "")
                .replace("'", "")
            )

            targets = [
                t.strip().upper()
                for t in cleaned_raw.split(",")
                if t.strip()
            ]

            print(
                f"📋 成功從環境變數載入 "
                f"{len(targets)} 架目標飛機！"
            )

    # 去除重複
    return list(dict.fromkeys(targets))


TARGETS = load_targets("targets.txt")


# ============================================================
# 2. HTTP Session
# ============================================================

http_session = requests.Session()

adapter = requests.adapters.HTTPAdapter(
    pool_connections=30,
    pool_maxsize=30,
    max_retries=0
)

http_session.mount("https://", adapter)
http_session.mount("http://", adapter)


# ============================================================
# 3. Header
# ============================================================

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
]


def get_headers():
    """
    產生請求 Header。
    """

    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.flightradar24.com/",
        "Origin": "https://www.flightradar24.com",
        "Connection": "keep-alive",
    }


# ============================================================
# 4. 基本工具
# ============================================================

def normalize_target(value: str) -> str:
    """
    標準化比對字串。

    例如：
        B-1234 -> B1234
        b-1234 -> B1234
    """

    if not value:
        return ""

    return (
        str(value)
        .upper()
        .strip()
        .replace("-", "")
        .replace(" ", "")
    )


def format_full_datetime(ts: int | None) -> str:
    """
    Unix timestamp -> 台灣時間
    """

    if not ts:
        return "未知"

    try:
        tz_tw = timezone(timedelta(hours=8))

        dt = datetime.fromtimestamp(
            int(ts),
            tz=tz_tw
        )

        return dt.strftime("%Y-%m-%d %H:%M")

    except Exception:
        return "未知"


def check_is_taiwan(text_or_code: str) -> bool:
    """
    判斷機場 / 地點是否為台灣。
    """

    if not text_or_code or text_or_code == "未知":
        return False

    s = str(text_or_code).upper().strip()

    tw_airport_codes = {
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

    if s in tw_airport_codes:
        return True

    # ICAO 台灣機場通常 RC 開頭
    if len(s) == 4 and s.startswith("RC"):
        return True

    tw_name_keywords = [
        "TAIPEI",
        "TAIWAN",
        "KAOHSIUNG",
        "TAICHUNG",
        "TAINAN",
        "HUALIEN",
        "TAITUNG",
        "PENGHU",
        "KINMEN",
        "MATSU",
        "台北",
        "台灣",
        "高雄",
        "台中",
        "台南",
        "花蓮",
        "台東",
        "澎湖",
        "金門",
        "馬祖",
    ]

    return any(
        kw in s
        for kw in tw_name_keywords
    )


# ============================================================
# 5. PlaneSpotters
# ============================================================

def fetch_planespotters_image(
    registration: str
) -> str | None:

    if not registration or registration == "未知":
        return None

    try:

        url = (
            "https://api.planespotters.net/pub/photos/reg/"
            f"{registration}"
        )

        res = http_session.get(
            url,
            headers=get_headers(),
            timeout=3
        )

        if res.status_code == 200:

            photos = res.json().get(
                "photos",
                []
            )

            if photos:

                photo = photos[0]

                return (
                    photo.get(
                        "thumbnail_large",
                        {}
                    ).get("src")

                    or

                    photo.get(
                        "thumbnail",
                        {}
                    ).get("src")
                )

    except Exception:
        pass

    return None


# ============================================================
# 6. FlightRadar24 詳細資料
# ============================================================

def fetch_direct_clickhandler(
    fr_api_inst,
    flight_obj_or_id
) -> dict | None:

    try:

        # ----------------------------------------------------
        # 建立 Flight object
        # ----------------------------------------------------

        if hasattr(flight_obj_or_id, "id"):

            flight_obj = flight_obj_or_id

        else:

            class DummyFlight:

                def __init__(self, fid):
                    self.id = fid

            flight_obj = DummyFlight(
                flight_obj_or_id
            )

        # ----------------------------------------------------
        # FR24 詳細資料
        # ----------------------------------------------------

        details = fr_api_inst.get_flight_details(
            flight_obj
        )

        if not details or not isinstance(details, dict):
            return None

        # ----------------------------------------------------
        # Airport
        # ----------------------------------------------------

        airport = details.get(
            "airport"
        ) or {}

        origin_obj = (
            airport.get("origin")
            or {}
        ).get("code") or {}

        destination_obj = (
            airport.get("destination")
            or {}
        ).get("code") or {}

        origin = (
            origin_obj.get("iata")
            or origin_obj.get("icao")
            or (
                airport.get("origin")
                or {}
            ).get("name")
            or "未知"
        )

        destination = (
            destination_obj.get("iata")
            or destination_obj.get("icao")
            or (
                airport.get("destination")
                or {}
            )
            .get("pluginData", {})
            .get("details", {})
            .get("name")
            or "未知"
        )

        # ----------------------------------------------------
        # Identification
        # ----------------------------------------------------

        ident = details.get(
            "identification"
        ) or {}

        f_num = (
            (
                ident.get("number")
                or {}
            ).get("default")

            or

            (
                ident.get("callsign")
                or {}
            ).get("default")

            or "未知"
        )

        # ----------------------------------------------------
        # Aircraft
        # ----------------------------------------------------

        aircraft = details.get(
            "aircraft"
        ) or {}

        f_reg = (
            aircraft.get(
                "registration"
            )
            or "未知"
        )

        aircraft_model = (
            aircraft.get("model")
            or {}
        )

        ac_code = (
            aircraft_model.get("code")
            or "未知"
        )

        # ----------------------------------------------------
        # 時間
        # ----------------------------------------------------

        time_data = details.get(
            "time"
        ) or {}

        scheduled = (
            time_data.get("scheduled")
            or {}
        )

        estimated = (
            time_data.get("estimated")
            or {}
        )

        real = (
            time_data.get("real")
            or {}
        )

        std_ts = scheduled.get(
            "departure"
        )

        etd_ts = estimated.get(
            "departure"
        )

        atd_ts = real.get(
            "departure"
        )

        # 優先 estimated -> scheduled -> real
        dep_ts = (
            etd_ts
            or std_ts
            or atd_ts
        )

        dep_full = format_full_datetime(
            dep_ts
        )

        # ----------------------------------------------------
        # 飛機圖片
        # ----------------------------------------------------

        image_url = None

        images = (
            aircraft.get("images")
            or {}
        )

        large_images = (
            images.get("large")
            or images.get("medium")
            or []
        )

        if (
            isinstance(large_images, list)
            and large_images
        ):

            image_url = (
                large_images[0]
                .get("src")
            )

        # FR24 沒圖片 -> PlaneSpotters
        if (
            not image_url
            and f_reg != "未知"
        ):

            image_url = fetch_planespotters_image(
                f_reg
            )

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


# ============================================================
# 7. 建立高速航班索引
# ============================================================

def build_flight_index(
    all_flights: list
) -> dict:

    """
    將目前所有航班建立成 Hash Map。

    不再每個 target 都掃一次全部航班。
    """

    index = {
        "registration": {},
        "number": {},
        "callsign": {},
        "normalized": {},
    }

    for flight in all_flights:

        f_num = (
            getattr(
                flight,
                "number",
                ""
            )
            or ""
        ).upper().strip()

        f_callsign = (
            getattr(
                flight,
                "callsign",
                ""
            )
            or ""
        ).upper().strip()

        f_reg = (
            getattr(
                flight,
                "registration",
                ""
            )
            or ""
        ).upper().strip()

        # ----------------------------------------------------
        # 精準 Registration
        # ----------------------------------------------------

        if f_reg:
            index["registration"][
                f_reg
            ] = flight

            normalized = normalize_target(
                f_reg
            )

            if normalized:
                index["normalized"][
                    normalized
                ] = flight

        # ----------------------------------------------------
        # 精準 Flight Number
        # ----------------------------------------------------

        if f_num:
            index["number"][
                f_num
            ] = flight

            normalized = normalize_target(
                f_num
            )

            if normalized:
                index["normalized"][
                    normalized
                ] = flight

        # ----------------------------------------------------
        # 精準 Callsign
        # ----------------------------------------------------

        if f_callsign:
            index["callsign"][
                f_callsign
            ] = flight

            normalized = normalize_target(
                f_callsign
            )

            if normalized:
                index["normalized"][
                    normalized
                ] = flight

    return index


# ============================================================
# 8. 精準搜尋單一目標
# ============================================================

def find_target_in_index(
    target: str,
    flight_index: dict
):

    target_upper = (
        target
        .upper()
        .strip()
    )

    target_normalized = normalize_target(
        target_upper
    )

    # --------------------------------------------------------
    # 第一優先：Registration
    # --------------------------------------------------------

    flight = (
        flight_index["registration"]
        .get(target_upper)
    )

    if flight:
        return flight, "EXACT_REGISTRATION"

    # --------------------------------------------------------
    # 第二優先：Flight Number
    # --------------------------------------------------------

    flight = (
        flight_index["number"]
        .get(target_upper)
    )

    if flight:
        return flight, "EXACT_FLIGHT_NUMBER"

    # --------------------------------------------------------
    # 第三優先：Callsign
    # --------------------------------------------------------

    flight = (
        flight_index["callsign"]
        .get(target_upper)
    )

    if flight:
        return flight, "EXACT_CALLSIGN"

    # --------------------------------------------------------
    # 第四優先：去掉 - 和空白
    # --------------------------------------------------------

    if target_normalized:

        flight = (
            flight_index["normalized"]
            .get(target_normalized)
        )

        if flight:
            return flight, "NORMALIZED_MATCH"

    return None, None


# ============================================================
# 9. 將 Flight object + details 組成結果
# ============================================================

def build_result(
    target_raw: str,
    flight,
    details: dict,
    source: str,
    match_type: str
) -> dict | None:

    if not details:
        return None

    orig = details["origin"]
    dest = details["destination"]

    is_tw_origin = check_is_taiwan(
        orig
    )

    dep_ts = details["dep_ts"]

    current_ts = int(
        time.time()
    )

    # 是否為未來航班
    is_future = bool(
        dep_ts
        and int(dep_ts) > current_ts
    )

    f_num = details["f_num"]

    if f_num == "未知":

        f_num = (
            getattr(
                flight,
                "number",
                ""
            )
            or getattr(
                flight,
                "callsign",
                ""
            )
            or target_raw
        )

    f_reg = details["f_reg"]

    if f_reg == "未知":

        f_reg = (
            getattr(
                flight,
                "registration",
                ""
            )
            or target_raw
        )

    return {
        "target": target_raw,

        "f_num": f_num,

        "f_reg": f_reg,

        "ac_code": details[
            "ac_code"
        ],

        "route": (
            f"{orig} ➔ {dest}"
        ),

        "dep_time": details[
            "dep_time"
        ],

        "dep_ts": dep_ts,

        "is_taiwan_origin": (
            is_tw_origin
        ),

        "is_future": is_future,

        "image_url": details[
            "image_url"
        ],

        "source": source,

        "match_type": match_type,
    }


# ============================================================
# 10. Web API 補查
# ============================================================

def web_search_target(
    target_raw: str,
    flight_map_by_id: dict,
    fr_api_inst
) -> dict | None:

    """
    只給第一階段沒有找到的目標使用。

    Web Search -> live ID -> get_flight_details
    """

    target_raw = (
        target_raw
        .upper()
        .strip()
    )

    try:

        search_url = (
            "https://www.flightradar24.com/"
            "v1/search/web/find"
            f"?query={target_raw}"
        )

        res = http_session.get(
            search_url,
            headers=get_headers(),
            timeout=4
        )

        if res.status_code != 200:
            return None

        data = res.json()

        results = data.get(
            "results",
            []
        )

        if not results:
            return None

        # ----------------------------------------------------
        # 精準選擇 live 結果
        # ----------------------------------------------------

        candidates = []

        for item in results:

            if item.get("type") != "live":
                continue

            live_id = str(
                item.get("id", "")
            ).strip()

            if not live_id:
                continue

            candidates.append(
                (
                    live_id,
                    item
                )
            )

        if not candidates:
            return None

        # ----------------------------------------------------
        # 逐個 live candidate 驗證
        # ----------------------------------------------------

        target_norm = normalize_target(
            target_raw
        )

        for live_id, item in candidates:

            target_obj = (
                flight_map_by_id.get(
                    live_id
                )
                or live_id
            )

            details = fetch_direct_clickhandler(
                fr_api_inst,
                target_obj
            )

            if not details:
                continue

            # ------------------------------------------------
            # 驗證搜尋結果是否真的對應 target
            # ------------------------------------------------

            values = [
                details.get("f_num", ""),
                details.get("f_reg", ""),
            ]

            # 如果 API 有 callsign
            identification = (
                item.get(
                    "identification"
                )
                or {}
            )

            values.append(
                identification.get(
                    "callsign",
                    ""
                )
            )

            exact = False
            normalized_match = False

            for value in values:

                value = (
                    str(value)
                    .upper()
                    .strip()
                )

                if not value:
                    continue

                if value == target_raw:
                    exact = True
                    break

                if (
                    normalize_target(value)
                    == target_norm
                ):
                    normalized_match = True

            # ------------------------------------------------
            # 搜尋結果如果沒有直接可驗證欄位
            # 仍接受 live 結果，但降低來源標示
            # ------------------------------------------------

            if exact:

                return build_result(
                    target_raw,
                    target_obj,
                    details,
                    "🔍 Web API 精準補查",
                    "WEB_EXACT"
                )

            if normalized_match:

                return build_result(
                    target_raw,
                    target_obj,
                    details,
                    "🔍 Web API 標準化匹配",
                    "WEB_NORMALIZED"
                )

        return None

    except Exception:
        return None


# ============================================================
# 11. Discord
# ============================================================

def send_discord_webhook(
    taiwan_flights: list
):

    if not DISCORD_WEBHOOK_URL:

        print(
            "⚠️ 未設定 DISCORD Webhook URL，"
            "跳過推播。"
        )

        return

    embeds = []

    for f in taiwan_flights:

        embed = {

            "title": (
                f"🚨 彩繪機台灣起飛警報："
                f"{f['f_num']}"
            ),

            "color": 3447003,

            "fields": [

                {
                    "name": "機身註冊號",
                    "value": (
                        f"`{f['f_reg']}` "
                        f"({f['ac_code']})"
                    ),
                    "inline": True,
                },

                {
                    "name": "航線狀況",
                    "value": (
                        f"📍 **{f['route']}**"
                    ),
                    "inline": True,
                },

                {
                    "name": "預計起飛 (UTC+8)",
                    "value": (
                        f"🕒 `{f['dep_time']}`"
                    ),
                    "inline": False,
                },

                {
                    "name": "搜尋方式",
                    "value": (
                        f"`{f.get('match_type', 'UNKNOWN')}`"
                    ),
                    "inline": False,
                },

            ],

            "footer": {
                "text": (
                    "FR24 智慧航班監測系統"
                    " • "
                    f"來源：{f['source']}"
                )
            },
        }

        if f.get("image_url"):

            embed["image"] = {
                "url": f["image_url"]
            }

        embeds.append(embed)

    # Discord 一次最多 10 embeds
    for i in range(
        0,
        len(embeds),
        10
    ):

        batch = embeds[
            i:i + 10
        ]

        payload = {
            "embeds": batch
        }

        try:

            res = http_session.post(
                DISCORD_WEBHOOK_URL,
                json=payload,
                timeout=5
            )

            if res.status_code in [
                200,
                204
            ]:

                print(
                    f"✅ 成功推播第 "
                    f"{i // 10 + 1} 批 "
                    f"共 {len(batch)} 架台灣起飛航班！"
                )

            else:

                print(
                    f"❌ Discord 發送失敗，"
                    f"HTTP 狀態碼: "
                    f"{res.status_code}"
                )

        except Exception as e:

            print(
                f"❌ Discord 發送異常: {e}"
            )


# ============================================================
# 12. 第一階段：一次取得全部航班
# ============================================================

def fast_scan(
    TARGETS: list,
    fr_api_inst
):

    print(
        "\n⚡ 第一階段："
        "高速一次性掃描開始..."
    )

    start_time = time.time()

    # --------------------------------------------------------
    # 只呼叫一次 get_flights()
    # --------------------------------------------------------

    try:

        snapshot = (
            fr_api_inst.get_flights()
            or []
        )

    except Exception as e:

        print(
            f"❌ get_flights() 失敗：{e}"
        )

        snapshot = []

    print(
        f"📡 目前取得 "
        f"{len(snapshot)} 架即時航班"
    )

    # --------------------------------------------------------
    # 建立 ID map
    # --------------------------------------------------------

    flight_map_by_id = {

        str(
            getattr(
                f,
                "id",
                ""
            )
        ): f

        for f in snapshot

        if getattr(
            f,
            "id",
            ""
        )
    }

    # --------------------------------------------------------
    # 建立索引
    # --------------------------------------------------------

    index_start = time.time()

    flight_index = build_flight_index(
        snapshot
    )

    print(
        f"🧠 航班索引建立完成 "
        f"({time.time() - index_start:.2f} 秒)"
    )

    # --------------------------------------------------------
    # 直接查詢 targets
    # --------------------------------------------------------

    matched_dict = {}

    unmatched_targets = []

    for target in TARGETS:

        flight, match_type = (
            find_target_in_index(
                target,
                flight_index
            )
        )

        if not flight:

            unmatched_targets.append(
                target
            )

            continue

        # ----------------------------------------------------
        # 只有真正匹配的航班才取得詳細資料
        # ----------------------------------------------------

        details = fetch_direct_clickhandler(
            fr_api_inst,
            flight
        )

        if not details:

            unmatched_targets.append(
                target
            )

            continue

        result = build_result(
            target,
            flight,
            details,
            "📡 FR24 直播廣播",
            match_type
        )

        if result:

            matched_dict[
                target
            ] = result

            print(
                f"  └─ 🟢 {target}"
                f" -> "
                f"{result['f_num']} "
                f"({result['f_reg']}) "
                f"[{match_type}]"
            )

        else:

            unmatched_targets.append(
                target
            )

    elapsed = (
        time.time()
        - start_time
    )

    print(
        f"\n⚡ 第一階段完成："
        f"{elapsed:.2f} 秒"
    )

    print(
        f"   🟢 找到："
        f"{len(matched_dict)}"
    )

    print(
        f"   ❓ 未找到："
        f"{len(unmatched_targets)}"
    )

    return (
        matched_dict,
        unmatched_targets,
        flight_map_by_id
    )


# ============================================================
# 13. 第二階段：只補查未找到的目標
# ============================================================

def deep_scan_unmatched(
    unmatched_targets: list,
    flight_map_by_id: dict,
    fr_api_inst
):

    if not unmatched_targets:

        return {}

    print(
        "\n🔍 第二階段："
        f"開始補查 {len(unmatched_targets)} 架未找到目標..."
    )

    start_time = time.time()

    results = {}

    # --------------------------------------------------------
    # 不需要 15 / 10 輪
    #
    # 只對真正未找到的目標進行 Web API
    # --------------------------------------------------------

    max_workers = min(
        6,
        max(
            1,
            len(unmatched_targets)
        )
    )

    print(
        f"🚀 開啟 {max_workers} 個補查線程"
    )

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        future_to_target = {

            executor.submit(
                web_search_target,
                target,
                flight_map_by_id,
                fr_api_inst
            ): target

            for target in unmatched_targets
        }

        for future in as_completed(
            future_to_target
        ):

            target = (
                future_to_target[
                    future
                ]
            )

            try:

                result = (
                    future.result()
                )

                if result:

                    results[
                        target
                    ] = result

                    print(
                        f"  └─ 🟢 [補查成功] "
                        f"{target} -> "
                        f"{result['f_num']} "
                        f"({result['route']})"
                    )

                else:

                    print(
                        f"  └─ ⚪ [無結果] "
                        f"{target}"
                    )

            except Exception as e:

                print(
                    f"  └─ ❌ [補查錯誤] "
                    f"{target}: {e}"
                )

    elapsed = (
        time.time()
        - start_time
    )

    print(
        f"\n🔍 第二階段完成："
        f"{elapsed:.2f} 秒"
    )

    print(
        f"   🟢 補查成功："
        f"{len(results)}"
    )

    print(
        f"   ❌ 仍未找到："
        f"{len(unmatched_targets) - len(results)}"
    )

    return results


# ============================================================
# 14. 最終篩選
# ============================================================

def filter_taiwan_departures(
    matched_dict: dict,
    minutes_ahead: int = 10
):

    # --------------------------------------------------------
    # 修正原程式時間邏輯
    #
    # 原本：
    # now - 10分鐘 -> now
    #
    # 現在：
    # now -> now + 10分鐘
    # --------------------------------------------------------

    now_ts = int(
        time.time()
    )

    limit_ts = (
        now_ts
        + minutes_ahead * 60
    )

    taiwan_departures = []

    for f in matched_dict.values():

        if not f.get(
            "is_taiwan_origin"
        ):
            continue

        dep_ts = f.get(
            "dep_ts"
        )

        if not dep_ts:
            continue

        try:

            dep_ts = int(
                dep_ts
            )

        except Exception:

            continue

        if (
            now_ts
            <= dep_ts
            < limit_ts
        ):

            taiwan_departures.append(
                f
            )

    # 起飛時間排序
    taiwan_departures.sort(
        key=lambda x: (
            x.get(
                "dep_ts"
            )
            or 0
        )
    )

    return taiwan_departures


# ============================================================
# 15. 主程式
# ============================================================

def main():

    program_start = time.time()

    print(
        "=" * 65
    )

    print(
        "✈️ FR24 高速智慧航班監測系統"
    )

    print(
        "=" * 65
    )

    # --------------------------------------------------------
    # 沒有 targets
    # --------------------------------------------------------

    if not TARGETS:

        print(
            "🛑 沒有偵測到任何監控目標，"
            "程式結束。"
        )

        return

    print(
        f"🎯 監控目標："
        f"{len(TARGETS)} 架"
    )

    # --------------------------------------------------------
    # 建立 FR24
    # --------------------------------------------------------

    try:

        fr_api_inst = (
            FlightRadar24API()
        )

    except Exception as e:

        print(
            f"❌ 無法建立 FlightRadar24API："
            f"{e}"
        )

        return

    # ========================================================
    # 第一階段
    # ========================================================

    (
        matched_dict,
        unmatched_targets,
        flight_map_by_id
    ) = fast_scan(
        TARGETS,
        fr_api_inst
    )

    # ========================================================
    # 第二階段
    # ========================================================

    if unmatched_targets:

        deep_results = (
            deep_scan_unmatched(
                unmatched_targets,
                flight_map_by_id,
                fr_api_inst
            )
        )

        matched_dict.update(
            deep_results
        )

    # ========================================================
    # 最終篩選
    # ========================================================

    taiwan_departures = (
        filter_taiwan_departures(
            matched_dict,
            minutes_ahead=10
        )
    )

    # ========================================================
    # 統計
    # ========================================================

    final_unmatched = (
        len(TARGETS)
        - len(matched_dict)
    )

    total_elapsed = (
        time.time()
        - program_start
    )

    print(
        "\n" + "=" * 65
    )

    print(
        "📊 掃描結果總結"
    )

    print(
        "=" * 65
    )

    print(
        f" • 監控目標數："
        f"{len(TARGETS)} 架"
    )

    print(
        f" • 成功定位："
        f"{len(matched_dict)} 架"
    )

    print(
        f" • ❌ 未找到："
        f"{final_unmatched} 架"
    )

    print(
        f" • 🛫 未來 10 分鐘內"
        f"自台灣起飛："
        f"{len(taiwan_departures)} 架"
    )

    print(
        f" • ⏱️ 本次總耗時："
        f"{total_elapsed:.2f} 秒"
    )

    print(
        "=" * 65
    )

    # ========================================================
    # 顯示台灣起飛航班
    # ========================================================

    if taiwan_departures:

        print(
            "\n🚨 發現即將自台灣起飛的目標："
        )

        for f in taiwan_departures:

            print(
                f"  ✈️ {f['f_num']}"
                f" | {f['f_reg']}"
                f" | {f['ac_code']}"
                f" | {f['route']}"
                f" | {f['dep_time']}"
            )

        # ----------------------------------------------------
        # Discord
        # ----------------------------------------------------

        send_discord_webhook(
            taiwan_departures
        )

    else:

        print(
            "\nℹ️ 目前沒有目標班機"
            "將在未來 10 分鐘內"
            "自台灣起飛。"
        )

    # ========================================================
    # 未找到清單
    # ========================================================

    if unmatched_targets:

        actually_unmatched = [
            t
            for t in TARGETS
            if t not in matched_dict
        ]

        if actually_unmatched:

            print(
                "\n❌ 以下目標目前沒有取得資料："
            )

            for target in (
                actually_unmatched
            ):

                print(
                    f"   - {target}"
                )

    print(
        "\n✅ 程式執行完成。"
    )


# ============================================================
# 16. Entry Point
# ============================================================

if __name__ == "__main__":
    main()
