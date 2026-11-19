"""Cost estimate for a Modal run, from the published per-second prices.

Prices from https://modal.com/pricing, read 2026-11-18. Modal bills GPU, CPU
and memory separately, per second, from container start to stop (idle time
before scale-down included). Check the page before relying on these numbers;
your Modal dashboard is the source of truth for what you are charged.
"""
from __future__ import annotations

GPU_USD_PER_S = {
    "T4": 0.000164,
    "L4": 0.000222,
    "A10": 0.000306,  # Modal's "A10" is an NVIDIA A10G
    "L40S": 0.000542,
}
CPU_CORE_USD_PER_S = 0.0000131  # one physical core = 2 vCPU
MEM_GIB_USD_PER_S = 0.00000222

# The container shape the Modal app requests (modal_app.py).
CPU_CORES = 4.0
MEMORY_GIB = 16.0
CPU_FALLBACK_CORES = 8.0


def gpu_key(name: str | None) -> str | None:
    """'NVIDIA L4' -> 'L4', 'NVIDIA A10G' -> 'A10'; None for CPU or unknown."""
    n = (name or "").upper()
    for key in ("L40S", "L4", "A10", "T4"):
        if key in n:
            return key
    return None


def usd_per_hour(gpu: str | None, cores: float = CPU_CORES, mem_gib: float = MEMORY_GIB) -> float:
    per_s = GPU_USD_PER_S.get(gpu_key(gpu) or "", 0.0)
    per_s += cores * CPU_CORE_USD_PER_S + mem_gib * MEM_GIB_USD_PER_S
    return per_s * 3600


def estimate_usd(gpu: str | None, billed_s: float, cores: float = CPU_CORES,
                 mem_gib: float = MEMORY_GIB) -> float:
    return round(usd_per_hour(gpu, cores, mem_gib) * billed_s / 3600, 4)
