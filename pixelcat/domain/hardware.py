"""What this PC can do for the local AI: memory, graphics cards, CPU, disk (plain data, no platform calls)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Gpu:
    name: str
    vram_gb: float
    integrated: bool


@dataclass
class Hardware:
    ram_gb: float = 16.0
    cores: int = 4
    cpu: str = ""
    avx2: bool = True
    gpus: list[Gpu] = field(default_factory=list)
    disk_free_gb: float = 100.0

    @property
    def gpu(self) -> Gpu | None:
        """The graphics card that helps the AI: a dedicated one with its own memory."""
        own = [g for g in self.gpus if not g.integrated and g.vram_gb >= 2]
        return max(own, key=lambda g: g.vram_gb) if own else None

    @property
    def vram_gb(self) -> float:
        g = self.gpu
        return g.vram_gb if g else 0.0

    def summary(self) -> str:
        """'16 GB memory · NVIDIA GeForce RTX 3060 (12 GB) · 8 cores'"""
        parts = [f"{self.ram_gb:.0f} GB memory"]
        g = self.gpu
        parts.append(f"{g.name} ({g.vram_gb:.0f} GB)" if g else "no graphics card for AI")
        parts.append(f"{self.cores} cores")
        return " · ".join(parts)
