from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from PySide6 import QtCore, QtGui, QtWidgets


STATE_COLORS = {
    "READY": QtGui.QColor(46, 125, 50),
    "BUSY": QtGui.QColor(245, 124, 0),
    "COOLING": QtGui.QColor(2, 136, 209),
    "NEEDS_ATTENTION": QtGui.QColor(198, 40, 40),
    "UNRESPONSIVE": QtGui.QColor(136, 14, 79),
    "DISCONNECTED": QtGui.QColor(97, 97, 97),
    "BOOTING": QtGui.QColor(93, 64, 55),
}


class PhoneTile(QtWidgets.QFrame):
    clicked = QtCore.Signal(str, int, QtCore.Qt.KeyboardModifiers)
    double_clicked = QtCore.Signal(str)

    def __init__(self, index: int, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.index = index
        self.serial: str = ""
        self._selected = False

        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setLineWidth(2)
        self.setAutoFillBackground(True)

        self._name = QtWidgets.QLabel("-")
        self._name.setWordWrap(True)
        font = self._name.font()
        font.setBold(True)
        self._name.setFont(font)

        self._serial = QtWidgets.QLabel("-")
        self._serial.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self._state = QtWidgets.QLabel("-")
        self._task = QtWidgets.QLabel("")
        self._task.setWordWrap(True)

        self._dot = QtWidgets.QLabel("●")
        self._dot.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self._name, 1)
        top.addWidget(self._dot, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addLayout(top)
        layout.addWidget(self._serial)
        layout.addWidget(self._state)
        layout.addWidget(self._task, 1)

        self._apply_style("DISCONNECTED")
        self._apply_selected(False)

    def set_snapshot(self, snap: Dict[str, Any]) -> None:
        meta = snap.get("meta", {})
        rt = snap.get("runtime", {})

        self.serial = str(meta.get("serial") or "")
        name = str(meta.get("name") or self.serial)
        state = str(rt.get("state") or "UNKNOWN")
        task = str(rt.get("current_task_label") or "")
        err = str(rt.get("last_error") or "")

        self._name.setText(name)
        self._serial.setText(self.serial)
        self._state.setText(f"State: {state}")
        if task:
            self._task.setText(f"Now: {task}")
        elif err:
            self._task.setText(f"Issue: {err}")
        else:
            self._task.setText("")

        self._apply_style(state)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._apply_selected(self._selected)

    def _apply_style(self, state: str) -> None:
        s = (state or "").upper()
        color = STATE_COLORS.get(s, QtGui.QColor(120, 120, 120))

        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor(30, 30, 30))
        pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(230, 230, 230))
        self.setPalette(pal)

        self._dot.setStyleSheet(f"color: rgb({color.red()},{color.green()},{color.blue()}); font-size: 18px;")

    def _apply_selected(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet(
                "QFrame { border: 2px solid #42A5F5; border-radius: 8px; background: #1E1E1E; }"
            )
        else:
            self.setStyleSheet(
                "QFrame { border: 2px solid #424242; border-radius: 8px; background: #1E1E1E; }"
            )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.serial, self.index, event.modifiers())
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self.serial:
            self.double_clicked.emit(self.serial)
        super().mouseDoubleClickEvent(event)


