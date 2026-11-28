"""pixelcat.domain.hardware: which graphics card helps the AI, and the summary line (plain data)."""
from __future__ import annotations

import pytest

from pixelcat.domain import Gpu, Hardware


@pytest.mark.parametrize("gpus, best", [
    ([Gpu("Intel(R) UHD Graphics", 0.1, True), Gpu("NVIDIA GeForce RTX 3060", 12.0, False),
      Gpu("NVIDIA GeForce GTX 1650", 4.0, False)], "NVIDIA GeForce RTX 3060"),
    ([Gpu("AMD Radeon RX 6400", 1.9, False)], None),                 # too little memory to help
    ([Gpu("Big APU", 16.0, True)], None),                            # integrated never counts
    ([], None),
])
def test_hardware_gpu_is_the_biggest_dedicated_card(gpus, best):
    hw = Hardware(gpus=gpus)
    assert (hw.gpu.name if hw.gpu else None) == best
    assert hw.vram_gb == (hw.gpu.vram_gb if hw.gpu else 0.0)


def test_hardware_summary():
    hw = Hardware(ram_gb=15.7, cores=8, gpus=[Gpu("NVIDIA GeForce RTX 3060", 12.0, False)])
    assert hw.summary() == "16 GB memory · NVIDIA GeForce RTX 3060 (12 GB) · 8 cores"
    assert Hardware(ram_gb=8, cores=4).summary() == "8 GB memory · no graphics card for AI · 4 cores"
    assert Hardware(ram_gb=36, cores=12, unified_memory=True).summary() == \
        "36 GB unified memory · graphics built in · 12 cores"
