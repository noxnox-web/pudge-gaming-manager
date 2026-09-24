"""Findings from the network scan.

Every limit here is a PGM heuristic, labelled as such (rule #8): there is no
vendor document that says how much LAN jitter is too much for a club.

Absence of data is still not evidence (rule #64). A gateway that does not
answer ping, with the internet answering fine, is a router that filters
ICMP — not a broken LAN — and produces no finding.
"""

from __future__ import annotations

from ...hardware.models import ThresholdKind
from ...network.models import LinkKind, NetworkSnapshot, PingStats
from .model import Issue, Severity

#: A gigabit port negotiating less almost always means a damaged cable or
#: one wired with only two pairs.
EXPECTED_WIRED_MBPS = 1000

#: The first hop on a wired LAN answers in well under a millisecond.
GATEWAY_LATENCY_WARNING_MS = {LinkKind.WIRED: 5.0, LinkKind.WIRELESS: 20.0}

#: Any loss to the gateway is abnormal on a wired LAN.
GATEWAY_LOSS_WARNING_PERCENT = 1.0
GATEWAY_LOSS_CRITICAL_PERCENT = 20.0

INTERNET_LOSS_WARNING_PERCENT = 5.0
INTERNET_JITTER_WARNING_MS = 15.0
INTERNET_LATENCY_WARNING_MS = 100.0

_HEURISTIC = ThresholdKind.HEURISTIC


