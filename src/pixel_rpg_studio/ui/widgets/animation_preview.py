"""Animated sprite preview with play/pause, FPS and frame stepping."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget
from PySide6.QtCore import Qt

from pixel_rpg_studio.ui.widgets.image_view import PixelImageView, load_qimage


class AnimationPreview(QWidget):
    frame_selected = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.frames = []
        self.paths: list[Path] = []
        self.index = 0
        self.view = PixelImageView(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.play_btn = QPushButton("Pause")
        self.play_btn.clicked.connect(self.toggle)
        self.prev_btn = QPushButton("◀")
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn = QPushButton("▶")
        self.next_btn.clicked.connect(lambda: self.step(1))
        self.fps = QSpinBox()
        self.fps.setRange(1, 60)
        self.fps.setValue(8)
        self.fps.setSuffix(" fps")
        self.fps.valueChanged.connect(self._restart)
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(True)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self._slider_moved)
        self.label = QLabel("–")
        controls = QHBoxLayout()
        for w in (self.prev_btn, self.play_btn, self.next_btn, self.fps, self.loop, self.label):
            controls.addWidget(w)
        controls.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.slider)
        layout.addLayout(controls)

    def set_frames(self, paths: list[Path], fps: int | None = None, loop: bool | None = None) -> None:
        self.paths = [Path(p) for p in paths]
        self.frames = [load_qimage(p) for p in self.paths]
        self.frames = [f for f in self.frames if f is not None]
        if fps:
            self.fps.setValue(int(fps))
        if loop is not None:
            self.loop.setChecked(loop)
        self.index = 0
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(self.frames) - 1))
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self._show()
        self._restart()

    def _restart(self) -> None:
        self.timer.stop()
        if len(self.frames) > 1 and self.play_btn.text() == "Pause":
            self.timer.start(int(1000 / max(1, self.fps.value())))

    def toggle(self) -> None:
        if self.play_btn.text() == "Pause":
            self.play_btn.setText("Play")
            self.timer.stop()
        else:
            self.play_btn.setText("Pause")
            self._restart()

    def step(self, delta: int) -> None:
        if not self.frames:
            return
        self.index = (self.index + delta) % len(self.frames)
        self._show()

    def _tick(self) -> None:
        if not self.frames:
            return
        if self.index + 1 >= len(self.frames) and not self.loop.isChecked():
            self.timer.stop()
            self.play_btn.setText("Play")
            return
        self.step(1)

    def _slider_moved(self, value: int) -> None:
        self.index = value
        self._show()

    def _show(self) -> None:
        if not self.frames:
            self.view.set_image(None, "No frames yet")
            self.label.setText("–")
            return
        self.view.set_image(self.frames[self.index])
        self.slider.blockSignals(True)
        self.slider.setValue(self.index)
        self.slider.blockSignals(False)
        self.label.setText(f"Frame {self.index + 1}/{len(self.frames)}")
        self.frame_selected.emit(self.index)
