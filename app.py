# --- 主程式執行區塊 ---
if scan_button or "first_run" not in st.session_state:
    st.session_state["first_run"] = True

    with st.spinner("正從 FlightRadar24 抓取全球即時空域數據並進行多線程比對..."):
        try:
            # 1. 先抓取全球廣播資料 (一次性請求)
            all_active_flights = fr_api.get_flights()
            matched_results = []

            # 2. worker 任務函數
            def worker(t):
                return search_single_target(t, all_active_flights)

            progress_bar = st.progress(0)
            completed_count = 0

            # ⚡ 這裡改成 max_workers=5（每次平行處理 5 個目標）
            with ThreadPoolExecutor(max_workers=5) as executor:
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
                # 3. 升級版 PyDeck 互動地圖
                st.subheader(
                    "🗺️ 飛機即時位置雷達地圖 (將滑鼠移至點上可查看詳情)"
                )

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
