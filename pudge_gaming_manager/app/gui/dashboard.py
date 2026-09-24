"""The main dashboard window.

Layout follows the brief: machine identity and readiness at the top, the
measured metrics on the left, the score and the single primary action on the
right, findings below.

Presentation rules this window enforces:

* An unavailable value reads ``UNAVAILABLE`` in grey with its reason in the
  tooltip. It never shows ``0`` and never borrows a warning colour.
* The score shows its own arithmetic on demand (rule #30).
* ``OPTIMIZE PC`` is disabled until a scan has actually produced findings,
  because a button that does nothing teaches an operator to distrust it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.types import Issue
from ..controllers.optimize_controller import OptimizeController
from ..controllers.profile_controller import ProfileController
from ..controllers.scan_controller import ScanController, ScanResult
from ..controllers.steam_controller import SteamController
from . import presenters, theme, widgets
from .dashboard_actions import ProfileAndGamesActions
from .preview_dialog import PreviewDialog, ResultDialog
from .widgets import MetricRow


class Dashboard(ProfileAndGamesActions, QMainWindow):
    """Administrator dashboard."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pudge Gaming Manager")
        self.resize(1180, 760)
        self.setStyleSheet(theme.stylesheet())

        self._result: ScanResult | None = None
        self._controller = ScanController(self)
        self._optimizer = OptimizeController(parent=self)
        self._profiles = ProfileController(self)
        self._steam = SteamController(self)

        # Widgets must exist before any signal is bound to them.
        self._build()

        self._controller.started.connect(self._on_scan_started)
        self._controller.progress.connect(self._status.setText)
        self._controller.completed.connect(self._on_scan_completed)
        self._controller.failed.connect(self._on_scan_failed)

        self._optimizer.preview_ready.connect(self._on_preview_ready)
        self._optimizer.apply_finished.connect(self._on_optimize_finished)
        self._optimizer.failed.connect(self._on_optimize_failed)

        self._profiles.saved.connect(self._on_profile_saved)
        self._profiles.compared.connect(self._on_profile_compared)
        self._profiles.failed.connect(self._on_profile_failed)

        self._steam.planned.connect(self._on_steam_planned)
        self._steam.wiped.connect(self._on_steam_wiped)
        self._steam.failed.connect(self._on_steam_failed)

        self._controller.start()
        # After the window is up, report any change a previous run left
        # unfinished (a crash or power cut mid-apply).
        QTimer.singleShot(0, self._report_interrupted_changes)

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(18)

        outer.addLayout(self._build_header())
        outer.addLayout(self._build_body(), stretch=1)
        self.setCentralWidget(root)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()

        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(widgets.label("PUDGE GAMING MANAGER", "Title"))
        self._machine = widgets.label("Сканирование…", "Subtitle")
        left.addWidget(self._machine)

        right = QVBoxLayout()
        right.setSpacing(2)
        self._state = widgets.label("СКАНИРОВАНИЕ", "ScoreCaption")
        self._state.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._state.setStyleSheet(f"color: {theme.WARNING};")
        self._status = widgets.label("", "ScoreNote")
        self._status.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self._state)
        right.addWidget(self._status)

        row.addLayout(left)
        row.addStretch(1)
        row.addLayout(right)
        return row

    def _build_body(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addWidget(self._build_metrics(), stretch=3)

        column = QVBoxLayout()
        column.setSpacing(18)
        column.addWidget(self._build_score())
        column.addWidget(self._build_issues(), stretch=1)
        row.addLayout(column, stretch=4)
        return row

    def _build_metrics(self) -> QFrame:
        card = widgets.card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(2)

        layout.addWidget(widgets.label("СИСТЕМА", "SectionHeading"))
        layout.addSpacing(8)

        self._metrics: dict[str, MetricRow] = {}
        for key, name in (
            ("cpu", "CPU"),
            ("gpu", "GPU"),
            ("ram", "Память"),
            ("disk", "Системный диск"),
            ("display", "Дисплей"),
            ("network", "Сеть"),
        ):
            widget = MetricRow(name)
            self._metrics[key] = widget
            layout.addWidget(widget)

        layout.addSpacing(10)
        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)
        layout.addSpacing(10)

        self._scan_meta = widgets.label("", "MetricDetail")
        self._scan_meta.setWordWrap(True)
        layout.addWidget(self._scan_meta)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        self._rescan = QPushButton("Пересканировать")
        self._rescan.setObjectName("Secondary")
        self._rescan.clicked.connect(self._controller.start)
        buttons.addWidget(self._rescan)

        # Golden Profile actions need a scan to capture or compare against.
        self._save_profile = QPushButton("Сохранить профиль…")
        self._save_profile.setObjectName("Secondary")
        self._save_profile.setEnabled(False)
        self._save_profile.clicked.connect(self._on_save_profile)
        buttons.addWidget(self._save_profile)

        self._compare_profile = QPushButton("Сравнить с профилем…")
        self._compare_profile.setObjectName("Secondary")
        self._compare_profile.setEnabled(False)
        self._compare_profile.clicked.connect(self._on_compare_profile)
        buttons.addWidget(self._compare_profile)
        layout.addLayout(buttons)

        # A club reset: remove every Steam game except those a profile keeps.
        self._reset_steam = QPushButton("ОЧИСТКА СТИМА")
        self._reset_steam.setObjectName("Secondary")
        self._reset_steam.setEnabled(False)
        self._reset_steam.clicked.connect(self._on_reset_steam)
        layout.addWidget(self._reset_steam)
        return card

    def _build_score(self) -> QFrame:
        card = widgets.card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(6)

        layout.addWidget(widgets.label("ГОТОВНОСТЬ К ИГРАМ", "SectionHeading"))

        self._score_value = widgets.label("—", "ScoreValue")
        self._score_value.setStyleSheet(f"color: {theme.UNAVAILABLE};")
        layout.addWidget(self._score_value)

        self._score_note = widgets.label(
            "Диагностический показатель, не прогноз FPS.", "ScoreNote"
        )
        self._score_note.setWordWrap(True)
        layout.addWidget(self._score_note)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)
        layout.addSpacing(10)

        buttons = QHBoxLayout()
        self._optimize = QPushButton("ОПТИМИЗИРОВАТЬ ПК")
        self._optimize.setObjectName("Primary")
        self._optimize.setEnabled(False)
        self._optimize.clicked.connect(self._on_optimize)
        buttons.addWidget(self._optimize, stretch=2)

        self._explain = QPushButton("Как это посчитано?")
        self._explain.setObjectName("Secondary")
        self._explain.setEnabled(False)
        self._explain.clicked.connect(self._on_explain)
        buttons.addWidget(self._explain, stretch=1)
        layout.addLayout(buttons)
        return card

    def _build_issues(self) -> QFrame:
        card = widgets.card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        self._issues_heading = widgets.label("НАХОДКИ", "SectionHeading")
        layout.addWidget(self._issues_heading)

        self._issues = QListWidget()
        self._issues.setWordWrap(True)
        # Findings wrap; a horizontal scrollbar would only ever be an
        # empty bar across the bottom of the card.
        self._issues.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._issues.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self._issues, stretch=1)
        return card

    # -- scan lifecycle ----------------------------------------------------

    def _on_scan_started(self) -> None:
        self._state.setText("СКАНИРОВАНИЕ")
        self._state.setStyleSheet(f"color: {theme.WARNING};")
        self._rescan.setEnabled(False)
        self._optimize.setEnabled(False)
        self._explain.setEnabled(False)
        self._set_profile_actions(False)

    def _on_scan_failed(self, message: str) -> None:
        self._state.setText("СБОЙ СКАНИРОВАНИЯ")
        self._state.setStyleSheet(f"color: {theme.CRITICAL};")
        self._status.setText("")
        self._rescan.setEnabled(True)
        QMessageBox.warning(self, "Сбой сканирования", message)

    def _on_scan_completed(self, result: ScanResult) -> None:
        self._result = result
        self._rescan.setEnabled(True)
        self._status.setText("")

        self._populate_metrics(result)
        self._populate_score(result)
        self._populate_issues(result.issues)

        actionable = len(result.actionable_issues)
        if actionable:
            self._state.setText("ТРЕБУЕТСЯ ДЕЙСТВИЕ")
            self._state.setStyleSheet(f"color: {theme.WARNING};")
        else:
            self._state.setText("ГОТОВО")
            self._state.setStyleSheet(f"color: {theme.GOOD};")

        fixable = [i for i in result.actionable_issues if i.fixable]
        self._optimize.setEnabled(bool(fixable))
        self._explain.setEnabled(True)
        self._set_profile_actions(True)

        snapshot = result.snapshot
        self._machine.setText(
            f"{snapshot.os.machine_name} · {snapshot.os.caption} "
            f"(build {snapshot.os.build})"
        )
        self._scan_meta.setText(
            f"Сканирование за {snapshot.scan_duration_s:.1f} с · "
            f"предупреждений: {len(snapshot.warnings)}"
        )

    # -- population --------------------------------------------------------

    def _populate_metrics(self, result: ScanResult) -> None:
        """Render each row from its presenter.

        All formatting lives in ``presenters`` so it can be tested without
        constructing a window.
        """
        for key, widget in self._metrics.items():
            view = (
                presenters.network_view(result.network)
                if key == "network"
                else presenters.METRIC_VIEWS[key](result.snapshot)
            )
            widget.set(view.value, view.status, view.detail, tooltip=view.tooltip)

    def _populate_score(self, result: ScanResult) -> None:
        score = result.score
        colour = theme.score_colour(score.value)
        self._score_value.setText(score.display())
        self._score_value.setStyleSheet(f"color: {colour};")
        self._progress.setValue(int(score.value or 0))
        self._progress.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {colour}; border-radius: 3px; }}"
        )
        if score.excluded:
            self._score_note.setText(
                "Диагностический показатель, не прогноз FPS. "
                f"Исключено: {', '.join(score.excluded)}."
            )

    def _populate_issues(self, issues: tuple[Issue, ...]) -> None:
        self._issues.clear()
        actionable = [i for i in issues if i.status.is_actionable]
        self._issues_heading.setText(
            f"НАХОДКИ — ТРЕБУЮТ ВНИМАНИЯ: {len(actionable)}" if actionable
            else "НАХОДКИ — НЕТ"
        )

        if not issues:
            item = QListWidgetItem("Проблем не найдено.")
            item.setForeground(Qt.GlobalColor.gray)
            self._issues.addItem(item)
            return

        for issue in issues:
            item = QListWidgetItem(f"{issue.title}\n{issue.detail}")
            item.setToolTip(
                issue.fix_hint
                + (
                    f"\n\nThreshold: {issue.threshold_kind.value.lower()}"
                    if issue.threshold_kind
                    else ""
                )
            )
            font = QFont()
            font.setPointSize(9)
            item.setFont(font)
            self._issues.addItem(item)

    # -- actions -----------------------------------------------------------

    def _on_explain(self) -> None:
        if self._result is None:
            return
        score = self._result.score
        body = "\n".join(f"• {line}" for line in score.formula_lines())
        QMessageBox.information(
            self,
            "Как считается оценка готовности",
            f"Оценка: {score.display()}\n\n{body}\n\n"
            "Это диагностический показатель, а не прогноз FPS. Рост "
            "оценки сам по себе не доказывает прирост производительности.",
        )

    def _on_optimize(self) -> None:
        """Build the preview. Nothing is changed until it is accepted."""
        if self._result is None or self._optimizer.busy:
            return
        self._optimize.setEnabled(False)
        self._rescan.setEnabled(False)
        self._status.setText("Планирование изменений…")
        self._optimizer.start_preview(self._result.snapshot, self._result.issues)

    def _on_preview_ready(self, preview: object) -> None:
        """Show the plan and ask for confirmation (rule #39)."""
        self._status.setText("")
        self._rescan.setEnabled(True)
        self._optimize.setEnabled(True)

        dialog = PreviewDialog(preview, self)  # type: ignore[arg-type]
        if dialog.exec() != PreviewDialog.DialogCode.Accepted:
            return
        preview = dialog.selected_preview()  # only what the operator ticked

        self._optimize.setEnabled(False)
        self._rescan.setEnabled(False)
        self._state.setText("ОПТИМИЗАЦИЯ")
        self._state.setStyleSheet(f"color: {theme.WARNING};")
        self._status.setText("Применение изменений…")
        self._optimizer.start_apply(preview)  # type: ignore[arg-type]

    def _on_optimize_finished(self, outcome: object) -> None:
        self._status.setText("")
        self._rescan.setEnabled(True)
        ResultDialog(outcome, self).exec()  # type: ignore[arg-type]
        # Rescan so the dashboard reflects the machine as it is now rather
        # than the state that justified the changes.
        self._controller.start()

    def _on_optimize_failed(self, message: str) -> None:
        self._status.setText("")
        self._rescan.setEnabled(True)
        self._optimize.setEnabled(True)
        self._state.setText("ТРЕБУЕТСЯ ДЕЙСТВИЕ")
        self._state.setStyleSheet(f"color: {theme.WARNING};")
        QMessageBox.warning(self, "Сбой оптимизации", message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # A write in progress must not be interrupted: applying a tweak or
        # removing games between the change and its verification could strand
        # the system half-done, so either one blocks the close outright.
        # Scans and profile reads only read, so closing waits for them to
        # finish (shutdown joins the worker).
        blocker = (
            "Применяются изменения."
            if self._optimizer.applying
            else "Идёт очистка Стима."
            if self._steam.wiping
            else None
        )
        if blocker is not None:
            QMessageBox.warning(
                self, "Операция выполняется",
                f"{blocker} Please wait for it to finish before closing.",
            )
            event.ignore()
            return
        self._controller.shutdown()
        self._optimizer.shutdown()
        self._profiles.shutdown()
        self._steam.shutdown()
        super().closeEvent(event)
