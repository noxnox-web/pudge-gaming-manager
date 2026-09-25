"""Which applications may be removed, named one by one.

There is no "remove unused apps" rule here, because nothing in an inventory
says whether a person uses something. Every name below was chosen
deliberately, and the entries that are *deliberately absent* matter as much
as the ones present — that is what the exclusion lists are for.

The list is short on purpose. A gaming club PC is not made faster by
uninstalling forty Store applications that were never running; it is made
broken by uninstalling the one that a game needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AppGroup:
    """A named set of packages a single tweak removes."""

    id: str
    name: str
    rationale: str
    packages: tuple[str, ...]

    excluded: tuple[tuple[str, str], ...] = field(default=())
    """``(package, why)`` for things a reader would expect to find here.

    Recorded rather than simply left out: the next person to read this list
    will wonder whether Xbox Identity Provider was forgotten, and the answer
    has to be in the file, not in somebody's memory.
    """


#: Xbox applications. **Not** the sign-in broker, and not the shell
#: component, which is why this group exists as a curated list rather than
#: "everything matching Xbox".
XBOX = AppGroup(
    id="xbox",
    name="Приложения Xbox",
    rationale=(
        "Приложение Xbox и игровая панель держат фоновые процессы и "
        "оверлей поверх игр. Убираются само приложение и оверлей; вход в "
        "Xbox Live не затрагивается."
    ),
    packages=(
        "Microsoft.GamingApp",
        "Microsoft.XboxApp",
        "Microsoft.XboxGamingOverlay",
        "Microsoft.XboxSpeechToTextOverlay",
        "Microsoft.Xbox.TCUI",
    ),
    excluded=(
        (
            "Microsoft.XboxIdentityProvider",
            "через него игры входят в Xbox Live. Без него не запускаются "
            "игры Game Pass, Minecraft, Forza и часть игр из Steam, "
            "использующих аккаунт Xbox. На игровом ПК это ломает ровно то, "
            "ради чего ПК существует",
        ),
        (
            "Microsoft.XboxGameCallableUI",
            "компонент оболочки Windows, а не приложение: подписан как "
            "System и удалению не подлежит",
        ),
    ),
)

#: Windows 11 widgets board.
WIDGETS = AppGroup(
    id="widgets",
    name="Виджеты Windows",
    rationale=(
        "Панель виджетов держит фоновый процесс, который тянет ленту "
        "новостей и погоду и рисует всплывающую панель поверх экрана. "
        "Только Windows 11."
    ),
    packages=("MicrosoftWindows.Client.WebExperience",),
)

#: The consumer Teams, the one Windows 11 pins as "Chat".
TEAMS = AppGroup(
    id="teams",
    name="Teams (личная версия)",
    rationale=(
        "Личный Teams, который Windows 11 закрепляет на панели задач как "
        "«Чат». Запускается при входе и держит процесс. Рабочий Teams — "
        "отдельное приложение и не затрагивается."
    ),
    packages=("MicrosoftTeams",),
    excluded=(
        (
            "MSTeams",
            "новый рабочий клиент Teams: на клубном ПК его обычно нет, а "
            "если поставили — значит он кому-то нужен",
        ),
    ),
)

#: Store applications a club PC has no use for. Each one named, none
#: matched by pattern.
UNUSED = AppGroup(
    id="unused",
    name="Неиспользуемые приложения Windows",
    rationale=(
        "Предустановленные приложения Магазина, которым на клубном ПК "
        "нечего делать: новости, погода, пасьянс, устаревшие плееры, "
        "просмотрщики 3D. Каждое перечислено поимённо — правила «удалить "
        "всё неиспользуемое» здесь нет, потому что по списку установленного "
        "нельзя понять, пользуется ли им человек."
    ),
    packages=(
        "Microsoft.BingNews",
        "Microsoft.BingWeather",
        "Microsoft.BingFinance",
        "Microsoft.BingSports",
        "Microsoft.MicrosoftSolitaireCollection",
        "Microsoft.ZuneMusic",
        "Microsoft.ZuneVideo",
        "Microsoft.Microsoft3DViewer",
        "Microsoft.MSPaint",
        "Microsoft.MixedReality.Portal",
        "Microsoft.GetHelp",
        "Microsoft.Getstarted",
        "Microsoft.WindowsFeedbackHub",
        "Microsoft.WindowsMaps",
        "Microsoft.Wallet",
        "Microsoft.MicrosoftOfficeHub",
        "Microsoft.Office.OneNote",
        "Microsoft.SkypeApp",
        "Microsoft.People",
        "Microsoft.Todos",
        "Microsoft.windowscommunicationsapps",
        "Clipchamp.Clipchamp",
        "MicrosoftCorporationII.MicrosoftFamily",
    ),
    excluded=(
        (
            "Microsoft.WindowsStore",
            "без Магазина не переустановить ничего из удалённого, включая "
            "по ошибке",
        ),
        (
            "Microsoft.WindowsCalculator",
            "ничего не грузит в фоне и регулярно нужен; удалять его — "
            "экономия, которой не существует",
        ),
        (
            "Microsoft.ScreenSketch",
            "«Ножницы» — то, чем игрок снимает скриншот бага или счёта",
        ),
        (
            "Microsoft.YourPhone",
            "«Связь с телефоном» кто-то действительно использует, и это "
            "не фоновая нагрузка на игру",
        ),
        (
            "Microsoft.WindowsNotepad",
            "блокнот: им правят конфиги игр и читают логи, и на клубном ПК "
            "это единственный текстовый редактор",
        ),
        (
            "Microsoft.SecHealthUI",
            "интерфейс Защитника Windows: безопасность, а не украшение",
        ),
    ),
)

GROUPS: tuple[AppGroup, ...] = (XBOX, WIDGETS, TEAMS, UNUSED)

GROUPS_BY_ID: dict[str, AppGroup] = {g.id: g for g in GROUPS}


def all_excluded() -> frozenset[str]:
    """Every package some group deliberately refuses to remove."""
    return frozenset(
        package.lower()
        for group in GROUPS
        for package, _reason in group.excluded
    )


__all__ = [
    "GROUPS",
    "GROUPS_BY_ID",
    "TEAMS",
    "UNUSED",
    "WIDGETS",
    "XBOX",
    "AppGroup",
    "all_excluded",
]
