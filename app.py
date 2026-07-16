import sys
import os
import json
import uuid
import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QHeaderView,
    QDateEdit, QComboBox, QMessageBox, QFileDialog, QSplitter,
    QScrollArea, QAbstractItemView, QDialog, QDialogButtonBox,
    QFormLayout, QLineEdit, QTextEdit, QSpinBox, QCheckBox,
    QMenu, QFrame, QListWidget, QListWidgetItem, QSizePolicy,
    QToolButton, QGroupBox, QDoubleSpinBox, QTabWidget
)
from PyQt6.QtCore import Qt, QDate, QRectF, pyqtSignal, QTimer, QPoint, QSize
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QBrush, QPen, QPixmap, QIcon,
    QLinearGradient, QPainterPath
)

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib import colors as rl_colors
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader

# ─────────────────────────────────────────────
# Constants & Paths
# ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_FILE = os.path.join(BASE_DIR, "cronoprogramma_data.json")

STATUS_OPTIONS = ["In corso", "Completata", "Stop", "Non iniziata", "In ritardo"]
PHASE_OPTIONS  = ["Generale", "Scavi", "Strutture", "Impianti", "Finiture", "Collaudi", "Altro"]
STATUS_COLORS  = {
    "In corso":     "#4facfe",
    "Completata":   "#27ae60",
    "Stop":         "#e74c3c",
    "Non iniziata": "#95a5a6",
    "In ritardo":   "#e67e22",
}
PHASE_COLORS = {
    "Generale":   "#4facfe",
    "Scavi":      "#e67e22",
    "Strutture":  "#8e44ad",
    "Impianti":   "#16a085",
    "Finiture":   "#2980b9",
    "Collaudi":   "#c0392b",
    "Altro":      "#7f8c8d",
}

# ─────────────────────────────────────────────
# Data Model
# ─────────────────────────────────────────────
class Task:
    def __init__(self, name, start_date, end_date,
                 status="Non iniziata", task_id=None, phase="Generale",
                 is_pinned=False, predecessors=None, progress=0,
                 notes="", actual_start=None, actual_end=None):
        self.id           = task_id or str(uuid.uuid4())[:8]
        self.name         = name
        self.start_date   = start_date
        self.end_date     = end_date
        self.actual_start = actual_start
        self.actual_end   = actual_end
        self.status       = status
        self.phase        = phase
        self.is_pinned    = is_pinned
        self.predecessors = predecessors or []   # list of task IDs
        self.progress     = progress             # 0-100
        self.notes        = notes

    def duration_days(self):
        return max(1, self.start_date.daysTo(self.end_date))

    def is_overdue(self):
        today = QDate.currentDate()
        return (self.status not in ("Completata", "Stop") and
                self.end_date < today)

    def days_to_deadline(self):
        return QDate.currentDate().daysTo(self.end_date)

    def to_dict(self):
        def qd(d): return d.toString("dd/MM/yyyy") if d and d.isValid() else None
        return {
            "id":           self.id,
            "name":         self.name,
            "start_date":   qd(self.start_date),
            "end_date":     qd(self.end_date),
            "actual_start": qd(self.actual_start),
            "actual_end":   qd(self.actual_end),
            "status":       self.status,
            "phase":        self.phase,
            "is_pinned":    self.is_pinned,
            "predecessors": self.predecessors,
            "progress":     self.progress,
            "notes":        self.notes,
        }

    @classmethod
    def from_dict(cls, data):
        def parse_date(s):
            if not s: return None
            d = QDate.fromString(s, "dd/MM/yyyy")
            return d if d.isValid() else None

        start = parse_date(data.get("start_date")) or QDate(2026, 3, 1)
        end   = parse_date(data.get("end_date"))   or QDate(2026, 3, 15)
        return cls(
            name         = data.get("name", "Attività"),
            start_date   = start,
            end_date     = end,
            actual_start = parse_date(data.get("actual_start")),
            actual_end   = parse_date(data.get("actual_end")),
            status       = data.get("status", "Non iniziata"),
            task_id      = data.get("id"),
            phase        = data.get("phase", "Generale"),
            is_pinned    = data.get("is_pinned", False),
            predecessors = data.get("predecessors", []),
            progress     = data.get("progress", 0),
            notes        = data.get("notes", ""),
        )


# ─────────────────────────────────────────────
# Notification System
# ─────────────────────────────────────────────
class Notification:
    def __init__(self, title, message, level="warning", notif_id=None):
        self.id      = notif_id or str(uuid.uuid4())[:8]
        self.title   = title
        self.message = message
        self.level   = level   # "info" | "warning" | "critical"
        self.read    = False
        self.timestamp = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

    def to_dict(self):
        return {
            "id": self.id, "title": self.title, "message": self.message,
            "level": self.level, "read": self.read, "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data):
        n = cls(data["title"], data["message"], data.get("level","warning"), data.get("id"))
        n.read      = data.get("read", False)
        n.timestamp = data.get("timestamp", "")
        return n


class NotificationManager:
    LEVEL_ICONS = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}
    LEVEL_COLORS = {"info": "#3498db", "warning": "#e67e22", "critical": "#e74c3c"}

    def __init__(self):
        self.notifications: list[Notification] = []

    def generate_from_tasks(self, tasks: list[Task]):
        """Re-generate automatic notifications based on current task state."""
        auto_ids = {n.id for n in self.notifications if n.id.startswith("auto_")}
        self.notifications = [n for n in self.notifications if not n.id.startswith("auto_")]

        task_map = {t.id: t for t in tasks}
        today = QDate.currentDate()

        for task in tasks:
            # 1. Overdue
            if task.is_overdue():
                delta = task.end_date.daysTo(today)
                self._add_auto(
                    f"Ritardo: {task.name}",
                    f"La lavorazione è scaduta da {delta} giorno/i (Fine prevista: {task.end_date.toString('dd/MM/yyyy')}).",
                    "critical", f"auto_overdue_{task.id}"
                )
            # 2. Scadenza imminente (7 giorni)
            elif 0 <= task.days_to_deadline() <= 7 and task.status not in ("Completata","Stop"):
                self._add_auto(
                    f"Scadenza imminente: {task.name}",
                    f"La lavorazione scade il {task.end_date.toString('dd/MM/yyyy')} ({task.days_to_deadline()} giorni rimanenti).",
                    "warning", f"auto_soon_{task.id}"
                )
            # 3. Conflitti di dipendenza
            for pred_id in task.predecessors:
                pred = task_map.get(pred_id)
                if pred and pred.end_date > task.start_date:
                    self._add_auto(
                        f"Conflitto dipendenza: {task.name}",
                        f"«{pred.name}» termina il {pred.end_date.toString('dd/MM/yyyy')}, "
                        f"ma «{task.name}» inizia il {task.start_date.toString('dd/MM/yyyy')}. "
                        f"Verificare il cronoprogramma.",
                        "critical", f"auto_dep_{task.id}_{pred_id}"
                    )
            # 4. Attività pinnata a rischio
            if task.is_pinned:
                for pred_id in task.predecessors:
                    pred = task_map.get(pred_id)
                    if pred and pred.end_date >= task.start_date:
                        self._add_auto(
                            f"📌 Pinnata a rischio: {task.name}",
                            f"La data fissa di {task.start_date.toString('dd/MM/yyyy')} è compromessa "
                            f"dal ritardo di «{pred.name}».",
                            "critical", f"auto_pinned_{task.id}_{pred_id}"
                        )

    def _add_auto(self, title, message, level, notif_id):
        if not any(n.id == notif_id for n in self.notifications):
            n = Notification(title, message, level, notif_id)
            self.notifications.append(n)

    def unread_count(self):
        return sum(1 for n in self.notifications if not n.read)

    def mark_all_read(self):
        for n in self.notifications:
            n.read = True

    def clear_read(self):
        self.notifications = [n for n in self.notifications if not n.read]


