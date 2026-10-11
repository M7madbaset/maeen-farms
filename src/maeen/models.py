from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Reading:
    """Raw observation with provenance. `simulated` is never dropped."""
    sensor_id: str
    value: float
    unit: str
    ts: datetime
    quality: str = "good"
    source: str = "simulator"
    simulated: bool = True

    def to_evidence(self) -> dict:
        return {"sensor_id": self.sensor_id, "value": self.value, "unit": self.unit,
                "ts": self.ts.isoformat(), "quality": self.quality,
                "source": self.source, "simulated": self.simulated}


@dataclass
class DeviceCommand:
    command_id: str
    idempotency_key: str
    device_id: str
    action: str                 # "run_valve_timed"
    minutes: float
    expected_flow_m3h: float
    planned_volume_m3: float
    expires_at: datetime
    workflow_id: str
    requested_by: str


@dataclass
class Approval:
    approval_id: str
    workflow_id: str
    approver_user_id: str
    created_at: datetime
    expires_at: datetime
    fingerprints: tuple          # exactly the commands the human approved


@dataclass
class CommandResult:
    status: str                 # verified | advisory_recorded | denied | failed_verification | ...
    verified: bool
    simulated: bool
    detail: dict
