import math
import random
import statistics
from dataclasses import dataclass, field

from minides import Environment, Resource

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
    if mean <= 0:
        return 0.0
    if cv <= 0:
        return mean
    shape = 1.0 / (cv ** 2)
    scale = mean / shape
    return rng.gammavariate(shape, scale)


@dataclass
class StageRecord:
    name: str
    capacity: int
    wip_samples: list = field(default_factory=list)
    busy_samples: list = field(default_factory=list)
    qlen_samples: list = field(default_factory=list)


def run_simulation(
    arrival_rate_per_hour=80.0,
    sim_hours=8.0,
    wave_amplitude=0.3,
    seed=7,
    stage_overrides=None,
    sample_interval_min=5.0,
):
    rng = random.Random(seed)
    env = Environment()
    sim_time_min = sim_hours * 60.0
    stage_overrides = stage_overrides or {}

    stages, resources, records = {}, {}, {}
    for name in STAGE_NAMES:
        params = dict(DEFAULT_STAGE_PARAMS[name])
        override = stage_overrides.get(name, {})
        capacity = int(override.get("capacity", params["capacity"]))
        base_service = float(override.get("base_service_min", params["base_service_min"]))
        cap_mult = float(override.get("capacity_multiplier", 1.0))
        service_cv = float(override.get("service_cv", 0.35))
        effective_service_mean = base_service / cap_mult

        stages[name] = {"capacity": capacity, "service_mean": effective_service_mean, "service_cv": service_cv}
        resources[name] = Resource(env, capacity=capacity)
        records[name] = StageRecord(name=name, capacity=capacity)

    lead_times = []
    completed_count = [0]
    arrived_count = [0]

    def item_process(item_id, arrive_t):
        for stage_name in STAGE_NAMES:
            res = resources[stage_name]
            p = stages[stage_name]
            token = yield res.request()
            service_time = gamma_sample(rng, p["service_mean"], p["service_cv"])
            yield env.timeout(service_time)
            yield res.release(token)
        lead_times.append(env.now - arrive_t)
        completed_count[0] += 1

    def arrival_process():
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
        while env.now < sim_time_min:
            t = env.now
            for name in STAGE_NAMES:
                res = resources[name]
                records[name].wip_samples.append((t, res.in_use + res.queue_len))
                records[name].busy_samples.append((t, res.in_use))
                records[name].qlen_samples.append((t, res.queue_len))
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
            }
        }

    return {
        "arrived": arrived_count[0],
        "completed": completed_count[0],
        "avg_lead_time_min": statistics.mean(lead_times) if lead_times else 0.0,
        "p90_lead_time_min": (sorted(lead_times)[int(len(lead_times) * 0.9)] if lead_times else 0.0),
        "throughput_per_hour": completed_count[0] / sim_hours if sim_hours > 0 else 0.0,
        "stage_summary": stage_summary,
        "params": {
            "arrival_rate_per_hour": arrival_rate_per_hour,
            "sim_hours": sim_hours,
            "wave_amplitude": wave_amplitude,
            "seed": seed,
        }
    }