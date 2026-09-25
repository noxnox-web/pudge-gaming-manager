"""Graphics and multimedia scheduling settings, each one documented.

Both tweaks here change a single registry value that Microsoft documents
and that Windows reads back, which is the bar rule #64 sets: a setting whose
mechanism can be described, whose state can be observed, and whose previous
value can be restored. Neither claims a frame rate.
"""

from __future__ import annotations

from typing import Any

from ....windows.registry.manager import Hive
from ..tweak import RiskLevel
from .registry_value import ABSENT, RegistryValueTweak


class HardwareGpuSchedulingTweak(RegistryValueTweak):
    """Turn on Hardware-Accelerated GPU Scheduling.

    Rationale (rule #64)
    --------------------
    Normally Windows batches and schedules GPU work itself. With hardware
    scheduling the GPU's own scheduling processor manages its queues, which
    removes a round trip through the operating system for each submission.
    Microsoft documents the setting and exposes it in Graphics settings; it
    is also the prerequisite for several driver-side latency features.

    **What is claimed:** the scheduling mode changes, and the value is
    readable afterwards.
    **What is not claimed:** more frames. Published measurements go both
    ways depending on GPU, driver and title, so promising a gain here would
    be inventing one.

    Requires a restart, and a GPU and driver that support it — on hardware
    that does not, Windows ignores the value, which is why verification
    reports "stored, takes effect after a restart" rather than "in force".
    """

    id = "graphics.gpu_scheduling.enable"
    version = 1
    name = "Аппаратное планирование GPU"
    description = (
        "Передаёт планирование очередей видеокарте вместо Windows. "
        "Требует перезагрузки и поддержки со стороны видеокарты и драйвера."
    )
    rationale = (
        "Обычно очередями GPU управляет Windows. При аппаратном "
        "планировании этим занимается сам планировщик видеокарты, что "
        "убирает обращение к системе на каждую отправку работы. Настройка "
        "документирована Microsoft и доступна в параметрах графики; прирост "
        "кадров не обещается — опубликованные замеры расходятся в "
        "зависимости от видеокарты, драйвера и игры."
    )
    risk = RiskLevel.MEDIUM
    subsystem = "graphics"
    requires_admin = True
    restart_required = True

    hive = Hive.HKLM
    subkey = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
    value_name = "HwSchMode"
    desired_data = 2
    value_type = "REG_DWORD"

    #: The documented encoding of ``HwSchMode``.
    _STATES = {1: "выключено", 2: "включено"}

    def describe(self, data: Any) -> str:
        if data == ABSENT:
            return "не задано (по умолчанию драйвера)"
        return self._STATES.get(data, str(data))


class NetworkThrottlingTweak(RegistryValueTweak):
    """Stop Windows throttling network traffic while media is playing.

    Rationale (rule #64)
    --------------------
    This is the rare gaming registry setting with a documented, specific
    mechanism rather than folklore. When a multimedia task is running under
    MMCSS, Windows limits non-multimedia network traffic to roughly ten
    packets per millisecond so that audio and video are not starved of CPU
    by the network stack. ``NetworkThrottlingIndex`` is that limit, and
    ``0xFFFFFFFF`` disables the throttle. Microsoft documents both.

    A multiplayer game sends many small packets and is not registered as a
    multimedia task, so it sits on the throttled side of that rule while
    anything else on the PC plays audio — which on a club machine is most of
    the time.

    **What is claimed:** the throttle is off, and the value reads back.
    **What is not claimed:** lower ping. The limit only binds when it binds.
    """

    id = "network.throttling.disable"
    version = 1
    name = "Сетевой троттлинг"
    description = (
        "Снимает ограничение Windows на сетевой трафик немультимедийных "
        "приложений, которое действует, пока на ПК воспроизводится звук "
        "или видео."
    )
    rationale = (
        "Пока работает мультимедийная задача, Windows ограничивает трафик "
        "остальных приложений примерно десятью пакетами в миллисекунду, "
        "чтобы звук и видео не голодали. Многопользовательская игра шлёт "
        "много мелких пакетов и мультимедийной задачей не считается, "
        "поэтому попадает под это ограничение. Механизм и значение "
        "документированы Microsoft."
    )
    risk = RiskLevel.LOW
    subsystem = "network"
    requires_admin = True
    restart_required = True

    hive = Hive.HKLM
    subkey = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
    value_name = "NetworkThrottlingIndex"
    desired_data = 0xFFFFFFFF
    value_type = "REG_DWORD"

    def describe(self, data: Any) -> str:
        if data == ABSENT:
            return "не задано (10 — ограничение включено)"
        if data == 0xFFFFFFFF:
            return "выключено"
        return f"{data} пакетов/мс"


__all__ = ["HardwareGpuSchedulingTweak", "NetworkThrottlingTweak"]
