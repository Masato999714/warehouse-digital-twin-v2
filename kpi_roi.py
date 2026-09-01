import pandas as pd

BOTTLENECK_UTIL_THRESHOLD = 0.80


def build_stage_comparison_df(res_before, res_after, stage_names):
    rows = []
    for name in stage_names:
        b = res_before["stage_summary"][name]
        a = res_after["stage_summary"][name]
        wip_diff_pct = (a["avg_wip"] - b["avg_wip"]) / b["avg_wip"] * 100 if b["avg_wip"] else 0.0
        util_diff_pt = (a["avg_utilization"] - b["avg_utilization"]) * 100
        rows.append({
            "工程": name,
            "容量(Before)": b["capacity"], "容量(After)": a["capacity"],
            "WIP(Before)": round(b["avg_wip"], 2), "WIP(After)": round(a["avg_wip"], 2),
            "WIP変化率(%)": round(wip_diff_pct, 1),
            "稼働率(Before)": round(b["avg_utilization"] * 100, 1),
            "稼働率(After)": round(a["avg_utilization"] * 100, 1),
            "稼働率変化(pt)": round(util_diff_pt, 1),
            "待ち行列長(Before)": round(b["avg_qlen"], 2),
            "待ち行列長(After)": round(a["avg_qlen"], 2),
        })
    return pd.DataFrame(rows)


def detect_bottlenecks(res, stage_names, threshold=BOTTLENECK_UTIL_THRESHOLD):
    return [(name, res["stage_summary"][name]["avg_utilization"])
            for name in stage_names if res["stage_summary"][name]["avg_utilization"] >= threshold]


def build_overall_kpi(res_before, res_after):
    def pct(b, a):
        return (a - b) / b * 100 if b else 0.0
    return {
        "completed_before": res_before["completed"], "completed_after": res_after["completed"],
        "completed_diff_pct": pct(res_before["completed"], res_after["completed"]),
        "lead_time_before": res_before["avg_lead_time_min"], "lead_time_after": res_after["avg_lead_time_min"],
        "lead_time_diff_pct": pct(res_before["avg_lead_time_min"], res_after["avg_lead_time_min"]),
        "throughput_before": res_before["throughput_per_hour"], "throughput_after": res_after["throughput_per_hour"],
        "throughput_diff_pct": pct(res_before["throughput_per_hour"], res_after["throughput_per_hour"]),
    }


def estimate_roi(capex_yen, stage_name, res_before, res_after,
                  labor_cost_per_person_hour=2500, hours_per_day=8, days_per_year=250):
    """
    capex_yen: 投資額（円）。UI側で万円入力→円に変換した値をそのまま渡す。
    ここでは追加の単位変換（億円化など）は一切行わない。
    """
    b = res_before["stage_summary"][stage_name]
    a = res_after["stage_summary"][stage_name]
    util_drop = max(b["avg_utilization"] - a["avg_utilization"], 0.0)
    freed_capacity_equiv = util_drop * b["capacity"]
    annual_hours = hours_per_day * days_per_year
    annual_saving_yen = freed_capacity_equiv * labor_cost_per_person_hour * annual_hours
    payback_years = capex_yen / annual_saving_yen if annual_saving_yen > 0 else float("inf")
    return {
        "freed_capacity_equiv": freed_capacity_equiv,
        "annual_saving_yen": annual_saving_yen,
        "capex_yen": capex_yen,
        "payback_years": payback_years,
    }