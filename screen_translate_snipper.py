import os
import sys
import socket
import traceback

from dataclasses import dataclass

from PySide6.QtCore import (
    Qt,
    QRect,
    QObject,
    Signal,
    Slot,
    QThread,
)

from PySide6.QtGui import (
    QPainter,
    QPen,
    QColor,
    QImage,
    QIcon,
    QAction,
)

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QPlainTextEdit,
    QMessageBox,
    QSystemTrayIcon,
    QMenu,
    QStyle,
)

from PIL import Image

import pytesseract


try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None


try:
    import keyboard
except Exception:
    keyboard = None


APP_NAME = "Screen Translate"

# 给所有网络请求设一个默认超时兜底，避免翻译请求卡死不返回也不报错
# （deep_translator 内部用的 requests 库，如果调用时没显式传 timeout，
#  会退化使用这个 socket 默认超时）
socket.setdefaulttimeout(15)


# ==========================
# Tesseract路径处理
# ==========================

def setup_tesseract():

    paths = []

    # exe打包后的路径 (PyInstaller --onefile 解压到的临时目录)
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
        paths.append(os.path.join(base, "tesseract.exe"))

        tessdata = os.path.join(base, "tessdata")
        if os.path.exists(tessdata):
            os.environ["TESSDATA_PREFIX"] = tessdata

    # 普通Python运行路径 / 系统安装路径
    paths.extend([
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ])

    for path in paths:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path

            tess_dir = os.path.join(os.path.dirname(path), "tessdata")
            if os.path.exists(tess_dir):
                os.environ["TESSDATA_PREFIX"] = tess_dir

            return True

    return False


setup_tesseract()


# ==========================
# 全局异常处理
# ==========================
# --windowed 打包后没有控制台，任何没被 try/except 接住的异常会被静默吞掉，
# 表现就是"点了没反应"。这里把所有未捕获异常都弹窗显示出来，方便定位问题。

def install_excepthook():

    def _hook(exc_type, exc_value, exc_tb):
        text = "".join(
            traceback.format_exception(exc_type, exc_value, exc_tb)
        )
        print(text, file=sys.stderr)
        try:
            QMessageBox.critical(None, "程序出错", text[-4000:])
        except Exception:
            pass

    sys.excepthook = _hook


# ==========================
# 数据结构
# ==========================

@dataclass
class Result:
    text: str
    translate: str


# ==========================
# 全局热键通信
# ==========================

class HotkeyBridge(QObject):
    triggered = Signal()


# ==========================
# OCR + 翻译线程
# ==========================

class Worker(QObject):

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, image, target="zh-CN"):
        super().__init__()
        self.image = image
        self.target = target

    def run(self):
        try:
            # --psm 6：把截图当成"一块统一的文本"，而不是用默认的整页版面分析。
            # 截图通常是一行字/一小块区域，默认 PSM 很容易识别成"没有文字"。
            text = pytesseract.image_to_string(
                self.image,
                lang="eng+chi_sim",
                config="--psm 6",
            )
            text = text.strip()

            if not text:
                text = "没有识别到文字"

            if GoogleTranslator:
                try:
                    trans = GoogleTranslator(
                        source="auto",
                        target=self.target
                    ).translate(text)
                except Exception as e:
                    trans = f"翻译失败：{e}"
            else:
                trans = "未安装翻译模块 (deep_translator)"

            self.finished.emit(Result(text, trans))

        except Exception:
            self.error.emit(traceback.format_exc())


# ==========================
# 屏幕框选窗口
# ==========================

class SelectionOverlay(QWidget):

    selected = Signal(QRect)
    canceled = Signal()

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.start = None
        self.end = None
        self.drag = False

        self.setCursor(Qt.CursorShape.CrossCursor)

        # 覆盖所有屏幕（虚拟桌面坐标，可能包含负值）
        rect = QRect()
        for s in QApplication.screens():
            rect = rect.united(s.geometry())

        self.setGeometry(rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()
            self.end = self.start
            self.drag = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.drag:
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.drag:
            self.drag = False
            rect = QRect(self.start, self.end).normalized()

            if rect.width() > 10 and rect.height() > 10:
                self.selected.emit(rect)
            else:
                self.canceled.emit()

            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.canceled.emit()
            self.close()

    def paintEvent(self, event):
        painter = QPainter(self)

        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        if self.drag:
            rect = QRect(self.start, self.end).normalized()

            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.fillRect(rect, Qt.GlobalColor.transparent)

            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )

            painter.setPen(QPen(QColor(0, 180, 255), 2))
            painter.drawRect(rect)


# ==========================
# 翻译结果窗口
# ==========================

