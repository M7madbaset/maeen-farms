"""Deterministic water-demand and allocation maths. No LLM involved.
All parameters come from FarmConfig and are placeholders until agronomist-validated."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class BlockDemand:
    block_id: str
    kc: float
    etc_mm: float
    net_m3: float
    leaching_fraction: float
    gross_m3: float


def leaching_requirement(ecw: float, ece_threshold: float) -> float:
    denom = 5.0 * ece_threshold - ecw
    if ecw < 0 or ece_threshold <= 0 or denom <= 0:
        raise ValueError("invalid salinity inputs for leaching requirement")
    return ecw / denom


def block_demand(block, eto_mm: float, cfg, effective_rain_mm: float = 0.0) -> BlockDemand:
    if block.stage not in cfg.kc_by_stage:
        raise ValueError(f"no Kc configured for stage '{block.stage}' (block {block.block_id})")
    if eto_mm < 0 or not (0 < cfg.irrigation_efficiency <= 1):
        raise ValueError("invalid ETo or irrigation efficiency")
    kc = cfg.kc_by_stage[block.stage]
    etc = max(0.0, eto_mm * kc - effective_rain_mm)
    net = etc / 1000.0 * block.area_m2
    lr = leaching_requirement(cfg.ecw_dsm, cfg.ece_threshold_dsm) if cfg.apply_leaching else 0.0
    gross = net / cfg.irrigation_efficiency / (1.0 - lr)
    return BlockDemand(block.block_id, kc, etc, net, lr, gross)


def allocate(demands: dict, blocks, available_m3: float):
    """Pass 1: serve each block's floor in priority order. Pass 2: top up in priority
    order. Returns (allocation, unallocated_remainder)."""
    if available_m3 < 0:
        raise ValueError("available water cannot be negative")
    order = sorted(blocks, key=lambda b: b.priority)
    alloc = {b.block_id: 0.0 for b in blocks}
    rem = available_m3
    for b in order:
        g = min(b.min_fraction * demands[b.block_id], rem)
        alloc[b.block_id] += g
        rem -= g
    for b in order:
        g = min(demands[b.block_id] - alloc[b.block_id], rem)
        alloc[b.block_id] += g
        rem -= g
    return alloc, rem
