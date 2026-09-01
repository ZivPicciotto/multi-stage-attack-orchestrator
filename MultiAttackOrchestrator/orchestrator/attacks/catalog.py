"""A small, deliberately overlapping catalog of sample attacks.

Three attacks with different real-world profiles, so attack selection actually has work to do:

- BOOTROM_CHAIN: a hardware bug (checkm8-style). Unpatchable, cheap to retry — a failed attempt
  doesn't cost anything, so it tolerates several full-chain restarts.
- KERNEL_CHAIN: a software bug. Higher iOS/battery floor, and a failed attempt can panic the
  device (the device's call at runtime, not declared here — see StageResult.crashed).
- PASSCODE_CHAIN: brute force. Low success probability and a failed attempt burns a limited
  attempt counter in reality, so it never restarts (max_restarts=0) — modeling cost-of-failure.
"""

from __future__ import annotations

from orchestrator.models.attack import Attack, SingleStage
from orchestrator.models.device import DeviceCompatibilityReqs, IOSVersion

BOOTROM_CHAIN = Attack(
    id="bootrom-checkm8-style",
    description="Hardware bug; unpatchable, cheap to retry.",
    requirements=DeviceCompatibilityReqs(
        max_ios=IOSVersion(14, 8),
        supported_models=frozenset({"iPhone10,3", "iPhone10,6", "iPhone11,8"}),
        min_battery=10,
    ),
    max_restarts=3,
    stages=(
        SingleStage("DFU entry", "dfu", success_probability=0.95, max_retries=2),
        SingleStage("Bootrom trigger", "bootrom", success_probability=0.80, max_retries=1),
        SingleStage("Payload upload", "payload", success_probability=0.90),
    ),
)

KERNEL_CHAIN = Attack(
    id="kernel-exploit",
    description="Software bug; a failed attempt can panic the device.",
    requirements=DeviceCompatibilityReqs(
        min_ios=IOSVersion(14, 0),
        max_ios=IOSVersion(15, 4),
        min_battery=30,
    ),
    max_restarts=1,
    stages=(
        SingleStage("Info leak", "leak", success_probability=0.85),
        SingleStage("Kernel R/W", "kernel_rw", success_probability=0.70),
        SingleStage("Escalate", "escalate", success_probability=0.90, max_retries=1),
    ),
)

PASSCODE_CHAIN = Attack(
    id="passcode-bruteforce",
    description="Brute force; burns a limited attempt counter, so it never restarts.",
    requirements=DeviceCompatibilityReqs(min_battery=20),
    max_restarts=0,
    stages=(
        SingleStage("Pair with device", "pair", success_probability=0.99),
        SingleStage("Brute force passcode", "bruteforce", success_probability=0.40),
    ),
)

CATALOG: tuple[Attack, ...] = (BOOTROM_CHAIN, KERNEL_CHAIN, PASSCODE_CHAIN)
