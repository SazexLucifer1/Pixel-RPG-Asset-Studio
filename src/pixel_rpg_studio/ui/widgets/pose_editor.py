"""Pose editor: drag the joints of an OpenPose skeleton.

Shows the OpenPose control image exactly as it is sent to ComfyUI, optionally
over a faint underlay (the generated frame or the reference). Editable
joints: head, neck, shoulders, elbows, hands, hips, knees, feet. Changes are
emitted on mouse release.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from pixel_rpg_studio.imaging.openpose import draw_pose
from pixel_rpg_studio.poses.skeleton import EDITABLE_JOINTS, JOINT_LABELS, Pose2D
from pixel_rpg_studio.ui.widgets.image_view import pil_to_qimage

GRAB_RADIUS = 14
RENDER_SIZE = 384


class PoseEditor(QWidget):
    pose_edited = Signal(object)  # Pose2D

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.pose: Pose2D | None = None
        self._pix: QPixmap | None = None
        self._underlay: QPixmap | None = None
        self._drag: str | None = None
        self._hover: str | None = None
        self.underlay_opacity = 0.35
        self.setMouseTracking(True)
        self.setMinimumSize(260, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # -------------------------------------------------------------- data
    def set_pose(self, pose: Pose2D | None) -> None:
        self.pose = pose.copy() if pose is not None else None
        self._render()

    def set_underlay(self, path: Path | None) -> None:
        self._underlay = None
        if path is not None and Path(path).is_file():
            img = Image.open(path).convert("RGBA")
            self._underlay = QPixmap.fromImage(pil_to_qimage(img))
        self.update()

    def _render(self) -> None:
        if self.pose is None:
            self._pix = None
        else:
            img = draw_pose(self.pose.openpose_joints(), RENDER_SIZE, RENDER_SIZE)
            self._pix = QPixmap.fromImage(pil_to_qimage(img))
        self.update()

    # ---------------------------------------------------------- geometry
    def _area(self) -> QRectF:
        side = min(self.width(), self.height()) - 8
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

    def _to_widget(self, p: list[float]) -> QPointF:
        a = self._area()
        return QPointF(a.x() + p[0] * a.width(), a.y() + p[1] * a.height())

    def _to_pose(self, pos: QPointF) -> tuple[float, float]:
        a = self._area()
        return (pos.x() - a.x()) / a.width(), (pos.y() - a.y()) / a.height()

    def joint_at(self, pos: QPointF) -> str | None:
        if self.pose is None:
            return None
        best, best_d = None, GRAB_RADIUS ** 2
        for name in EDITABLE_JOINTS:
            p = self.pose.joints.get(name)
            if p is None:
                continue
            w = self._to_widget(p)
            d = (w.x() - pos.x()) ** 2 + (w.y() - pos.y()) ** 2
            if d <= best_d:
                best, best_d = name, d
        return best

    # ------------------------------------------------------------- events
    def mousePressEvent(self, event) -> None:  # pragma: no cover - interaction
        self._drag = self.joint_at(event.position())

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - interaction
        if self._drag and self.pose is not None:
            u, v = self._to_pose(event.position())
            self.pose.move(self._drag, u, v)
            self._render()
        else:
            hover = self.joint_at(event.position())
            if hover != self._hover:
                self._hover = hover
                self.setCursor(Qt.CursorShape.OpenHandCursor if hover else Qt.CursorShape.ArrowCursor)
                self.update()

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - interaction
        if self._drag and self.pose is not None:
            self.pose_edited.emit(self.pose.copy())
        self._drag = None

    def move_joint(self, name: str, u: float, v: float) -> None:
        """Programmatic edit (same effect as dragging); emits ``pose_edited``."""
        if self.pose is None:
            return
        self.pose.move(name, u, v)
        self._render()
        self.pose_edited.emit(self.pose.copy())

    def paintEvent(self, event) -> None:  # pragma: no cover - painting
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#15151c"))
        a = self._area()
        if self._pix is None:
            p.setPen(QColor("#8a8aa3"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No pose yet.\nSelect an animation, direction and frame.")
            return
        p.drawPixmap(a.toRect(), self._pix)
        if self._underlay is not None:
            p.setOpacity(self.underlay_opacity)
            p.drawPixmap(a.toRect(), self._underlay)
            p.setOpacity(1.0)
        font = QFont()
        font.setPointSize(8)
        p.setFont(font)
        for name in EDITABLE_JOINTS:
            pt = self.pose.joints.get(name) if self.pose else None
            if pt is None:
                continue
            w = self._to_widget(pt)
            active = name in (self._drag, self._hover)
            p.setPen(QPen(QColor("#ffffff"), 2 if active else 1))
            p.setBrush(QColor(255, 255, 255, 90 if active else 30))
            r = 8 if active else 6
            p.drawEllipse(w, r, r)
            if active:
                p.drawText(w + QPointF(10, -8), JOINT_LABELS.get(name, name))
