import os
import sys
import pytesseract

def setup_tesseract_path():
    candidates = []

    if getattr(sys, "frozen", False):
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates.append(os.path.join(base_dir, "tesseract", "tesseract.exe"))

    candidates.append(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    candidates.append(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")

    for path in candidates:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            return path

    return None

setup_tesseract_path()

import os
import sys
import threading
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, QRect, QPoint, QThread, Signal, QObject, QSettings, QTimer, Slot, QMetaObject
from PySide6.QtGui import QAction, QCursor, QGuiApplication, QPainter, QPen, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QVBoxLayout,
    QPushButton,
    QHBoxLayout,
    QMessageBox,
    QSystemTrayIcon,
    QMenu,
    QPlainTextEdit,
)

# Third-party helpers
import mss
from PIL import Image
import pytesseract

try:
    import keyboard
except Exception:
    keyboard = None

try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None


APP_NAME = "屏幕翻译框选"
ORGANIZATION = "OpenAI"


@dataclass
class CaptureResult:
    image_path: str
    ocr_text: str
    translated_text: str


class Worker(QObject):
    finished = Signal(object)
    error = Signal(str)


class HotkeyBridge(QObject):
    triggered = Signal()

    def __init__(self, image: Image.Image, save_dir: str, target_lang: str = "zh-CN"):
        super().__init__()
        self.image = image
        self.save_dir = save_dir
        self.target_lang = target_lang

    def run(self):
        try:
            os.makedirs(self.save_dir, exist_ok=True)
            img_path = os.path.join(self.save_dir, "capture.png")
            self.image.save(img_path)

            # OCR
            ocr_text = pytesseract.image_to_string(self.image, lang="eng+chi_sim")
            ocr_text = ocr_text.strip()
            if not ocr_text:
                ocr_text = "（未识别到文字）"

            # Translation
            translated = self.translate_text(ocr_text)
            result = CaptureResult(img_path, ocr_text, translated)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def translate_text(self, text: str) -> str:
        if GoogleTranslator is None:
            return "翻译模块未安装。请安装 deep-translator 后重试。\n\n原文：\n" + text

        try:
            # GoogleTranslator uses language codes like 'zh-CN' / 'en'
            return GoogleTranslator(source="auto", target=self.target_lang).translate(text)
        except Exception as e:
            return f"翻译失败：{e}\n\n原文：\n{text}"


class ResultPopup(QWidget):
    def __init__(self, result: CaptureResult, parent=None):
        super().__init__(parent)
        self.setWindowTitle("翻译结果")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumSize(520, 420)

        layout = QVBoxLayout(self)
        title = QLabel("识别与翻译结果")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")

        ocr_label = QLabel("识别文本")
        self.ocr_box = QPlainTextEdit()
        self.ocr_box.setPlainText(result.ocr_text)
        self.ocr_box.setReadOnly(True)

        trans_label = QLabel("翻译文本")
        self.trans_box = QPlainTextEdit()
        self.trans_box.setPlainText(result.translated_text)
        self.trans_box.setReadOnly(True)

        btn_row = QHBoxLayout()
        copy_ocr = QPushButton("复制识别文本")
        copy_trans = QPushButton("复制翻译结果")
        close_btn = QPushButton("关闭")
        copy_ocr.clicked.connect(lambda: QApplication.clipboard().setText(self.ocr_box.toPlainText()))
        copy_trans.clicked.connect(lambda: QApplication.clipboard().setText(self.trans_box.toPlainText()))
        close_btn.clicked.connect(self.close)

        btn_row.addWidget(copy_ocr)
        btn_row.addWidget(copy_trans)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)

        layout.addWidget(title)
        layout.addWidget(ocr_label)
        layout.addWidget(self.ocr_box, 1)
        layout.addWidget(trans_label)
        layout.addWidget(self.trans_box, 1)
        layout.addLayout(btn_row)

        self.setStyleSheet("""
            QWidget { background: #ffffff; color: #222; }
            QPlainTextEdit {
                border: 1px solid #d0d0d0;
                border-radius: 8px;
                padding: 8px;
                font-family: Consolas, 'Microsoft YaHei', sans-serif;
                font-size: 13px;
            }
            QPushButton {
                padding: 8px 12px;
                border-radius: 8px;
                border: 1px solid #c9c9c9;
                background: #f4f4f4;
            }
            QPushButton:hover { background: #e9e9e9; }
        """)

        # Put near cursor
        pos = QCursor.pos()
        self.move(pos.x() + 20, pos.y() + 20)


