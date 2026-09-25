"""What PGM deliberately does not do, and what only a person can do.

The optimisation guides this program drew on recommend more than it
implements. Everything left out is listed here with the reason, so "why
doesn't it disable HPET?" has an answer on screen instead of in someone's
memory — and so the list can be argued with, item by item.

Two lists:

``NOT_CHANGED``
    Recommendations PGM will not automate: dangerous, obsolete,
    contradicted by another source, placebo, or too hardware-specific to be
    right on every club PC.

``manual_steps``
    Settings that are real and useful but live in vendor software with no
    supported interface (NVIDIA Control Panel, AMD Software). PGM cannot
    change them without either an undocumented API or a third-party tool,
    so it names them as a checklist instead of pretending.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Exclusion:
    name: str
    reason: str
    category: str
    """dangerous | contradictory | placebo | hardware | obsolete | tool"""


NOT_CHANGED: tuple[Exclusion, ...] = (
    Exclusion(
        "Отключение Защитника Windows",
        "Источники прямо противоречат друг другу, а на клубном ПК, куда "
        "приносят флешки, выключенный антивирус — дыра, а не оптимизация. "
        "Состояние только показывается.",
        "dangerous",
    ),
    Exclusion(
        "Отключение Центра обновления Windows",
        "Отключённая служба оставляет ПК без исправлений безопасности и "
        "ломает Магазин и Xbox-игры. Показываются статус, ожидающая "
        "перезагрузка и пауза; приостановить обновления можно в «Параметрах».",
        "dangerous",
    ),
    Exclusion(
        "Отключение целостности памяти (HVCI)",
        "Решение о безопасности ядра, а не настройка производительности; "
        "эффект зависит от процессора. Состояние показывается.",
        "contradictory",
    ),
    Exclusion(
        "Отключение HPET / bcdedit useplatformclock / disabledynamictick",
        "На современных Windows HPET не используется как основной таймер, "
        "пока его не навязали через bcdedit; «отключение» в диспетчере "
        "устройств и правки загрузчика приводят к рассинхрону таймеров и "
        "статтерам на части систем. Одной кнопкой такое не делается.",
        "dangerous",
    ),
    Exclusion(
        "Разрешение системного таймера (TimerResolution, SystemTimerExpiration)",
        "С Windows 10 2004 разрешение таймера привязано к процессу, который "
        "его запросил; глобальные утилиты не делают того, что обещают, а "
        "игры сами запрашивают нужное разрешение.",
        "placebo",
    ),
    Exclusion(
        "Очистка standby list (ISLC, MemReduct)",
        "Постоянно работающий «чистильщик памяти» выбрасывает кэш, который "
        "Windows всё равно освобождает по требованию, и добавляет фоновый "
        "процесс. Встраивать его в программу нельзя.",
        "placebo",
    ),
    Exclusion(
        "Привязка к ядрам (affinity) игр и драйверов",
        "Ручная привязка легко ухудшает результат — особенно на гибридных "
        "Intel и двухкристальных Ryzen X3D, где планировщик и драйвер AMD "
        "делают это сами. Топология процессора показывается в аудите.",
        "hardware",
    ),
    Exclusion(
        "Приоритет процессов Realtime / High для игр",
        "Realtime может отнять процессор у драйверов ввода и звука и "
        "подвесить систему; High для всего подряд обесценивает приоритеты.",
        "dangerous",
    ),
    Exclusion(
        "Принудительный режим MSI и приоритеты IRQ",
        "Если драйвер устройства не поддерживает MSI, после перезагрузки "
        "оно остаётся без прерываний: для видеокарты — чёрный экран, для "
        "USB-контроллера с клавиатурой — нечем откатить. Текущий режим "
        "показывается; менять — вручную и осознанно.",
        "dangerous",
    ),
    Exclusion(
        "Отключение парковки ядер и C-состояний процессора",
        "На двухкристальных Ryzen X3D парковка — механизм, которым драйвер "
        "AMD переводит игру на кристалл с кэшем; отключение вредит. На "
        "остальных эффект зависит от процессора и охлаждения.",
        "hardware",
    ),
    Exclusion(
        "Глобальное отключение FSO через GameConfigStore",
        "Недокументированные ключи, часть которых современные Windows "
        "игнорирует; при этом именно оптимизации полноэкранного режима дают "
        "flip-модель. Для конкретной игры — галочка в свойствах .exe.",
        "contradictory",
    ),
    Exclusion(
        "Частота опроса USB (bInterval, hidusbf)",
        "Требует неподписанного драйвера-фильтра и зависит от прошивки "
        "мыши; игровые мыши задают частоту в своём ПО. Программа частоту "
        "не выдумывает — она её не измеряет.",
        "tool",
    ),
    Exclusion(
        "Модерация прерываний, RSS, разгрузки, буферы, jumbo-кадры",
        "Советы противоречат друг другу, правильное значение зависит от "
        "карты, драйвера и сети. Значения показываются в аудите.",
        "contradictory",
    ),
    Exclusion(
        "MMCSS: GPU Priority и SFIO Priority",
        "По документации Microsoft оба значения не используются; «Priority» "
        "при категории High всегда равен 2. Из этой задачи в "
        "экспериментальных только Scheduling Category.",
        "placebo",
    ),
    Exclusion(
        "Разгон, лимит мощности GPU, MSI Afterburner, CRU и тайминги монитора",
        "Зависит от конкретного экземпляра железа и может повредить его или "
        "вывести монитор из строя. Не для клубного конвейера.",
        "hardware",
    ),
    Exclusion(
        "DDU, NVCleanInstall, NVIDIA Profile Inspector, Autoruns",
        "Сторонние утилиты. Программа не скачивает и не запускает чужие "
        "исполняемые файлы; при необходимости их ставят с официальных "
        "сайтов вручную.",
        "tool",
    ),
    Exclusion(
        "Скрипты вида «irm URL | iex» и «FPS boost»-пакеты",
        "Выполнение кода из интернета без проверки. Никогда.",
        "dangerous",
    ),
)


@dataclass(frozen=True, slots=True)
class ManualStep:
    where: str
    setting: str
    value: str
    why: str


_NVIDIA: tuple[ManualStep, ...] = (
    ManualStep(
        "Панель управления NVIDIA → Управление параметрами 3D",
        "Режим управления электропитанием",
        "Предпочтителен режим максимальной производительности",
        "Не даёт видеокарте сбрасывать частоты между кадрами в лёгких сценах.",
    ),
    ManualStep(
        "Панель управления NVIDIA → Управление параметрами 3D",
        "Режим низкой задержки",
        "Вкл (или Ultra, если в игре нет NVIDIA Reflex)",
        "Сокращает очередь заранее подготовленных кадров. Если в игре есть "
        "Reflex — включать Reflex в игре, он главнее.",
    ),
    ManualStep(
        "Панель управления NVIDIA → Управление параметрами 3D",
        "Фильтрация текстур — качество",
        "Высокая производительность",
        "Небольшая экономия на фильтрации ценой едва заметного качества.",
    ),
    ManualStep(
        "Панель управления NVIDIA → Регулировка размера и положения рабочего стола",
        "Выполнить масштабирование на",
        "Дисплей (если монитор сам масштабирует без задержки) или ГП",
        "Зависит от монитора; проверяется глазами на своём железе.",
    ),
    ManualStep(
        "Панель управления NVIDIA → Изменение разрешения",
        "Частота обновления",
        "Максимальная для монитора",
        "Аудит ниже показывает, работает ли монитор на максимальной частоте; "
        "ОПТИМИЗИРОВАТЬ ПК это исправляет сам.",
    ),
)

_AMD: tuple[ManualStep, ...] = (
    ManualStep(
        "AMD Software → Игры → Графика",
        "Профиль графики",
        "«Киберспорт» или «Стандартный» с выключенными лишними функциями",
        "Профиль задаёт сразу несколько драйверных параметров.",
    ),
    ManualStep(
        "AMD Software → Игры → Графика → Дополнительно",
        "Качество фильтрации текстур / Оптимизация формата поверхности",
        "Производительность / Вкл",
        "Небольшая экономия на фильтрации и форматах поверхностей.",
    ),
    ManualStep(
        "AMD Software → Игры → Графика → Дополнительно",
        "Режим тесселяции",
        "Оптимизировано AMD",
        "Оставляет драйверу решать уровень тесселяции.",
    ),
    ManualStep(
        "AMD Software → Настройки → Горячие клавиши и Оверлей",
        "Оверлей, запись, мгновенный повтор",
        "Выключить, если не используются",
        "Фоновая запись держит видеокодировщик занятым, как Game DVR.",
    ),
)


def manual_steps(gpu_vendors: list[str]) -> list[ManualStep]:
    """The checklist for the GPUs present."""
    steps: list[ManualStep] = []
    joined = " ".join(v.lower() for v in gpu_vendors)
    if "nvidia" in joined:
        steps.extend(_NVIDIA)
    if "amd" in joined or "advanced micro" in joined or "radeon" in joined:
        steps.extend(_AMD)
    return steps


__all__ = ["NOT_CHANGED", "Exclusion", "ManualStep", "manual_steps"]
