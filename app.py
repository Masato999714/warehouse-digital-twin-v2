import json
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import streamlit as st

# 日本語フォント設定（グラフ内の文字化け対策）
matplotlib.rcParams["font.family"] = ["Noto Sans CJK JP", "IPAexGothic", "Meiryo", "Yu Gothic", "MS Gothic", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

from warehouse_sim import run_simulation, STAGE_NAMES
from kpi_roi import build_stage_comparison_df, detect_bottlenecks, build_overall_kpi, estimate_roi

st.set_page_config(page_title="倉庫デジタルツイン・シミュレーター", layout="wide")

with open("equipment_master.json", encoding="utf-8") as f:
    EQUIPMENT_MASTER = json.load(f)

st.title("倉庫デジタルツイン・シミュレーター（プロトタイプ）")
st.caption("待ち行列網モデル＋離散イベントシミュレーションにより、機器導入が全工程に与える影響を可視化します。")

# --- サイドバー：共通条件（倉庫規模） ---
st.sidebar.header("倉庫規模・稼働条件")
daily_volume = st.sidebar.number_input(
    "想定日次処理件数（件/日）", min_value=50, max_value=100000, value=600, step=50,
    help="1日に倉庫で処理する総件数（入荷〜出荷までを通過するアイテム数）の目安です。"
)
sim_hours = st.sidebar.slider("1日の稼働時間（時間）", 2, 24, 8, step=1)
wave_amp = st.sidebar.slider("時間帯波動の強さ", 0.0, 0.8, 0.3, step=0.05,
                              help="値が大きいほど、ピーク時間帯に物量が集中します。")
seed = st.sidebar.number_input("乱数シード", value=7, step=1)

arrival_rate = daily_volume / sim_hours
st.sidebar.metric("到着率（自動算出）", f"{arrival_rate:.1f} 件/時")

st.sidebar.header("ROI計算用条件")
labor_cost = st.sidebar.number_input("人件費（円/人時）", value=2500, step=100)
days_per_year = st.sidebar.number_input("年間稼働日数（日）", min_value=1, max_value=366, value=250, step=1)

# --- メイン画面：工程別設定 ---
st.header("工程別 容量・導入機器・投資額の設定")
st.caption("各工程の容量（人・台数）と導入機器を選択してください。投資額は機器マスタの参考値が自動入力されますが、実際の見積金額に書き換えられます。")

stage_overrides = {}
default_caps = {"入荷・荷役": 7, "保管": 6, "ピッキング": 8, "仕分け": 6, "梱包": 6, "搬送・出荷": 6}

cols = st.columns(3)
selected_equipment = {}
capacities = {}
capex_inputs = {}  # 万円単位

for i, name in enumerate(STAGE_NAMES):
    with cols[i % 3]:
        st.subheader(name)
        cap = st.number_input("容量（人・台）", min_value=1, max_value=200,
                               value=default_caps[name], key=f"cap_{name}")
        options = [e["name"] for e in EQUIPMENT_MASTER[name]]
        choice = st.selectbox("導入機器", options, key=f"eq_{name}")
        eq_info = next(e for e in EQUIPMENT_MASTER[name] if e["name"] == choice)

        default_capex_man = eq_info["capex_oku"] * 10000  # 億円 -> 万円
        capex_man = st.number_input(
            "投資額（万円）", min_value=0, max_value=1000000,
            value=int(default_capex_man), step=100, key=f"capex_{name}",
            help="機器マスタの参考値が初期値として入っています。実際の見積金額に書き換えてください。"
        )

        with st.expander("機器の特徴・注意点"):
            st.write(eq_info["note"])
            st.write(f"処理速度向上率: x{eq_info['capacity_multiplier']}")
            st.write(f"投資額（機器マスタ参考値）: {eq_info['capex_oku']}億円")

        capacities[name] = cap
        selected_equipment[name] = eq_info
        capex_inputs[name] = capex_man
        stage_overrides[name] = {
            "capacity": cap,
            "capacity_multiplier": eq_info["capacity_multiplier"],
            "service_cv": eq_info["service_cv"],
        }

run_btn = st.button("シミュレーション実行", type="primary")

# --- ボタンが押されたら、計算結果を session_state に保存する ---
if run_btn:
    before_overrides = {}
    for name in STAGE_NAMES:
        base_eq = EQUIPMENT_MASTER[name][0]  # マスタの先頭 = 現状（人手）
        before_overrides[name] = {
            "capacity": capacities[name],
            "capacity_multiplier": base_eq["capacity_multiplier"],
            "service_cv": base_eq["service_cv"],
        }

    res_before = run_simulation(
        arrival_rate_per_hour=arrival_rate, sim_hours=sim_hours,
        wave_amplitude=wave_amp, seed=seed, stage_overrides=before_overrides,
    )
    res_after = run_simulation(
        arrival_rate_per_hour=arrival_rate, sim_hours=sim_hours,
        wave_amplitude=wave_amp, seed=seed, stage_overrides=stage_overrides,
    )

    # 結果一式をセッションに保存（これ以降、ページ再実行しても消えない）
    st.session_state["res_before"] = res_before
    st.session_state["res_after"] = res_after
    st.session_state["selected_equipment"] = selected_equipment
    st.session_state["capex_inputs"] = capex_inputs
    st.session_state["labor_cost"] = labor_cost
    st.session_state["sim_hours"] = sim_hours
    st.session_state["days_per_year"] = days_per_year
    st.session_state["has_result"] = True

# --- 保存された結果があれば、常に表示する（ボタンを押した直後でなくても表示） ---
if st.session_state.get("has_result", False):
    res_before = st.session_state["res_before"]
    res_after = st.session_state["res_after"]
    selected_equipment = st.session_state["selected_equipment"]
    capex_inputs = st.session_state["capex_inputs"]
    labor_cost_saved = st.session_state["labor_cost"]
    sim_hours_saved = st.session_state["sim_hours"]
    days_per_year_saved = st.session_state["days_per_year"]

    st.header("結果：全体KPI比較")
    kpi = build_overall_kpi(res_before, res_after)
    c1, c2, c3 = st.columns(3)
    completed_after = kpi["completed_after"]
    completed_diff_pct = kpi["completed_diff_pct"]
    completed_before = kpi["completed_before"]
    lead_time_after = kpi["lead_time_after"]
    lead_time_diff_pct = kpi["lead_time_diff_pct"]
    lead_time_before = kpi["lead_time_before"]
    throughput_after = kpi["throughput_after"]
    throughput_diff_pct = kpi["throughput_diff_pct"]
    throughput_before = kpi["throughput_before"]

    c1.metric("完了数", f"{completed_after}件",
              f"{completed_diff_pct:+.1f}% (Before {completed_before}件)")
    c2.metric("平均リードタイム", f"{lead_time_after:.1f}分",
              f"{lead_time_diff_pct:+.1f}% (Before {lead_time_before:.1f}分)")
    c3.metric("スループット", f"{throughput_after:.1f}件/時",
              f"{throughput_diff_pct:+.1f}% (Before {throughput_before:.1f}件/時)")

    st.header("結果：工程別 WIP・稼働率比較")
    df = build_stage_comparison_df(res_before, res_after, STAGE_NAMES)

    def color_diff(val):
        if isinstance(val, (int, float)):
            if val > 0:
                return "color: red"
            elif val < 0:
                return "color: green"
        return ""

    st.dataframe(
        df.style.map(color_diff, subset=["WIP変化率(%)", "稼働率変化(pt)"]),
        use_container_width=True,
    )

    bottlenecks_after = detect_bottlenecks(res_after, STAGE_NAMES)
    if bottlenecks_after:
        names = "、".join([f"{n}（稼働率{u:.0%}）" for n, u in bottlenecks_after])
        st.warning(f"⚠️ ボトルネック候補（稼働率80%以上）: {names}")
    else:
        st.success("導入後、稼働率80%以上の工程はありません。")

    st.header("結果：工程別 WIP 時系列（Before/After）")
    fig, axes = plt.subplots(2, 3, figsize=(16, 8))
    for i, name in enumerate(STAGE_NAMES):
        ax = axes[i // 3][i % 3]
        tb = res_before["stage_summary"][name]["timeseries"]
        ta = res_after["stage_summary"][name]["timeseries"]
        ax.plot(tb["t"], tb["wip"], label="Before", alpha=0.7)
        ax.plot(ta["t"], ta["wip"], label="After", alpha=0.7)
        ax.set_title(name)
        ax.set_xlabel("時間(分)")
        ax.set_ylabel("WIP")
        ax.legend(fontsize=8)
    plt.tight_layout()
    st.pyplot(fig)

    st.header("簡易ROI推定")
    roi_stage = st.selectbox("ROI算出対象工程", STAGE_NAMES, key="roi_stage")
    eq = selected_equipment[roi_stage]
    capex_man = capex_inputs[roi_stage]
    capex_yen = capex_man * 10000  # 万円 -> 円

    roi = estimate_roi(
        capex_yen, roi_stage, res_before, res_after,
        labor_cost_per_person_hour=labor_cost_saved,
        hours_per_day=sim_hours_saved,
        days_per_year=days_per_year_saved,
    )
    r1, r2, r3 = st.columns(3)
    annual_saving_man = roi["annual_saving_yen"] / 1e4
    r1.metric("投資額（入力値）", f"{capex_man:,.0f}万円")
    r2.metric("年間人件費削減額（推定）", f"{annual_saving_man:,.0f}万円")
    if roi["payback_years"] == float("inf"):
        r3.metric("投資回収期間", "算出不可（削減効果なし）")
    else:
        payback_years = roi["payback_years"]
        r3.metric("投資回収期間（推定）", f"{payback_years:.1f}年")
    st.caption(
        "※ ROIは「稼働率の低下分 × 工程容量」を浮いた人員相当とみなし、"
        "人件費レート・年間稼働時間から年間削減額を推定する簡易ロジックです。"
        "投資額は機器マスタの参考値を初期値としていますが、実際の見積金額に書き換えて計算してください。"
        "実際の投資判断には現場データでのキャリブレーションが必要です。"
    )
else:
    st.info("上記で倉庫規模・工程別の条件を設定し、「シミュレーション実行」ボタンを押してください。")