class PhoneGrid(QtWidgets.QWidget):
    selection_changed = QtCore.Signal(list)
    scrcpy_requested = QtCore.Signal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._tiles: List[PhoneTile] = []
        self._selected: Set[str] = set()
        self._last_clicked_index: Optional[int] = None

        self._layout = QtWidgets.QGridLayout(self)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(12)

        self._rubber_band: Optional[QtWidgets.QRubberBand] = None
        self._drag_origin: Optional[QtCore.QPoint] = None
        self._dragging = False

        self._columns = 4

    def selected_serials(self) -> List[str]:
        # Preserve order based on tile ordering.
        out: List[str] = []
        for t in self._tiles:
            if t.serial in self._selected:
                out.append(t.serial)
        return out

    def set_devices(self, devices: List[Dict[str, Any]], selection: List[str]) -> None:
        # Keep existing selection if possible.
        self._selected = set(selection or [])

        # Rebuild tiles if device count changed or serials changed.
        serials = [d.get("meta", {}).get("serial") for d in devices]
        existing_serials = [t.serial for t in self._tiles]
        if serials != existing_serials:
            self._rebuild_tiles(devices)
        else:
            for tile, snap in zip(self._tiles, devices, strict=False):
                tile.set_snapshot(snap)

        for tile in self._tiles:
            tile.set_selected(tile.serial in self._selected)

    def _rebuild_tiles(self, devices: List[Dict[str, Any]]) -> None:
        # Clear layout.
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        self._tiles = []
        for idx, snap in enumerate(devices):
            tile = PhoneTile(index=idx)
            tile.set_snapshot(snap)
            tile.clicked.connect(self._on_tile_clicked)
            tile.double_clicked.connect(self.scrcpy_requested)
            self._tiles.append(tile)

        self._place_tiles()

    def _place_tiles(self) -> None:
        cols = max(1, int(self._columns))
        for i, tile in enumerate(self._tiles):
            row = i // cols
            col = i % cols
            self._layout.addWidget(tile, row, col)

        # Add stretch so tiles stay top-aligned.
        self._layout.setRowStretch((len(self._tiles) // cols) + 1, 1)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        # Responsive columns based on width.
        w = max(200, self.width())
        cols = max(1, min(8, w // 240))
        if cols != self._columns:
            self._columns = cols
            self._place_tiles()
        super().resizeEvent(event)

    def _on_tile_clicked(self, serial: str, index: int, mods: QtCore.Qt.KeyboardModifiers) -> None:
        if not serial:
            return

        shift = bool(mods & QtCore.Qt.ShiftModifier)
        ctrl = bool(mods & QtCore.Qt.ControlModifier)

        if shift and self._last_clicked_index is not None:
            lo = min(self._last_clicked_index, index)
            hi = max(self._last_clicked_index, index)
            range_serials = [t.serial for t in self._tiles[lo : hi + 1] if t.serial]
            if not ctrl:
                self._selected = set(range_serials)
            else:
                self._selected.update(range_serials)
        elif ctrl:
            if serial in self._selected:
                self._selected.remove(serial)
            else:
                self._selected.add(serial)
        else:
            self._selected = {serial}

        self._last_clicked_index = index
        self._apply_selection()

    def _apply_selection(self) -> None:
        for tile in self._tiles:
            tile.set_selected(tile.serial in self._selected)
        self.selection_changed.emit(self.selected_serials())

    def clear_selection(self) -> None:
        self._selected.clear()
        self._apply_selection()

    # --- Drag-select (rubber band) ---

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_origin = event.pos()
            self._dragging = True
            if self._rubber_band is None:
                self._rubber_band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self)
            self._rubber_band.setGeometry(QtCore.QRect(self._drag_origin, QtCore.QSize()))
            self._rubber_band.show()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging and self._drag_origin and self._rubber_band:
            rect = QtCore.QRect(self._drag_origin, event.pos()).normalized()
            self._rubber_band.setGeometry(rect)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._dragging:
            self._dragging = False
            if self._rubber_band:
                rect = self._rubber_band.geometry()
                self._rubber_band.hide()

                mods = event.modifiers()
                ctrl = bool(mods & QtCore.Qt.ControlModifier)

                hit: List[str] = []
                for tile in self._tiles:
                    if not tile.serial:
                        continue
                    tile_rect = tile.geometry()
                    if rect.intersects(tile_rect):
                        hit.append(tile.serial)

                if hit:
                    if ctrl:
                        self._selected.update(hit)
                    else:
                        self._selected = set(hit)
                    self._apply_selection()

        super().mouseReleaseEvent(event)