def detect_network_issues(network: NetworkSnapshot) -> list[Issue]:
    if network.unavailable_reason:
        return []
    if not network.connected:
        return [
            Issue(
                id="network.disconnected",
                title="Нет сетевого подключения",
                detail="У Windows нет активного адаптера с маршрутом наружу.",
                severity=Severity.CRITICAL,
                subsystem="network",
                fixable=False,
                fix_hint="Проверьте кабель, порт коммутатора и драйвер адаптера.",
            )
        ]

    issues: list[Issue] = []
    if network.path is None and network.gateway is None:
        issues.append(
            Issue(
                id="network.no_default_route",
                title="Нет маршрута в интернет",
                detail=(
                    "Сетевая карта подключена, но у Windows нет шлюза по умолчанию, "
                    "поэтому за пределами локальной сети ничего недоступно. "
                    "Это нормально для LAN-турнира; иначе роутер или DHCP "
                    "не выдали шлюз."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Проверьте роутер и что ПК получает адрес по DHCP.",
            )
        )
    issues.extend(_link_issues(network))
    issues.extend(_path_issues(network))
    issues.extend(_gateway_issues(network))
    issues.extend(_internet_issues(network.internet_ping))
    return issues


def _link_issues(network: NetworkSnapshot) -> list[Issue]:
    uplink = network.uplink
    if uplink is None:
        return []
    found: list[Issue] = []

    if uplink.kind is LinkKind.WIRELESS:
        found.append(
            Issue(
                id="network.wireless",
                title="Этот ПК на Wi-Fi",
                detail=(
                    f"{uplink.name} ({uplink.description}) — беспроводной канал. "
                    "Wi-Fi делит эфир со всеми устройствами рядом, что в играх "
                    "проявляется скачками задержки, которых нет на кабеле."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Подключите ПК кабелем Ethernet.",
                threshold_kind=_HEURISTIC,
            )
        )

    speed = uplink.link_speed_mbps
    if uplink.kind is LinkKind.WIRED and speed is not None and speed < EXPECTED_WIRED_MBPS:
        found.append(
            Issue(
                id="network.link_speed",
                title=f"Ethernet работает на {speed} Мбит/с",
                detail=(
                    f"{uplink.name} согласовал {speed} Мбит/с. В гигабитной сети "
                    "это обычно повреждённый кабель, кабель с двумя парами "
                    "или порт коммутатора на 100 Мбит/с."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Замените патч-корд или попробуйте другой порт коммутатора.",
                threshold_kind=_HEURISTIC,
            )
        )

    if uplink.kind is LinkKind.WIRED and uplink.full_duplex is False:
        found.append(
            Issue(
                id="network.half_duplex",
                title="Ethernet работает в полудуплексе",
                detail=(
                    f"{uplink.name} может передавать или принимать, но не одновременно. "
                    "Это ошибка согласования; под нагрузкой вызывает коллизии и "
                    "повторные передачи."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Включите автосогласование на адаптере и порту коммутатора.",
            )
        )
    return found


def _path_issues(network: NetworkSnapshot) -> list[Issue]:
    if not network.tunnelled or network.path is None:
        return []
    path = network.path
    return [
        Issue(
            id="network.tunnelled",
            title=f"Трафик в интернет идёт через «{path.name}»",
            detail=(
                f"Windows направляет интернет-трафик через {path.name} "
                f"({path.description}) — виртуальный адаптер, а не сетевую "
                "карту. Обычно это VPN или туннель; тогда игровой трафик идёт "
                "маршрутом и с задержкой туннеля. Игнорируйте, если так "
                "задумано."
            ),
            severity=Severity.WARNING,
            subsystem="network",
            fixable=False,
            fix_hint="Отключите VPN или исключите из него игры.",
            threshold_kind=_HEURISTIC,
        )
    ]


def _gateway_issues(network: NetworkSnapshot) -> list[Issue]:
    stats = network.gateway_ping
    uplink = network.uplink
    if stats is None or not stats.trustworthy or not stats.reachable:
        return []
    found: list[Issue] = []

    loss = stats.loss_percent
    if loss >= GATEWAY_LOSS_WARNING_PERCENT:
        found.append(
            Issue(
                id="network.gateway_loss",
                title=f"Потери пакетов до роутера: {loss:.0f}%",
                detail=(
                    f"Вернулось {stats.received} из {stats.sent} эхо-запросов к шлюзу "
                    f"{stats.target}. Потери на первом узле указывают на "
                    "кабель, коммутатор или роутер, а не на интернет."
                ),
                severity=(
                    Severity.CRITICAL
                    if loss >= GATEWAY_LOSS_CRITICAL_PERCENT
                    else Severity.WARNING
                ),
                subsystem="network",
                fixable=False,
                fix_hint="Проверьте кабель и порт коммутатора этого ПК.",
                threshold_kind=_HEURISTIC,
            )
        )

    limit = GATEWAY_LATENCY_WARNING_MS.get(uplink.kind if uplink else LinkKind.OTHER)
    average = stats.avg_ms
    if limit is not None and average is not None and average > limit:
        found.append(
            Issue(
                id="network.gateway_latency",
                title=f"Роутер отвечает за {average:.0f} мс",
                detail=(
                    f"Шлюз {stats.target} отвечает в среднем за {average:.0f} мс "
                    f"(макс {stats.max_ms:.0f} мс). Локальный узел должен "
                    f"отвечать за единицы миллисекунд (эвристический предел {limit:.0f} мс)."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Проверьте, не перегружен ли роутер.",
                threshold_kind=_HEURISTIC,
            )
        )
    return found


def _internet_issues(stats: PingStats | None) -> list[Issue]:
    if stats is None or not stats.trustworthy:
        return []
    if not stats.reachable:
        return [
            Issue(
                id="network.internet_unreachable",
                title="Нет ответа из интернета",
                detail=(
                    f"{stats.label} не ответил на {stats.sent} эхо-запросов. Либо "
                    "интернет недоступен с этого ПК, либо сеть блокирует ping — "
                    "эта проверка не может отличить одно от другого."
                ),
                severity=Severity.WARNING,
                subsystem="network",
                fixable=False,
                fix_hint="Откройте сайт, чтобы проверить интернет.",
            )
        ]

    found: list[Issue] = []
    if stats.loss_percent >= INTERNET_LOSS_WARNING_PERCENT:
        found.append(
            _internet_issue(
                "network.internet_loss",
                f"{stats.loss_percent:.0f}% packet loss to the internet",
                f"Вернулось {stats.received} из {stats.sent} эхо-запросов к {stats.label}.",
            )
        )
    jitter = stats.jitter_ms
    if jitter is not None and jitter > INTERNET_JITTER_WARNING_MS:
        found.append(
            _internet_issue(
                "network.internet_jitter",
                f"Нестабильная задержка: джиттер {jitter:.0f} мс",
                f"Задержка до {stats.label} колеблется в среднем на {jitter:.0f} мс "
                "между соседними запросами.",
            )
        )
    average = stats.avg_ms
    if average is not None and average > INTERNET_LATENCY_WARNING_MS:
        found.append(
            _internet_issue(
                "network.internet_latency",
                f"Высокая задержка до интернета: {average:.0f} мс",
                f"{stats.label} отвечает в среднем за {average:.0f} мс. Это опорная "
                "точка, не игровой сервер, но медленный путь до неё обычно "
                "означает медленный путь везде.",
            )
        )
    return found


def _internet_issue(issue_id: str, title: str, detail: str) -> Issue:
    return Issue(
        id=issue_id,
        title=title,
        detail=detail,
        severity=Severity.WARNING,
        subsystem="network",
        fixable=False,
        fix_hint="Проверьте канал клуба и затронуты ли другие ПК.",
        threshold_kind=_HEURISTIC,
    )
