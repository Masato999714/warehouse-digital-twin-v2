"""
倉庫デジタルツイン・シミュレーター：工程網モデル

6工程（入荷・荷役 → 保管 → ピッキング → 仕分け → 梱包 → 搬送・出荷）を
直列に接続した待ち行列網として、離散事象シミュレーション（DES）で再現する。

【SimPyについて】
本モジュールは、実際の SimPy ライブラリ（https://simpy.readthedocs.io/）を
第一候補として使用する。SimPy がインストールされている環境では、そのまま
simpy.Environment / simpy.Resource を利用する。

SimPy がインストールされていない環境（サンドボックス等）でも動作確認できるよう、
その場合は同一のAPI（request() / release() の使い方）を持つ自作の軽量エンジン
（minides.py）に自動的にフォールバックする。

  from warehouse_sim import USING_REAL_SIMPY
  print(USING_REAL_SIMPY)  # True: 本物のSimPyを使用中 / False: 自作エンジンを使用中
"""

import math
import random
import statistics
from dataclasses import dataclass, field

try:
    import simpy
    Environment = simpy.Environment
    Resource = simpy.Resource
    USING_REAL_SIMPY = True
except ImportError:
    from minides import Environment, Resource
    USING_REAL_SIMPY = False


STAGE_NAMES = ["入荷・荷役", "保管", "ピッキング", "仕分け", "梱包", "搬送・出荷"]

DEFAULT_STAGE_PARAMS = {
    "入荷・荷役":   {"base_service_min": 3.4, "capacity": 7},
    "保管":         {"base_service_min": 2.6, "capacity": 6},
    "ピッキング":   {"base_service_min": 3.6, "capacity": 8},
    "仕分け":       {"base_service_min": 2.8, "capacity": 6},
    "梱包":         {"base_service_min": 3.0, "capacity": 6},
    "搬送・出荷":   {"base_service_min": 2.6, "capacity": 6},
}


def gamma_sample(rng, mean, cv):
    """平均meanのガンマ分布から1サンプルを生成する。cv=変動係数（ばらつきの大きさ）。"""
    if mean <= 0:
        return 0.0
    if cv <= 0:
        return mean
    shape = 1.0 / (cv ** 2)
    scale = mean / shape
    return rng.gammavariate(shape, scale)


@dataclass
class StageRecord:
    """各工程の状態（WIP・稼働状況・待ち行列長）を時系列で記録するための入れ物。"""
    name: str
    capacity: int
    wip_samples: list = field(default_factory=list)
    busy_samples: list = field(default_factory=list)
    qlen_samples: list = field(default_factory=list)


def _resource_in_use(res):
    """SimPy本体／自作エンジンのどちらでも、使用中の数を取得できるようにするヘルパー。"""
    return res.count


def _resource_queue_len(res):
    """SimPy本体／自作エンジンのどちらでも、待ち行列長を取得できるようにするヘルパー。"""
    return len(res.queue)


