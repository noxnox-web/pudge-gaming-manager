"""Blocking dota2.com, on one unreplicated report.

Where this came from
--------------------
Valve's own tracker, ValveSoftware/Dota2-Gameplay issue #35438. A player
reports that Dota 2 loses frames progressively across a long client session
— roughly 310 down to 190 in one match, 210 down to 160 in the next, as low
as 100 over an evening — and that blocking ``dota2.com`` in the hosts file
almost removes the decline, holding about 300 across three matches.

Why this is not a RECOMMENDED tweak
-----------------------------------
It fails both tests this program applies to a tweak, and saying so is more
useful than dressing it up.

Rule #64 wants a documented mechanism. There is none. The reporter offers a
hypothesis — "it may be worth investigating web/dashboard/Panorama content
for resource accumulation" — and hypothesis is the right word: Valve has not
responded, the issue carries no label, and nobody has published a
reproduction.

Rule #65 forbids claiming a frame rate. Here the frame rate *is* the entire
claim, and it rests on one person, one machine, one evening.

There is also a confound nobody has ruled out. Applying a hosts change means
restarting the client, so every "blocked" measurement began on a fresh
process. Progressive loss across a long session is equally the signature of
thermal throttling, a driver leak or a growing shader cache, all of which a
restart also relieves. The comparison that would settle it — fresh client,
same number of matches, domain unblocked — is not in the report.

What it costs
-------------
The report does not mention side effects, which is not the same as there
being none. The Dota 2 client loads web content from that domain for the
news panel on the main menu, event and battle-pass pages, parts of the store
and the esports sections. Those stop loading. Matchmaking, the game servers
and Steam itself are elsewhere and are not affected.

So it is offered, unticked, at MEDIUM, with the evidence stated. An operator
who has seen the decline on their own machines can decide it is worth a try;
nobody gets it because they did not untick a row.
"""

from __future__ import annotations

from ....utilities.logging_setup import get_logger
from ....windows import hosts
from ..tweak import (
    ApplyResult,
    BackupRecord,
    BackupScope,
    Outcome,
    RiskLevel,
    Tweak,
    TweakContext,
    TweakState,
    Validation,
    Verification,
)

_log = get_logger(__name__)

#: Both spellings. A hosts entry matches one name exactly — there are no
#: wildcards — so blocking only the bare domain leaves ``www`` resolving,
#: which is how a blocklist copied from a forum post ends up doing nothing.
DOTA_HOSTS: tuple[str, ...] = ("dota2.com", "www.dota2.com")