# ─────────────────────────────────────────────
# Gantt Widget
# ─────────────────────────────────────────────
MONTHS_IT = ["Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic", "Gen"]

class GanttWidget(QWidget):
    orderChanged = pyqtSignal()
    taskClicked  = pyqtSignal(str)   # emits task id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tasks: list[Task] = []
        self.start_year    = 2026
        self.timeline_start = QDate(self.start_year, 3, 1)
        self.timeline_end   = QDate(self.start_year + 1, 2, 1)
        self.total_days     = self.timeline_start.daysTo(self.timeline_end)

        self.setMinimumSize(900, 400)
        self.dragged_index = None
        self.drag_start_y  = 0
        self.current_y     = 0
        self.header_height = 50
        self.row_height    = 48
        self.left_margin   = 280

    def set_tasks(self, tasks):
        self.tasks = tasks
        self.start_year     = self._compute_start_year()
        self.timeline_start = QDate(self.start_year, 3, 1)
        self.timeline_end   = QDate(self.start_year + 1, 2, 1)
        self.total_days     = self.timeline_start.daysTo(self.timeline_end)
        min_h = max(400, len(tasks) * self.row_height + self.header_height + 60)
        self.setMinimumHeight(min_h)
        self.update()

    def _compute_start_year(self):
        if not self.tasks: return 2026
        return min(t.start_date.year() for t in self.tasks)

    def _pixels_per_day(self):
        w = self.width() - self.left_margin - 20
        return w / max(1, self.total_days)

    def _bar_x(self, date):
        return self.left_margin + self.timeline_start.daysTo(date) * self._pixels_per_day()

    # ── Mouse ──────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            y = event.pos().y()
            if y > self.header_height:
                idx = int((y - self.header_height - 4) / self.row_height)
                if 0 <= idx < len(self.tasks):
                    self.dragged_index = idx
                    self.drag_start_y  = y
                    self.current_y     = y
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        if self.dragged_index is not None:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            y = event.pos().y()
            new_idx = int((y - self.header_height - 4) / self.row_height)
            new_idx = max(0, min(new_idx, len(self.tasks) - 1))
            if new_idx != self.dragged_index:
                task = self.tasks.pop(self.dragged_index)
                self.tasks.insert(new_idx, task)
                self.orderChanged.emit()
            self.dragged_index = None
            self.update()

    def mouseMoveEvent(self, event):
        if self.dragged_index is not None:
            self.current_y = event.pos().y()
            self.update()

    def mouseDoubleClickEvent(self, event):
        y = event.pos().y()
        if y > self.header_height:
            idx = int((y - self.header_height - 4) / self.row_height)
            if 0 <= idx < len(self.tasks):
                self.taskClicked.emit(self.tasks[idx].id)

    # ── Paint ──────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        ppd  = self._pixels_per_day()

        # Background
        painter.fillRect(0, 0, W, H, QColor("#f8f9fa"))

        # Left panel background
        painter.fillRect(0, 0, self.left_margin, H, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#e0e0e0"), 1))
        painter.drawLine(self.left_margin, 0, self.left_margin, H)

        # Header bg
        painter.fillRect(0, 0, W, self.header_height, QColor(TOPBAR_COLOR))

        # Month columns
        for i, month in enumerate(MONTHS_IT):
            x = int(self.left_margin + i * (W - self.left_margin - 20) / len(MONTHS_IT))
            # month label
            painter.setPen(QPen(QColor("#ecf0f1"), 1))
            painter.setFont(QFont("Inter", 10, QFont.Weight.Bold))
            painter.drawText(x, 0, int((W - self.left_margin - 20) / len(MONTHS_IT)),
                             self.header_height, Qt.AlignmentFlag.AlignCenter, month)
            # grid line
            painter.setPen(QPen(QColor("#dee2e6"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(x, self.header_height, x, H)

        # Today line
        today = QDate.currentDate()
        today_x = int(self._bar_x(today))
        if self.left_margin < today_x < W - 20:
            painter.setPen(QPen(QColor("#e74c3c"), 2))
            painter.drawLine(today_x, self.header_height, today_x, H)
            painter.setFont(QFont("Inter", 7))
            painter.setPen(QColor("#e74c3c"))
            painter.drawText(today_x + 2, self.header_height + 12, "Oggi")

        # Draw dependency arrows first (behind bars)
        task_map = {t.id: t for t in self.tasks}
        task_y_center = {t.id: self.header_height + 4 + i * self.row_height + self.row_height // 2
                         for i, t in enumerate(self.tasks)}
        painter.setPen(QPen(QColor("#c0392b"), 1.5, Qt.PenStyle.DashLine))
        for task in self.tasks:
            for pred_id in task.predecessors:
                pred = task_map.get(pred_id)
                if pred:
                    x1 = int(self._bar_x(pred.end_date))
                    y1 = task_y_center.get(pred_id, 0)
                    x2 = int(self._bar_x(task.start_date))
                    y2 = task_y_center.get(task.id, 0)
                    painter.drawLine(x1, y1, x2, y2)
                    # arrowhead
                    painter.setBrush(QColor("#c0392b"))
                    painter.setPen(Qt.PenStyle.NoPen)
                    path = QPainterPath()
                    path.moveTo(x2, y2)
                    path.lineTo(x2 - 8, y2 - 4)
                    path.lineTo(x2 - 8, y2 + 4)
                    path.closeSubpath()
                    painter.drawPath(path)
                    painter.setPen(QPen(QColor("#c0392b"), 1.5, Qt.PenStyle.DashLine))

        # Draw tasks
        for i, task in enumerate(self.tasks):
            if i == self.dragged_index:
                continue
            y = self.header_height + 4 + i * self.row_height
            self._draw_task_row(painter, task, i, y, ppd)

        # Dragged task
        if self.dragged_index is not None:
            task = self.tasks[self.dragged_index]
            y = (self.header_height + 4 + self.dragged_index * self.row_height
                 + (self.current_y - self.drag_start_y))
            painter.fillRect(0, int(y), W, self.row_height, QColor(0, 0, 0, 15))
            self._draw_task_row(painter, task, self.dragged_index, int(y), ppd)

    def _draw_task_row(self, painter, task, idx, y, ppd):
        # Row alternating background
        if idx % 2 == 0:
            painter.fillRect(0, y, self.left_margin, self.row_height, QColor("#fafafa"))

        # Task name (left panel)
        phase_color = QColor(PHASE_COLORS.get(task.phase, "#4facfe"))
        painter.fillRect(0, y, 4, self.row_height, phase_color)

        painter.setPen(QPen(QColor("#1a1a1a"), 1))
        painter.setFont(QFont("Inter", 9, QFont.Weight.Bold if task.is_pinned else QFont.Weight.Normal))
        name_rect_y = y + (self.row_height - 32) // 2
        painter.drawText(10, name_rect_y, self.left_margin - 16, 18,
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, task.name)

        # Phase tag
        painter.setFont(QFont("Inter", 7))
        painter.setPen(phase_color)
        painter.drawText(10, name_rect_y + 18, self.left_margin - 16, 12,
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, task.phase)

        # Pin badge
        if task.is_pinned:
            painter.setFont(QFont("Inter", 10))
            painter.setPen(QColor("#e74c3c"))
            painter.drawText(self.left_margin - 26, y, 20, self.row_height,
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter, "📌")

        # ── Gantt bar (planned) ──────────────────
        days_from_start = self.timeline_start.daysTo(task.start_date)
        duration        = task.duration_days()
        bar_x  = self.left_margin + days_from_start * ppd
        bar_w  = max(4.0, duration * ppd)
        bar_y  = y + 10
        bar_h  = self.row_height - 24

        base_color = QColor(STATUS_COLORS.get(task.status, "#4facfe"))
        painter.setBrush(QBrush(base_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 4.0, 4.0)

        # Progress fill (darker overlay)
        if task.progress > 0:
            prog_w = bar_w * task.progress / 100
            dark = base_color.darker(140)
            painter.setBrush(QBrush(dark))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, prog_w, bar_h), 4.0, 4.0)

        # Progress % label
        if task.progress > 0:
            painter.setFont(QFont("Inter", 7, QFont.Weight.Bold))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(bar_x, bar_y, bar_w, bar_h),
                             Qt.AlignmentFlag.AlignCenter, f"{task.progress}%")

        # ── Actual bar (actual dates) ─────────────
        if task.actual_start and task.actual_start.isValid():
            act_end = task.actual_end if (task.actual_end and task.actual_end.isValid()) else QDate.currentDate()
            act_days_from_start = self.timeline_start.daysTo(task.actual_start)
            act_duration = max(1, task.actual_start.daysTo(act_end))
            act_x = self.left_margin + act_days_from_start * ppd
            act_w = max(4.0, act_duration * ppd)
            act_color = QColor(255, 165, 0, 140)  # semi-transparent orange
            painter.setBrush(QBrush(act_color))
            painter.setPen(QPen(QColor(200, 120, 0), 1))
            painter.drawRoundedRect(QRectF(act_x, bar_y + bar_h, act_w, 5), 2.0, 2.0)

        # Date label
        painter.setFont(QFont("Inter", 7))
        painter.setPen(QColor("#555"))
        dates_text = f"{task.start_date.toString('dd/MM')} – {task.end_date.toString('dd/MM')}"
        painter.drawText(QRectF(bar_x, bar_y + bar_h + 6, max(80.0, bar_w), 12),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, dates_text)

        # Row separator
        painter.setPen(QPen(QColor("#eeeeee"), 1))
        painter.drawLine(0, y + self.row_height - 1, self.width(), y + self.row_height - 1)


# ─────────────────────────────────────────────
# Settings Dialog
# ─────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, project_config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ Impostazioni Progetto")
        self.setMinimumWidth(520)
        self.config = dict(project_config)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Impostazioni Progetto")
        title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #2c3e50; margin-bottom: 8px;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.company_edit  = QLineEdit(self.config.get("company_name", ""))
        self.subtitle_edit = QLineEdit(self.config.get("subtitle", ""))
        self.location_edit = QLineEdit(self.config.get("location", ""))
        self.address_edit  = QLineEdit(self.config.get("address", ""))
        self.client_edit   = QLineEdit(self.config.get("client", ""))
        self.year_spin     = QSpinBox()
        self.year_spin.setRange(2020, 2040)
        self.year_spin.setValue(self.config.get("start_year", 2026))

        # Logo row
        logo_row = QHBoxLayout()
        self.logo_label = QLabel(self.config.get("logo_path", "") or "Nessun logo selezionato")
        self.logo_label.setStyleSheet("color: #666; font-size: 11px;")
        btn_logo = QPushButton("Scegli Logo…")
        btn_logo.clicked.connect(self._pick_logo)
        btn_logo.setStyleSheet(STYLE_BTN_SECONDARY)
        logo_row.addWidget(self.logo_label)
        logo_row.addWidget(btn_logo)

        form.addRow("🏢 Nome Azienda:", self.company_edit)
        form.addRow("📋 Descrizione lavori:", self.subtitle_edit)
        form.addRow("📍 Luogo:", self.location_edit)
        form.addRow("🛣 Via / Indirizzo:", self.address_edit)
        form.addRow("👤 Committente:", self.client_edit)
        form.addRow("📅 Anno inizio:", self.year_spin)

        logo_group = QGroupBox("Logo Azienda")
        logo_group.setLayout(logo_row)
        logo_group.setStyleSheet("QGroupBox { font-weight: bold; color: #2c3e50; }")

        layout.addLayout(form)
        layout.addWidget(logo_group)
        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Salva")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setStyleSheet(STYLE_BTN_PRIMARY)
        layout.addWidget(buttons)

        self.setStyleSheet(GLOBAL_STYLE)

    def _pick_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Scegli Logo", "",
                                              "Immagini (*.png *.jpg *.jpeg *.svg)")
        if path:
            self.config["logo_path"] = path
            self.logo_label.setText(os.path.basename(path))

    def _save_and_accept(self):
        self.config["company_name"] = self.company_edit.text().strip()
        self.config["subtitle"]     = self.subtitle_edit.text().strip()
        self.config["location"]     = self.location_edit.text().strip()
        self.config["address"]      = self.address_edit.text().strip()
        self.config["client"]       = self.client_edit.text().strip()
        self.config["start_year"]   = self.year_spin.value()
        self.accept()


# ─────────────────────────────────────────────
# Task Detail Dialog
# ─────────────────────────────────────────────
class TaskDetailDialog(QDialog):
    def __init__(self, task: Task, all_tasks: list[Task], parent=None):
        super().__init__(parent)
        self.task = task
        self.all_tasks = all_tasks
        self.setWindowTitle(f"Dettaglio Attività – {task.name}")
        self.setMinimumWidth(600)
        self.setMinimumHeight(540)
        self.setStyleSheet(GLOBAL_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(12)

        title = QLabel("✏️ Modifica Attività")
        title.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        title.setStyleSheet("color: #2c3e50;")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_main_tab(), "Generale")
        tabs.addTab(self._build_dates_tab(), "Date Effettive")
        tabs.addTab(self._build_deps_tab(), "Dipendenze")
        tabs.addTab(self._build_notes_tab(), "Note")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Salva")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setStyleSheet(STYLE_BTN_PRIMARY)
        layout.addWidget(buttons)

    def _build_main_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(12, 12, 12, 12)

        self.name_edit = QLineEdit(self.task.name)
        self.phase_combo = QComboBox()
        self.phase_combo.addItems(PHASE_OPTIONS)
        self.phase_combo.setCurrentText(self.task.phase)
        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUS_OPTIONS)
        self.status_combo.setCurrentText(self.task.status)
        self.start_edit = QDateEdit(self.task.start_date)
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("dd/MM/yyyy")
        self.end_edit = QDateEdit(self.task.end_date)
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("dd/MM/yyyy")
        self.pinned_check = QCheckBox("Attività Pinnata (date fisse inamovibili)")
        self.pinned_check.setChecked(self.task.is_pinned)
        self.progress_spin = QSpinBox()
        self.progress_spin.setRange(0, 100)
        self.progress_spin.setValue(self.task.progress)
        self.progress_spin.setSuffix(" %")

        form.addRow("📌 Nome:", self.name_edit)
        form.addRow("🏷 Fase:", self.phase_combo)
        form.addRow("📊 Stato:", self.status_combo)
        form.addRow("📅 Inizio Previsto:", self.start_edit)
        form.addRow("📅 Fine Prevista:", self.end_edit)
        form.addRow("⚙️ Avanzamento:", self.progress_spin)
        form.addRow("", self.pinned_check)
        return w

    def _build_dates_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(12, 12, 12, 12)

        info = QLabel("Inserisci qui le date effettive di inizio e fine (a consuntivo).\n"
                      "Vengono visualizzate nel Gantt con una barra arancione sotto quella pianificata.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 8px;")
        form.addRow(info)

        self.actual_start_check = QCheckBox("Inizio effettivo:")
        self.actual_start_edit  = QDateEdit(self.task.actual_start or QDate.currentDate())
        self.actual_start_edit.setCalendarPopup(True)
        self.actual_start_edit.setDisplayFormat("dd/MM/yyyy")
        self.actual_start_edit.setEnabled(self.task.actual_start is not None)
        self.actual_start_check.setChecked(self.task.actual_start is not None)
        self.actual_start_check.toggled.connect(self.actual_start_edit.setEnabled)

        self.actual_end_check = QCheckBox("Fine effettiva:")
        self.actual_end_edit  = QDateEdit(self.task.actual_end or QDate.currentDate())
        self.actual_end_edit.setCalendarPopup(True)
        self.actual_end_edit.setDisplayFormat("dd/MM/yyyy")
        self.actual_end_edit.setEnabled(self.task.actual_end is not None)
        self.actual_end_check.setChecked(self.task.actual_end is not None)
        self.actual_end_check.toggled.connect(self.actual_end_edit.setEnabled)

        form.addRow(self.actual_start_check, self.actual_start_edit)
        form.addRow(self.actual_end_check,   self.actual_end_edit)

        # Scarto
        if self.task.actual_end and self.task.actual_end.isValid():
            delta = self.task.end_date.daysTo(self.task.actual_end)
            color = "#e74c3c" if delta > 0 else "#27ae60"
            lbl = QLabel(f"Scarto: {'+' if delta>0 else ''}{delta} giorni rispetto al previsto")
            lbl.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 12px;")
            form.addRow(lbl)

        return w

    def _build_deps_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)

        lbl = QLabel("Seleziona le attività che devono essere completate PRIMA di questa "
                     "(attività propedeutiche):")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #444; margin-bottom: 6px;")
        layout.addWidget(lbl)

        self.deps_list = QListWidget()
        self.deps_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for t in self.all_tasks:
            if t.id == self.task.id:
                continue
            item = QListWidgetItem(f"{t.name}  [{t.start_date.toString('dd/MM')} – {t.end_date.toString('dd/MM')}]")
            item.setData(Qt.ItemDataRole.UserRole, t.id)
            self.deps_list.addItem(item)
            if t.id in self.task.predecessors:
                item.setSelected(True)

        layout.addWidget(self.deps_list)
        return w

    def _build_notes_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        self.notes_edit = QTextEdit(self.task.notes)
        self.notes_edit.setPlaceholderText("Inserisci note, osservazioni o riferimenti per questa attività…")
        layout.addWidget(self.notes_edit)
        return w

    def _save(self):
        self.task.name     = self.name_edit.text().strip() or self.task.name
        self.task.phase    = self.phase_combo.currentText()
        self.task.status   = self.status_combo.currentText()
        self.task.start_date = self.start_edit.date()
        self.task.end_date   = self.end_edit.date()
        self.task.is_pinned  = self.pinned_check.isChecked()
        self.task.progress   = self.progress_spin.value()
        self.task.notes      = self.notes_edit.toPlainText()
        self.task.actual_start = self.actual_start_edit.date() if self.actual_start_check.isChecked() else None
        self.task.actual_end   = self.actual_end_edit.date()   if self.actual_end_check.isChecked()   else None
        selected = self.deps_list.selectedItems()
        self.task.predecessors = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        self.accept()