def run_simulation(
    arrival_rate_per_hour=80.0,
    sim_hours=8.0,
    wave_amplitude=0.3,
    seed=7,
    stage_overrides=None,
    sample_interval_min=5.0,
):
    """
    倉庫6工程の待ち行列網シミュレーションを1回実行する。

    Parameters
    ----------
    arrival_rate_per_hour : float
        1時間あたりの平均到着件数。
    sim_hours : float
        シミュレーションする稼働時間（時間）。
    wave_amplitude : float
        時間帯波動の強さ（0.0〜0.8程度）。値が大きいほどピーク帯に物量が集中する。
    seed : int
        乱数シード。同じ値なら同じ到着・処理パターンが再現される。
    stage_overrides : dict or None
        工程名をキーとし、{"capacity":, "base_service_min":, "capacity_multiplier":, "service_cv":}
        を値とする辞書。指定した工程・パラメータのみデフォルト値を上書きする。
    sample_interval_min : float
        WIP等をサンプリングする間隔（分）。

    Returns
    -------
    dict : 到着数・完了数・平均リードタイム・スループット・工程別サマリーを含む結果辞書。
    """
    rng = random.Random(seed)
    env = Environment()
    sim_time_min = sim_hours * 60.0
    stage_overrides = stage_overrides or {}

    stages = {}
    resources = {}
    records = {}

    for name in STAGE_NAMES:
        params = dict(DEFAULT_STAGE_PARAMS[name])
        override = stage_overrides.get(name, {})
        capacity = int(override.get("capacity", params["capacity"]))
        base_service = float(override.get("base_service_min", params["base_service_min"]))
        cap_mult = float(override.get("capacity_multiplier", 1.0))
        service_cv = float(override.get("service_cv", 0.35))
        effective_service_mean = base_service / cap_mult

        stages[name] = {
            "capacity": capacity,
            "service_mean": effective_service_mean,
            "service_cv": service_cv,
        }
        resources[name] = Resource(env, capacity=capacity)
        records[name] = StageRecord(name=name, capacity=capacity)

    lead_times = []
    completed_count = [0]
    arrived_count = [0]

    def item_process(item_id, arrive_t):
        """1個のアイテム（荷物）が、6工程を順番に通過していく処理。
        SimPy本来の書き方（request()でyieldし、releaseは同期的に呼ぶ）に統一している。
        """
        for stage_name in STAGE_NAMES:
            res = resources[stage_name]
            p = stages[stage_name]
            req = res.request()
            yield req
            service_time = gamma_sample(rng, p["service_mean"], p["service_cv"])
            yield env.timeout(service_time)
            res.release(req)
        lead_times.append(env.now - arrive_t)
        completed_count[0] += 1

    def arrival_process():
        """時間帯波動を伴うポアソン到着過程。"""
        item_id = 0
        while env.now < sim_time_min:
            phase = (env.now / max(sim_time_min, 1e-9)) * math.pi
            factor = max(1.0 + wave_amplitude * math.sin(phase), 0.05)
            rate_per_min = (arrival_rate_per_hour * factor) / 60.0
            iat = rng.expovariate(rate_per_min) if rate_per_min > 0 else 1.0
            yield env.timeout(iat)
            if env.now >= sim_time_min:
                break
            item_id += 1
            arrived_count[0] += 1
            env.process(item_process(item_id, env.now))

    def sampler_process():
        """一定間隔ごとに各工程のWIP・稼働数・待ち行列長を記録する。"""
        while env.now < sim_time_min:
            t = env.now
            for name in STAGE_NAMES:
                res = resources[name]
                in_use = _resource_in_use(res)
                qlen = _resource_queue_len(res)
                records[name].wip_samples.append((t, in_use + qlen))
                records[name].busy_samples.append((t, in_use))
                records[name].qlen_samples.append((t, qlen))
            yield env.timeout(sample_interval_min)

    env.process(arrival_process())
    env.process(sampler_process())
    env.run(until=sim_time_min)

    stage_summary = {}
    for name in STAGE_NAMES:
        rec = records[name]
        wip_vals = [v for _, v in rec.wip_samples]
        busy_vals = [v for _, v in rec.busy_samples]
        qlen_vals = [v for _, v in rec.qlen_samples]
        capacity = rec.capacity
        avg_util = (sum(busy_vals) / len(busy_vals) / capacity) if busy_vals and capacity > 0 else 0.0
        stage_summary[name] = {
            "capacity": capacity,
            "avg_wip": statistics.mean(wip_vals) if wip_vals else 0.0,
            "avg_utilization": avg_util,
            "avg_qlen": statistics.mean(qlen_vals) if qlen_vals else 0.0,
            "max_wip": max(wip_vals) if wip_vals else 0.0,
            "timeseries": {
                "t": [t for t, _ in rec.wip_samples],
                "wip": wip_vals,
                "utilization": [(b / capacity if capacity > 0 else 0.0) for b in busy_vals],
                "qlen": qlen_vals,
            },
        }

    return {
        "arrived": arrived_count[0],
        "completed": completed_count[0],
        "avg_lead_time_min": statistics.mean(lead_times) if lead_times else 0.0,
        "p90_lead_time_min": (sorted(lead_times)[int(len(lead_times) * 0.9)] if lead_times else 0.0),
        "throughput_per_hour": completed_count[0] / sim_hours if sim_hours > 0 else 0.0,
        "stage_summary": stage_summary,
        "engine": "simpy" if USING_REAL_SIMPY else "minides(fallback)",
        "params": {
            "arrival_rate_per_hour": arrival_rate_per_hour,
            "sim_hours": sim_hours,
            "wave_amplitude": wave_amplitude,
            "seed": seed,
        },
    }