class BlockDotaWebTweak(Tweak):
    """Point dota2.com at nowhere, through PGM's own hosts block."""

    id = "gaming.dota.block_web_domain"
    version = 1
    name = "Блокировка dota2.com"
    description = (
        "Добавляет dota2.com и www.dota2.com в файл hosts. Перестанут "
        "грузиться панель новостей, страницы событий и часть магазина "
        "внутри клиента Dota 2. Матчмейкинг и игровые серверы не "
        "затрагиваются."
    )
    rationale = (
        "Экспериментально, на основании одного сообщения в трекере Valve "
        "(Dota2-Gameplay #35438): у автора кадры падали по ходу длинной "
        "сессии — 310 → 190 за матч, к вечеру до 100 — и блокировка домена "
        "почти убрала просадку. Это единственный отчёт, на одной машине, "
        "без воспроизведения и без ответа Valve; механизм автор сам называет "
        "предположением. Не исключён и посторонний фактор: чтобы применить "
        "правку, клиент перезапускают, поэтому замеры «с блокировкой» шли "
        "со свежего процесса, а прогрессирующая просадка — типичный симптом "
        "ещё и троттлинга или утечки в драйвере. Прирост кадров не "
        "обещается: обещается ровно то, что домен перестанет резолвиться."
    )
    risk = RiskLevel.MEDIUM
    subsystem = "gaming"
    scope = BackupScope.FILE
    requires_admin = True
    """The hosts file lives under System32 and is writable only elevated."""

    # -- lifecycle ---------------------------------------------------------

    def scan(self, ctx: TweakContext) -> TweakState:
        current = hosts.blocked_hosts()
        if current is None:
            return TweakState(
                needs_change=False,
                current_absent=True,
                summary="Файл hosts не читается",
            )

        missing = [name for name in DOTA_HOSTS if name not in current]
        return TweakState(
            current_value=",".join(current),
            desired_value=",".join(DOTA_HOSTS),
            needs_change=bool(missing),
            summary=(
                f"Заблокировать в hosts: {', '.join(missing)}"
                if missing
                else "dota2.com уже заблокирован в hosts"
            ),
        )

    def validate(self, ctx: TweakContext, state: TweakState) -> Validation:
        if state.current_absent:
            return Validation.refuse(
                "Файл hosts не удалось прочитать, поэтому неизвестно, что в "
                "нём сейчас, и вернуть его обратно будет нечем."
            )
        return Validation.allow(requires_admin=True)

    def backup(self, ctx: TweakContext, state: TweakState) -> BackupRecord:
        """Record our block's contents, not the whole file.

        Restoring a copy of the file would also undo whatever another tool
        wrote to it in the meantime. The rollback puts our block back the
        way it was and touches nothing else.
        """
        return BackupRecord(
            backup_id=BackupRecord.new_id(),
            tweak_id=self.id,
            tweak_version=self.version,
            scope=BackupScope.FILE,
            target=str(hosts.path()),
            old_value=state.current_value or "",
            old_value_absent=not state.current_value,
            old_value_kind="HOSTS_BLOCK",
            new_value=",".join(DOTA_HOSTS),
        )

    def apply(self, ctx: TweakContext, state: TweakState) -> ApplyResult:
        if ctx.dry_run:
            return ApplyResult(Outcome.SUCCESS, "Пробный прогон: hosts не изменён.")

        existing = hosts.blocked_hosts() or []
        merged = list(existing) + [n for n in DOTA_HOSTS if n not in existing]
        if not hosts.set_block(merged):
            return ApplyResult(
                Outcome.FAILED,
                "Не удалось записать файл hosts. Обычно это значит, что "
                "программа запущена без прав администратора или файл "
                "защищён антивирусом.",
            )

        hosts.flush_dns(ctx.runner)
        return ApplyResult(
            Outcome.SUCCESS,
            "dota2.com и www.dota2.com заблокированы, кэш DNS сброшен. "
            "Клиент Dota 2 нужно перезапустить.",
        )

    def verify(self, ctx: TweakContext, state: TweakState) -> Verification:
        current = hosts.blocked_hosts()
        if current is None:
            return Verification.deny(None, "Файл hosts не перечитывается.")
        missing = [name for name in DOTA_HOSTS if name not in current]
        if missing:
            return Verification.deny(
                ",".join(current), f"В hosts не появилось: {', '.join(missing)}."
            )
        return Verification.confirm(
            ",".join(current), "Обе записи есть в файле hosts."
        )

    def rollback(self, ctx: TweakContext, backup: BackupRecord) -> ApplyResult:
        previous = [n for n in str(backup.old_value or "").split(",") if n]
        if not hosts.set_block(previous):
            return ApplyResult(
                Outcome.FAILED, "Не удалось вернуть прежний блок в hosts."
            )
        hosts.flush_dns(ctx.runner)

        current = hosts.blocked_hosts()
        if current is None or any(n in current for n in DOTA_HOSTS):
            return ApplyResult(
                Outcome.FAILED, "Записи dota2.com остались в нашем блоке hosts."
            )

        # Our lines are gone; somebody else's are not ours to remove, and
        # claiming the domain resolves again when they are still there would
        # be wrong.
        foreign = [n for n in DOTA_HOSTS if hosts.other_entry_exists(n)]
        if foreign:
            return ApplyResult(
                Outcome.SUCCESS,
                f"Наш блок убран, но {', '.join(foreign)} остаётся "
                "заблокирован записью, которую добавила другая программа.",
            )
        return ApplyResult(Outcome.SUCCESS, "Записи из hosts убраны.")


__all__ = ["DOTA_HOSTS", "BlockDotaWebTweak"]