# ─────────────────────────────────────────────
# Notification Panel (popup)
# ─────────────────────────────────────────────
class NotificationPanel(QFrame):
    closed = pyqtSignal()

    def __init__(self, manager: NotificationManager, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.manager = manager
        self.setFixedWidth(380)
        self.setStyleSheet("""
            QFrame { background: #ffffff; border: 1px solid #dee2e6;
                     border-radius: 12px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setStyleSheet(f"background: {TOPBAR_COLOR}; border-radius: 12px 12px 0 0;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(16, 12, 12, 12)
        lbl = QLabel("🔔 Notifiche")
        lbl.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        lbl.setStyleSheet("color: white;")
        btn_clear = QPushButton("Segna tutte come lette")
        btn_clear.setStyleSheet("background: transparent; color: #adb5bd; font-size: 11px; border: none;")
        btn_clear.clicked.connect(self._mark_all_read)
        h_lay.addWidget(lbl)
        h_lay.addStretch()
        h_lay.addWidget(btn_clear)
        layout.addWidget(header)

        # Scroll content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        scroll.setWidget(self.content_widget)
        layout.addWidget(scroll)

        max_h = min(480, 80 + max(1, len(manager.notifications)) * 72)
        self.setFixedHeight(max_h + 56)
        self._populate()

    def _populate(self):
        for i in reversed(range(self.content_layout.count())):
            self.content_layout.itemAt(i).widget().deleteLater()

        level_colors = NotificationManager.LEVEL_COLORS
        level_icons  = NotificationManager.LEVEL_ICONS

        if not self.manager.notifications:
            lbl = QLabel("✅ Nessuna notifica attiva")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #adb5bd; padding: 24px; font-size: 13px;")
            self.content_layout.addWidget(lbl)
            return

        for n in reversed(self.manager.notifications):
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{ background: {'#f8f9fa' if n.read else '#fff3cd'};
                          border-bottom: 1px solid #f0f0f0; }}
            """)
            c_lay = QHBoxLayout(card)
            c_lay.setContentsMargins(12, 10, 12, 10)

            icon_lbl = QLabel(level_icons.get(n.level, "ℹ️"))
            icon_lbl.setFont(QFont("Inter", 18))
            icon_lbl.setFixedWidth(30)

            text_col = QVBoxLayout()
            t_lbl = QLabel(n.title)
            t_lbl.setFont(QFont("Inter", 10, QFont.Weight.Bold))
            t_lbl.setStyleSheet(f"color: {level_colors.get(n.level,'#333')};")
            m_lbl = QLabel(n.message)
            m_lbl.setWordWrap(True)
            m_lbl.setFont(QFont("Inter", 9))
            m_lbl.setStyleSheet("color: #555;")
            ts_lbl = QLabel(n.timestamp)
            ts_lbl.setFont(QFont("Inter", 8))
            ts_lbl.setStyleSheet("color: #aaa;")
            text_col.addWidget(t_lbl)
            text_col.addWidget(m_lbl)
            text_col.addWidget(ts_lbl)

            btn_read = QPushButton("✓")
            btn_read.setFixedSize(28, 28)
            btn_read.setToolTip("Segna come letta")
            btn_read.setStyleSheet("background: #dee2e6; border-radius: 14px; border: none; color: #555;")
            btn_read.clicked.connect(lambda _, nid=n.id: self._mark_read(nid))

            c_lay.addWidget(icon_lbl)
            c_lay.addLayout(text_col, 1)
            c_lay.addWidget(btn_read)
            self.content_layout.addWidget(card)

        self.content_layout.addStretch()

    def _mark_read(self, notif_id):
        for n in self.manager.notifications:
            if n.id == notif_id:
                n.read = True
        self._populate()
        self.closed.emit()  # trigger bell update

    def _mark_all_read(self):
        self.manager.mark_all_read()
        self._populate()
        self.closed.emit()


# ─────────────────────────────────────────────
# Styles
# ─────────────────────────────────────────────
# Topbar color: #4facfe (Gantt blue) with luminosity -30% → #3778b2
TOPBAR_COLOR = "#3778b2"

GLOBAL_STYLE = f"""
    QWidget {{ font-family: 'Inter', 'Segoe UI', sans-serif; font-size: 13px; }}
    QDialog {{ background-color: #f8f9fa; }}
    QTabWidget::pane {{ border: 1px solid #dee2e6; border-radius: 6px; background: white; }}
    QTabBar::tab {{ background: #f0f0f0; padding: 8px 16px; border-radius: 4px 4px 0 0; margin-right: 2px; }}
    QTabBar::tab:selected {{ background: white; font-weight: bold; color: {TOPBAR_COLOR}; }}
    QLineEdit, QTextEdit, QSpinBox, QComboBox, QDateEdit {{
        border: 1px solid #dee2e6; border-radius: 6px; padding: 6px 10px;
        background: white; color: #1a1a1a;
    }}
    QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDateEdit:focus {{
        border-color: #4facfe;
    }}
"""
STYLE_BTN_PRIMARY = f"""
    QPushButton {{ background-color: {TOPBAR_COLOR}; color: white; border: none;
                  padding: 9px 20px; border-radius: 7px; font-weight: bold; }}
    QPushButton:hover {{ background-color: #2d68a0; }}
"""
STYLE_BTN_SECONDARY = """
    QPushButton { background-color: #ecf0f1; color: #1a1a1a; border: 1px solid #dee2e6;
                  padding: 6px 14px; border-radius: 7px; }
    QPushButton:hover { background-color: #dfe6e9; }
"""
STYLE_BTN_DANGER = """
    QPushButton { background-color: #e74c3c; color: white; border: none;
                  padding: 9px 20px; border-radius: 7px; font-weight: bold; }
    QPushButton:hover { background-color: #c0392b; }
"""
STYLE_BTN_SUCCESS = """
    QPushButton { background-color: #27ae60; color: white; border: none;
                  padding: 9px 20px; border-radius: 7px; font-weight: bold; }
    QPushButton:hover { background-color: #219a52; }
"""


# ─────────────────────────────────────────────
# Bell Widget (bell icon + overlaid badge)
# ─────────────────────────────────────────────
class BellWidget(QWidget):
    """Bell button with a red badge overlaid at top-right corner."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(48, 48)

        # Bell button fills the widget
        self.btn = QToolButton(self)
        self.btn.setText("🔔")
        self.btn.setFont(QFont("Inter", 20))
        self.btn.setFixedSize(48, 48)
        self.btn.setStyleSheet("""
            QToolButton { background: transparent; border: none; color: white; }
            QToolButton:hover { color: #f39c12; }
        """)
        self.btn.clicked.connect(self.clicked)

        # Badge overlaid at top-right
        self.badge = QLabel("0", self)
        self.badge.setFixedSize(18, 18)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setStyleSheet("""
            QLabel {
                background-color: #e74c3c;
                color: white;
                border-radius: 9px;
                font-size: 9px;
                font-weight: bold;
                border: 2px solid #3778b2;
            }
        """)
        self.badge.move(28, 2)   # top-right overlay
        self.badge.hide()
        self.badge.raise_()

    def set_count(self, count: int):
        if count > 0:
            self.badge.setText(str(min(count, 99)))
            self.badge.show()
            self.badge.raise_()
        else:
            self.badge.hide()


# ─────────────────────────────────────────────
# Main Window
# ─────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.tasks: list[Task] = []
        self.project: dict = {}
        self.notif_manager = NotificationManager()
        self.is_loading = False

        self.load_data()
        self._build_ui()
        self._refresh_all()

        # Auto-check notifications every 60 seconds
        self._notif_timer = QTimer(self)
        self._notif_timer.timeout.connect(self._refresh_notifications)
        self._notif_timer.start(60_000)

    # ── Data I/O ───────────────────────────────
    def load_data(self):
        if os.path.exists(SAVE_FILE):
            try:
                with open(SAVE_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    # Legacy format
                    self.tasks   = [Task.from_dict(t) for t in raw]
                    self.project = self._default_project()
                else:
                    self.project = raw.get("project", self._default_project())
                    self.tasks   = [Task.from_dict(t) for t in raw.get("tasks", [])]
                    notifs_raw   = raw.get("notifications", [])
                    self.notif_manager.notifications = [Notification.from_dict(n) for n in notifs_raw]
                return
            except Exception as e:
                print(f"Load error: {e}")
        self.project = self._default_project()
        self._init_default_tasks()

    def save_data(self):
        if self.is_loading: return
        try:
            self.notif_manager.generate_from_tasks(self.tasks)
            payload = {
                "project":       self.project,
                "tasks":         [t.to_dict() for t in self.tasks],
                "notifications": [n.to_dict() for n in self.notif_manager.notifications],
            }
            with open(SAVE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Save error: {e}")

    def _default_project(self):
        return {
            "company_name": "SBR Costruzioni Generali spa",
            "subtitle":     "Rinnovo impianto di spinta acque reflue Gennarini Taranto",
            "location":     "Gennarini, Taranto",
            "address":      "",
            "client":       "",
            "logo_path":    "",
            "start_year":   2026,
        }

    def _init_default_tasks(self):
        y = 2026
        self.tasks = [
            Task("PULIZIA AREA CANTIERE",    QDate(y,3,1),  QDate(y,4,1),  phase="Generale"),
            Task("BONIFICA BELLICA",          QDate(y,3,15), QDate(y,4,15), phase="Generale"),
            Task("VASCHE + BASAMENTO T.O.C.", QDate(y,4,1),  QDate(y,4,30), phase="Strutture"),
            Task("STOP LAVORI",               QDate(y,8,15), QDate(y,8,25), status="Stop", phase="Generale"),
            Task("TIRO TUBO (120 m/giorno)",  QDate(y,8,14), QDate(y,8,25), phase="Impianti"),
            Task("COSTRUZIONE CAMERETTA",     QDate(y,8,25), QDate(y,9,15), phase="Strutture"),
            Task("MICROTUNNELING",            QDate(y,9,15), QDate(y,10,15),phase="Scavi"),
            Task("POSA TUBO MICROTUNNELING",  QDate(y,9,15), QDate(y,10,30),phase="Impianti"),
            Task("POSA TUBO A MARE",          QDate(y,10,25),QDate(y+1,1,15),phase="Impianti"),
            Task("COMPLETAMENTI E CHIUSURA",  QDate(y+1,1,15),QDate(y+1,1,30),phase="Collaudi",
                 is_pinned=True),
        ]

    # ── UI Builder ─────────────────────────────
    def _build_ui(self):
        self.setMinimumSize(1280, 800)
        self.setStyleSheet(f"QMainWindow {{ background-color: #f0f2f5; }}" + GLOBAL_STYLE)

        root = QWidget()
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)
        self.setCentralWidget(root)

        # ── Top Bar ──────────────────────────────
        topbar = QWidget()
        topbar.setFixedHeight(72)
        topbar.setStyleSheet(f"background: {TOPBAR_COLOR}; border-bottom: 2px solid #2d68a0;")
        tb_lay = QHBoxLayout(topbar)
        tb_lay.setContentsMargins(24, 0, 24, 0)

        self.title_lbl    = QLabel()
        self.subtitle_lbl = QLabel()
        self.title_lbl.setFont(QFont("Inter", 18, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet("color: white;")
        self.subtitle_lbl.setFont(QFont("Inter", 11))
        self.subtitle_lbl.setStyleSheet("color: #adb5bd;")

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(self.title_lbl)
        title_col.addWidget(self.subtitle_lbl)

        # Bell widget (with overlaid badge)
        self.bell_widget = BellWidget()
        self.bell_widget.clicked.connect(self._show_notifications)

        # Settings button
        btn_settings = QToolButton()
        btn_settings.setText("⚙️")
        btn_settings.setFont(QFont("Inter", 20))
        btn_settings.setFixedSize(48, 48)
        btn_settings.setStyleSheet("""
            QToolButton { background: transparent; border: none; color: white; }
            QToolButton:hover { color: #a8d8ff; }
        """)
        btn_settings.clicked.connect(self._open_settings)

        tb_lay.addLayout(title_col, 1)
        tb_lay.addWidget(self.bell_widget)
        tb_lay.addWidget(btn_settings)
        root_lay.addWidget(topbar)

        # ── Action Bar ───────────────────────────
        actionbar = QWidget()
        actionbar.setStyleSheet("background: white; border-bottom: 1px solid #dee2e6;")
        ab_lay = QHBoxLayout(actionbar)
        ab_lay.setContentsMargins(16, 10, 16, 10)
        ab_lay.setSpacing(10)

        btn_add  = QPushButton("＋ Aggiungi Attività")
        btn_del  = QPushButton("🗑 Elimina")
        btn_edit = QPushButton("✏️ Modifica")
        btn_pdf  = QPushButton("⬇ Esporta PDF")

        btn_add.setStyleSheet(STYLE_BTN_PRIMARY)
        btn_edit.setStyleSheet(STYLE_BTN_SECONDARY)
        btn_del.setStyleSheet(STYLE_BTN_DANGER)
        btn_pdf.setStyleSheet(STYLE_BTN_SUCCESS)

        for btn in [btn_add, btn_del, btn_edit, btn_pdf]:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ab_lay.addWidget(btn)

        ab_lay.addStretch()

        # Filter / Search
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Cerca attività…")
        self.search_edit.setFixedWidth(200)
        self.search_edit.textChanged.connect(self._apply_filter)

        self.filter_phase = QComboBox()
        self.filter_phase.addItem("Tutte le fasi")
        self.filter_phase.addItems(PHASE_OPTIONS)
        self.filter_phase.currentTextChanged.connect(self._apply_filter)

        self.filter_status = QComboBox()
        self.filter_status.addItem("Tutti gli stati")
        self.filter_status.addItems(STATUS_OPTIONS)
        self.filter_status.currentTextChanged.connect(self._apply_filter)

        ab_lay.addWidget(self.search_edit)
        ab_lay.addWidget(self.filter_phase)
        ab_lay.addWidget(self.filter_status)

        root_lay.addWidget(actionbar)

        # Connect actions
        btn_add.clicked.connect(self._add_task)
        btn_del.clicked.connect(self._delete_task)
        btn_edit.clicked.connect(self._edit_selected)
        btn_pdf.clicked.connect(self._export_pdf)

        # ── Main Content ─────────────────────────
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(16, 12, 16, 12)
        content_lay.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Table
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "Attività", "Fase", "Inizio Prev.", "Fine Prev.",
            "Inizio Eff.", "Fine Eff.", "Stato", "Avanzo", "📌"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._edit_selected)
        self.table.setStyleSheet("""
            QTableWidget { border: none; background: white; gridline-color: #f0f0f0; }
            QHeaderView::section { background: #f8f9fa; padding: 8px; border: none;
                                   border-bottom: 2px solid #dee2e6; font-weight: bold;
                                   color: #2c3e50; }
            QTableWidget::item { padding: 6px; }
            QTableWidget::item:selected { background: #d5e8f8; color: #1a1a1a; }
        """)
        self.table.setAlternatingRowColors(True)
        splitter.addWidget(self.table)

        # Gantt
        self.gantt = GanttWidget()
        self.gantt.orderChanged.connect(self._on_gantt_reorder)
        self.gantt.taskClicked.connect(self._edit_task_by_id)

        gantt_scroll = QScrollArea()
        gantt_scroll.setWidgetResizable(True)
        gantt_scroll.setWidget(self.gantt)
        gantt_scroll.setStyleSheet("QScrollArea { border: none; background: white; }")
        splitter.addWidget(gantt_scroll)
        splitter.setSizes([280, 500])

        content_lay.addWidget(splitter)
        root_lay.addWidget(content, 1)

        # ── Footer / Credits ─────────────────────
        footer = QWidget()
        footer.setFixedHeight(24)
        footer.setStyleSheet(f"background: {TOPBAR_COLOR};")
        footer_lay = QHBoxLayout(footer)
        footer_lay.setContentsMargins(16, 0, 16, 0)
        copyright_lbl = QLabel("Copyright © Giuseppe Lobbene Design 2004")
        copyright_lbl.setFont(QFont("Inter", 9))
        copyright_lbl.setStyleSheet("color: rgba(255,255,255,0.65);")
        footer_lay.addStretch()
        footer_lay.addWidget(copyright_lbl)
        root_lay.addWidget(footer)

    # ── Refresh ────────────────────────────────
    def _refresh_all(self):
        self._update_header()
        self._refresh_table()
        self._refresh_notifications()

    def _update_header(self):
        p = self.project
        self.setWindowTitle(f"Cronoprogramma – {p.get('company_name','')}")
        self.title_lbl.setText(p.get("company_name", ""))
        parts = [x for x in [p.get("subtitle",""), p.get("location",""), p.get("client","")] if x]
        self.subtitle_lbl.setText("  |  ".join(parts))

    def _refresh_table(self):
        self.is_loading = True
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        search = self.search_edit.text().lower()
        phase_f  = self.filter_phase.currentText()
        status_f = self.filter_status.currentText()

        for task in self.tasks:
            if search and search not in task.name.lower():
                continue
            if phase_f  != "Tutte le fasi"  and task.phase  != phase_f:
                continue
            if status_f != "Tutti gli stati" and task.status != status_f:
                continue

            row = self.table.rowCount()
            self.table.insertRow(row)

            def cell(text, align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(align)
                return item

            self.table.setItem(row, 0, cell(task.name))
            phase_item = cell(task.phase)
            phase_color = QColor(PHASE_COLORS.get(task.phase, "#4facfe"))
            phase_color.setAlpha(40)
            phase_item.setBackground(phase_color)
            self.table.setItem(row, 1, phase_item)

            self.table.setItem(row, 2, cell(task.start_date.toString("dd/MM/yyyy"),
                                            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter))
            self.table.setItem(row, 3, cell(task.end_date.toString("dd/MM/yyyy"),
                                            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter))

            as_text = task.actual_start.toString("dd/MM/yyyy") if (task.actual_start and task.actual_start.isValid()) else "—"
            ae_text = task.actual_end.toString("dd/MM/yyyy")   if (task.actual_end   and task.actual_end.isValid())   else "—"
            self.table.setItem(row, 4, cell(as_text, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter))
            self.table.setItem(row, 5, cell(ae_text, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter))

            status_item = cell(task.status, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            sc = QColor(STATUS_COLORS.get(task.status, "#4facfe"))
            sc.setAlpha(50)
            status_item.setBackground(sc)
            self.table.setItem(row, 6, status_item)

            prog_item = cell(f"{task.progress}%", Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 7, prog_item)

            pin_item = cell("📌" if task.is_pinned else "", Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 8, pin_item)

            # Store task id
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, task.id)

            # Highlight overdue rows
            if task.is_overdue():
                for col in range(self.table.columnCount()):
                    itm = self.table.item(row, col)
                    if itm:
                        itm.setForeground(QColor("#e74c3c"))

        self.table.setRowHeight(0, 36)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.blockSignals(False)
        self.is_loading = False

        self.gantt.set_tasks(self.tasks)

    def _refresh_notifications(self):
        self.notif_manager.generate_from_tasks(self.tasks)
        count = self.notif_manager.unread_count()
        self.bell_widget.set_count(count)

    def _apply_filter(self):
        self._refresh_table()

    # ── Task Actions ───────────────────────────
    def _add_task(self):
        task = Task("Nuova Attività", QDate(2026, 3, 1), QDate(2026, 3, 15))
        dlg  = TaskDetailDialog(task, self.tasks, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.tasks.append(task)
            self._refresh_all()
            self.save_data()

    def _delete_task(self):
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Attenzione", "Seleziona almeno un'attività da eliminare.")
            return
        rows = sorted({item.row() for item in selected}, reverse=True)
        confirm = QMessageBox.question(self, "Conferma eliminazione",
                                       f"Eliminare {len(rows)} attività?",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        ids_to_del = set()
        for row in rows:
            item = self.table.item(row, 0)
            if item:
                ids_to_del.add(item.data(Qt.ItemDataRole.UserRole))
        self.tasks = [t for t in self.tasks if t.id not in ids_to_del]
        self._refresh_all()
        self.save_data()

    def _edit_selected(self):
        selected = self.table.selectedItems()
        if not selected:
            return
        row  = selected[0].row()
        item = self.table.item(row, 0)
        if item:
            self._edit_task_by_id(item.data(Qt.ItemDataRole.UserRole))

    def _edit_task_by_id(self, task_id: str):
        task = next((t for t in self.tasks if t.id == task_id), None)
        if not task: return
        dlg = TaskDetailDialog(task, self.tasks, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh_all()
            self.save_data()

    def _on_gantt_reorder(self):
        self._refresh_table()
        self.save_data()

    def _open_settings(self):
        dlg = SettingsDialog(self.project, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.project = dlg.config
            self._update_header()
            if self.gantt:
                self.gantt.start_year = self.project.get("start_year", 2026)
            self.save_data()

    def _show_notifications(self):
        panel = NotificationPanel(self.notif_manager, self)
        panel.closed.connect(self._refresh_notifications)
        pos = self.bell_widget.mapToGlobal(QPoint(0, self.bell_widget.height() + 4))
        panel.move(pos.x() - panel.width() + 40, pos.y())
        panel.show()

    # ── Lifecycle ──────────────────────────────
    def closeEvent(self, event):
        self.save_data()
        event.accept()

    # ── PDF Export ─────────────────────────────
    def _export_pdf(self):
        default = f"Cronoprogramma_{QDate.currentDate().toString('yyyyMMdd')}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Esporta PDF", default, "PDF (*.pdf)")
        if not path: return

        try:
            c = rl_canvas.Canvas(path, pagesize=landscape(A4))
            W, H = landscape(A4)
            p = self.project

            # Logo
            logo_path = p.get("logo_path", "")
            if logo_path and os.path.exists(logo_path):
                try:
                    c.drawImage(ImageReader(logo_path), cm, H - 2.8*cm, height=2*cm, preserveAspectRatio=True)
                except:
                    pass

            # Header
            c.setFont("Helvetica-Bold", 18)
            c.setFillColor(rl_colors.HexColor("#1a1a1a"))
            c.drawString(3.5*cm, H - 1.5*cm, p.get("company_name", ""))

            details = []
            if p.get("subtitle"):  details.append(p["subtitle"])
            if p.get("location"):  details.append(f"Luogo: {p['location']}")
            if p.get("address"):   details.append(f"Via: {p['address']}")
            if p.get("client"):    details.append(f"Committente: {p['client']}")

            c.setFont("Helvetica", 11)
            c.setFillColor(rl_colors.HexColor("#555555"))
            y_det = H - 2.0*cm
            for detail in details:
                c.drawString(3.5*cm, y_det, detail)
                y_det -= 0.5*cm

            # Legend
            c.setFont("Helvetica", 9)
            c.setFillColor(rl_colors.HexColor("#777777"))
            legend_y = H - 3.8*cm
            c.drawString(cm, legend_y,
                         "LEGENDA:  ■ In corso  ■ Completata  ■ Stop  ■ Non iniziata  ■ In ritardo  │ Barra arancione = date effettive  📌 = Pinnata")

            # Month headers
            months = ["MAR","APR","MAG","GIU","LUG","AGO","SET","OTT","NOV","DIC","GEN"]
            chart_left   = 6.5*cm
            chart_right  = W - cm
            chart_w      = chart_right - chart_left
            start_y_line = H - 4.6*cm
            mw = chart_w / len(months)

            c.setStrokeColor(rl_colors.HexColor("#cccccc"))
            c.line(chart_left, start_y_line, chart_right, start_y_line)

            c.setFont("Helvetica-Bold", 10)
            c.setFillColor(rl_colors.black)
            for i, m in enumerate(months):
                mx = chart_left + i * mw
                c.drawCentredString(mx + mw/2, start_y_line + 0.15*cm, m)
                c.setStrokeColor(rl_colors.HexColor("#eeeeee"))
                c.line(mx, start_y_line, mx, cm)

            # Tasks
            timeline_start  = QDate(p.get("start_year", 2026), 3, 1)
            timeline_end    = QDate(p.get("start_year", 2026) + 1, 2, 1)
            total_days      = timeline_start.daysTo(timeline_end)
            ppd             = chart_w / total_days

            task_y = start_y_line - 0.7*cm
            c.setFont("Helvetica", 9)
            status_hex = {k: v for k, v in STATUS_COLORS.items()}

            for task in self.tasks:
                if task_y < 1.5*cm:
                    c.showPage()
                    task_y = H - 2*cm

                # Name
                c.setFillColor(rl_colors.black)
                pin = " 📌" if task.is_pinned else ""
                c.drawString(cm, task_y + 0.1*cm, f"{task.name}{pin}")

                # Planned bar
                days_off = timeline_start.daysTo(task.start_date)
                dur      = max(1, task.start_date.daysTo(task.end_date))
                bx       = chart_left + days_off * ppd
                bw       = dur * ppd
                color_hex = status_hex.get(task.status, "#4facfe")
                c.setFillColor(rl_colors.HexColor(color_hex))
                c.roundRect(bx, task_y - 0.1*cm, bw, 0.45*cm, 3, fill=1, stroke=0)

                # Progress overlay
                if task.progress > 0:
                    prog_color = rl_colors.HexColor(color_hex)
                    r,g,b = prog_color.red()/255, prog_color.green()/255, prog_color.blue()/255
                    c.setFillColor(rl_colors.Color(r*0.7, g*0.7, b*0.7))
                    c.roundRect(bx, task_y - 0.1*cm, bw * task.progress/100, 0.45*cm, 3, fill=1, stroke=0)

                # Actual bar
                if task.actual_start and task.actual_start.isValid():
                    ae = task.actual_end if (task.actual_end and task.actual_end.isValid()) else QDate.currentDate()
                    a_off = timeline_start.daysTo(task.actual_start)
                    a_dur = max(1, task.actual_start.daysTo(ae))
                    ax    = chart_left + a_off * ppd
                    aw    = a_dur * ppd
                    c.setFillColor(rl_colors.Color(1, 0.6, 0, 0.7))
                    c.roundRect(ax, task_y - 0.22*cm, aw, 0.1*cm, 2, fill=1, stroke=0)

                # Date text
                c.setFillColor(rl_colors.HexColor("#666666"))
                c.setFont("Helvetica", 7)
                c.drawString(bx, task_y - 0.4*cm,
                             f"{task.start_date.toString('dd/MM')} – {task.end_date.toString('dd/MM')}")

                task_y -= 0.85*cm

            c.save()
            QMessageBox.information(self, "✅ PDF Esportato",
                                    f"Cronoprogramma esportato con successo!\n\n📄 {path}")
        except Exception as e:
            QMessageBox.critical(self, "Errore PDF", f"Errore durante l'esportazione:\n{str(e)}")


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())