class ResultWindow(QWidget):

    def __init__(self, result):
        super().__init__()

        self.setWindowTitle("屏幕翻译")
        self.resize(550, 450)

        layout = QVBoxLayout(self)

        title = QLabel("识别结果")
        self.original = QPlainTextEdit()
        self.original.setPlainText(result.text)
        self.original.setReadOnly(True)

        title2 = QLabel("翻译结果")
        self.trans = QPlainTextEdit()
        self.trans.setPlainText(result.translate)
        self.trans.setReadOnly(True)

        btn = QPushButton("复制翻译")
        btn.clicked.connect(self.copy)

        layout.addWidget(title)
        layout.addWidget(self.original)
        layout.addWidget(title2)
        layout.addWidget(self.trans)
        layout.addWidget(btn)

    def copy(self):
        QApplication.clipboard().setText(self.trans.toPlainText())


# ==========================
# 主程序窗口
# ==========================

class MainWindow(QWidget):

    def __init__(self):
        super().__init__()

        self.setWindowTitle(APP_NAME)
        self.resize(350, 200)

        self.overlay = None
        self.thread = None
        self.worker = None
        self.result = None

        self.hotkey = HotkeyBridge()
        self.hotkey.triggered.connect(self.start_capture)

        self.setup_ui()
        self.setup_tray()
        self.setup_hotkey()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        label = QLabel("Ctrl + Shift + T\n框选屏幕文字翻译")

        btn = QPushButton("开始翻译")
        btn.clicked.connect(self.start_capture)

        layout.addWidget(label)
        layout.addWidget(btn)

    # -----------------------
    # 托盘
    # -----------------------

    def setup_tray(self):

        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray = QSystemTrayIcon(self)

        icon_path = os.path.join(
            getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
            "icon.ico"
        )
        if os.path.exists(icon_path):
            self.tray.setIcon(QIcon(icon_path))
        else:
            self.tray.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
            )

        self.tray.setToolTip(APP_NAME)

        menu = QMenu()

        start_action = QAction("开始翻译", self)
        show_action = QAction("打开窗口", self)
        quit_action = QAction("退出", self)

        start_action.triggered.connect(self.start_capture)
        show_action.triggered.connect(self.show)
        quit_action.triggered.connect(QApplication.quit)

        menu.addAction(start_action)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
            self.activateWindow()

    # -----------------------
    # 全局热键
    # -----------------------

    def setup_hotkey(self):

        if keyboard is None:
            return

        try:
            def callback():
                self.hotkey.triggered.emit()

            keyboard.add_hotkey("ctrl+shift+t", callback)
        except Exception as e:
            print(f"热键注册失败（可能需要管理员权限）: {e}")

    # -----------------------
    # 开始框选
    # -----------------------

    @Slot()
    def start_capture(self):
        self.hide()

        self.overlay = SelectionOverlay()
        self.overlay.selected.connect(self.capture)
        self.overlay.canceled.connect(self.show)
        self.overlay.show()

    # -----------------------
    # 截图（用 Qt 自带接口，避免第三方截图库和 Qt 坐标系不一致导致框选错位）
    # -----------------------

    def capture(self, rect):

        try:
            # 根据选区左上角判断在哪个物理屏幕上，再把坐标换算成
            # 那块屏幕自己的局部坐标（grabWindow 要求的是屏幕局部坐标，
            # 不是跨屏虚拟桌面坐标，多屏环境下这一步不能省）
            screen = QApplication.screenAt(rect.topLeft())
            if screen is None:
                screen = QApplication.primaryScreen()

            local_rect = rect.translated(-screen.geometry().topLeft())

            pixmap = screen.grabWindow(
                0,
                local_rect.x(),
                local_rect.y(),
                local_rect.width(),
                local_rect.height(),
            )

            if pixmap.isNull():
                raise RuntimeError("截图失败：抓取到的图像为空")

            qimage = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
            width = qimage.width()
            height = qimage.height()
            stride = qimage.bytesPerLine()

            buf = bytes(qimage.constBits())[: stride * height]

            image = Image.frombuffer(
                "RGB", (width, height), buf, "raw", "RGB", stride, 1
            )

        except Exception:
            self.show()
            QMessageBox.warning(self, "截图失败", traceback.format_exc()[-3000:])
            return

        self.thread = QThread()
        self.worker = Worker(image)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.show_result)
        self.worker.error.connect(self.show_error)

        # 收尾清理，避免线程/对象泄漏
        self.worker.finished.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()

    def show_result(self, result):
        self.show()

        self.result = ResultWindow(result)
        self.result.show()

    def show_error(self, msg):
        self.show()
        QMessageBox.warning(self, "错误", msg[-3000:])

    def closeEvent(self, event):
        if hasattr(self, "tray"):
            self.hide()
            event.ignore()
        else:
            event.accept()


# ==========================
# 程序入口
# ==========================

def main():

    # Windows 高DPI支持（框选坐标与实际截图对齐的关键之一）
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        except Exception:
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)

    install_excepthook()

    # 关闭最后一个窗口不退出（保留托盘运行）
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