class SelectionOverlay(QWidget):
    selection_made = Signal(QRect)
    canceled = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._origin = QPoint()
        self._current = QPoint()
        self._dragging = False

        screen_geo = self._virtual_geometry()
        self.setGeometry(screen_geo)

    def _virtual_geometry(self) -> QRect:
        geo = QRect()
        for screen in QGuiApplication.screens():
            geo = geo.united(screen.geometry())
        return geo

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            rect = QRect(self._origin, self._current).normalized()
            if rect.width() < 10 or rect.height() < 10:
                self.canceled.emit()
            else:
                self.selection_made.emit(rect)
            self.close()
        elif event.button() == Qt.MouseButton.RightButton:
            self.canceled.emit()
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.canceled.emit()
            self.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dark overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 90))

        if self._dragging:
            rect = QRect(self._origin, self._current).normalized()
            # Cut-out rectangle effect
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            pen = QPen(QColor(0, 170, 255), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRect(rect)

            painter.setPen(QColor(255, 255, 255))
            painter.setFont(QFont("Microsoft YaHei", 10))
            hint = f"{rect.width()} x {rect.height()}"
            painter.drawText(rect.topLeft() + QPoint(6, -8), hint)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(360, 220)
        self.setWindowIcon(self._default_icon())

        self.settings = QSettings(ORGANIZATION, APP_NAME)
        self.target_lang = self.settings.value("target_lang", "zh-CN")

        layout = QVBoxLayout(self)
        self.info = QLabel(
            "按 Ctrl+Shift+T 开始框选屏幕文字。\n"
            "框选后自动 OCR 并弹窗翻译。"
        )
        self.info.setWordWrap(True)

        self.lang_hint = QLabel(f"目标语言：{self.target_lang}")
        self.start_btn = QPushButton("开始框选翻译")
        self.start_btn.clicked.connect(self.start_capture)

        row = QHBoxLayout()
        self.zh_btn = QPushButton("中文")
        self.en_btn = QPushButton("英文")
        self.ja_btn = QPushButton("日文")
        self.ko_btn = QPushButton("韩文")
        for btn, lang in [
            (self.zh_btn, "zh-CN"),
            (self.en_btn, "en"),
            (self.ja_btn, "ja"),
            (self.ko_btn, "ko"),
        ]:
            btn.clicked.connect(lambda checked=False, l=lang: self.set_lang(l))
            row.addWidget(btn)

        layout.addWidget(self.info)
        layout.addWidget(self.lang_hint)
        layout.addWidget(self.start_btn)
        layout.addLayout(row)

        self.setStyleSheet("""
            QWidget { background: #fafafa; font-size: 14px; }
            QLabel { color: #222; }
            QPushButton {
                padding: 8px 12px;
                border-radius: 8px;
                border: 1px solid #cfcfcf;
                background: white;
            }
            QPushButton:hover { background: #f1f1f1; }
        """)

        self.overlay = None
        self.worker_thread = None
        self.worker = None
        self.popup = None
        self.tray_icon = None
        self.hotkey_bridge = HotkeyBridge()
        self.hotkey_bridge.triggered.connect(self.start_capture)
        self._hotkey_stop_event = threading.Event()

        self._init_tray()
        self._init_hotkey()
        self._register_hotkey_hint()

    def _default_icon(self):
        pix = QIcon.fromTheme("accessories-text-editor")
        return pix

    def _register_hotkey_hint(self):
        # 提示用途：真正的全局热键在 _init_hotkey() 里注册。
        pass

    def _init_hotkey(self):
        # 需要 keyboard 包；如果缺失，程序仍可通过托盘菜单启动。
        if keyboard is None:
            if self.tray_icon:
                self.tray_icon.showMessage(
                    APP_NAME,
                    "未安装 keyboard，已禁用全局热键。可用托盘菜单启动。",
                    QSystemTrayIcon.MessageIcon.Warning,
                    4000,
                )
            return

        def on_hotkey():
            # 从热键线程切回 Qt 主线程
            self.hotkey_bridge.triggered.emit()

        try:
            keyboard.add_hotkey("ctrl+shift+t", on_hotkey)
        except Exception as e:
            if self.tray_icon:
                self.tray_icon.showMessage(
                    APP_NAME,
                    f"全局热键注册失败：{e}",
                    QSystemTrayIcon.MessageIcon.Warning,
                    5000,
                )

    def _init_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        tray_icon = QSystemTrayIcon(self)
        tray_icon.setToolTip(APP_NAME)
        tray_icon.setVisible(True)
        self.tray_icon = tray_icon

        menu = QMenu()
        act_capture = QAction("开始框选翻译", self)
        act_show = QAction("显示窗口", self)
        act_hide = QAction("隐藏到托盘", self)
        act_quit = QAction("退出", self)
        act_capture.triggered.connect(self.start_capture)
        act_show.triggered.connect(self._show_from_tray)
        act_hide.triggered.connect(self.hide)
        act_quit.triggered.connect(self._quit_app)
        menu.addAction(act_capture)
        menu.addAction(act_show)
        menu.addAction(act_hide)
        menu.addSeparator()
        menu.addAction(act_quit)
        tray_icon.setContextMenu(menu)
        tray_icon.activated.connect(self._on_tray_activated)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        try:
            if keyboard is not None:
                keyboard.unhook_all_hotkeys()
                keyboard.unhook_all()
        except Exception:
            pass
        QApplication.quit()

    def set_lang(self, lang: str):
        self.target_lang = lang
        self.settings.setValue("target_lang", lang)
        self.lang_hint.setText(f"目标语言：{lang}")

    @Slot()
    def start_capture(self):
        if self.overlay and self.overlay.isVisible():
            return
        self.overlay = SelectionOverlay()
        self.overlay.selection_made.connect(self.on_selection)
        self.overlay.canceled.connect(self.on_canceled)
        self.overlay.showFullScreen()
        self.overlay.raise_()
        self.overlay.activateWindow()
        self.hide()

    def on_canceled(self):
        self._show_from_tray()

    def on_selection(self, rect: QRect):
        # Capture the selected rectangle from the virtual desktop.
        try:
            virtual_geo = self._virtual_geometry()
            x = rect.x() - virtual_geo.x()
            y = rect.y() - virtual_geo.y()
            w = rect.width()
            h = rect.height()
            if w <= 0 or h <= 0:
                raise ValueError("无效选区")

            with mss.mss() as sct:
                monitor = {
                    "left": rect.x(),
                    "top": rect.y(),
                    "width": w,
                    "height": h,
                }
                shot = sct.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.rgb)

            self._run_worker(image)
        except Exception as e:
            QMessageBox.critical(self, "截图失败", str(e))
            self.showNormal()

    def _virtual_geometry(self) -> QRect:
        geo = QRect()
        for screen in QGuiApplication.screens():
            geo = geo.united(screen.geometry())
        return geo

    def _run_worker(self, image: Image.Image):
        self.worker_thread = QThread()
        self.worker = Worker(image=image, save_dir=os.path.join(os.path.expanduser("~"), "screen_translate_cache"), target_lang=self.target_lang)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_result)
        self.worker.error.connect(self.on_worker_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.error.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def on_result(self, result: CaptureResult):
        self._show_from_tray()
        self.popup = ResultPopup(result)
        self.popup.show()

    def on_worker_error(self, msg: str):
        self._show_from_tray()
        QMessageBox.critical(self, "处理失败", msg)

    def closeEvent(self, event):
        # 关闭窗口时默认隐藏到托盘，不直接退出
        if self.tray_icon and self.tray_icon.isVisible():
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                APP_NAME,
                "程序已隐藏到托盘，仍在后台运行。",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
        else:
            event.accept()


def main():
    QApplication.setOrganizationName(ORGANIZATION)
    QApplication.setApplicationName(APP_NAME)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

