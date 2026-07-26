import os
import sys
import threading

from dataclasses import dataclass

from PySide6.QtCore import (
    Qt,
    QRect,
    QPoint,
    QObject,
    Signal,
    Slot,
    QThread,
    QSettings
)

from PySide6.QtGui import (
    QPainter,
    QPen,
    QColor,
    QCursor,
    QAction
)

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QMessageBox,
    QSystemTrayIcon,
    QMenu
)


import mss
from PIL import Image

import pytesseract


try:
    from deep_translator import GoogleTranslator
except:
    GoogleTranslator = None


try:
    import keyboard
except:
    keyboard = None



APP_NAME = "Screen Translate"


# ==========================
# Tesseract路径处理
# ==========================

def setup_tesseract():

    paths = []


    # exe打包后的路径
    if getattr(sys, "frozen", False):

        base = sys._MEIPASS

        paths.append(
            os.path.join(
                base,
                "tesseract",
                "tesseract.exe"
            )
        )


        # 中文语言包路径
        tessdata = os.path.join(
            base,
            "tessdata"
        )

        if os.path.exists(tessdata):

            os.environ["TESSDATA_PREFIX"] = tessdata



    # 普通Python运行路径
    paths.extend([

        r"C:\Program Files\Tesseract-OCR\tesseract.exe",

        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"

    ])



    for path in paths:


        if os.path.exists(path):


            # 设置tesseract执行文件

            pytesseract.pytesseract.tesseract_cmd = path



            # 设置语言库目录

            tess_dir = os.path.join(
                os.path.dirname(path),
                "tessdata"
            )


            if os.path.exists(tess_dir):

                os.environ["TESSDATA_PREFIX"] = tess_dir



            return True



    return False



setup_tesseract()


# ==========================
# 数据结构
# ==========================


@dataclass
class Result:

    text:str
    translate:str



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



    def __init__(
        self,
        image,
        target="zh-CN"
    ):

        super().__init__()

        self.image=image
        self.target=target



    def run(self):

        try:

            text = pytesseract.image_to_string(
                self.image,
                lang="eng+chi_sim"
            )


            text=text.strip()


            if not text:
                text="没有识别到文字"



            if GoogleTranslator:

                try:

                    trans = GoogleTranslator(
                        source="auto",
                        target=self.target
                    ).translate(text)


                except:

                    trans="翻译失败"



            else:

                trans="未安装翻译模块"



            self.finished.emit(
                Result(
                    text,
                    trans
                )
            )


        except Exception as e:

            self.error.emit(
                str(e)
            )
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
            |
            Qt.WindowType.WindowStaysOnTopHint
            |
            Qt.WindowType.Tool
        )


        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )


        self.start=None
        self.end=None
        self.drag=False


        self.setCursor(
            Qt.CursorShape.CrossCursor
        )


        # 覆盖所有屏幕

        rect=QRect()

        for s in QApplication.screens():

            rect=rect.united(
                s.geometry()
            )


        self.setGeometry(rect)



    def mousePressEvent(self,event):

        if event.button()==Qt.MouseButton.LeftButton:

            self.start=event.position().toPoint()

            self.end=self.start

            self.drag=True

            self.update()



    def mouseMoveEvent(self,event):

        if self.drag:

            self.end=event.position().toPoint()

            self.update()



    def mouseReleaseEvent(self,event):

        if self.drag:

            self.drag=False

            rect=QRect(
                self.start,
                self.end
            ).normalized()


            if rect.width()>10 and rect.height()>10:

                self.selected.emit(rect)

            else:

                self.canceled.emit()


            self.close()



    def keyPressEvent(self,event):

        if event.key()==Qt.Key.Key_Escape:

            self.canceled.emit()

            self.close()



    def paintEvent(self,event):

        painter=QPainter(self)


        painter.fillRect(
            self.rect(),
            QColor(
                0,
                0,
                0,
                100
            )
        )


        if self.drag:

            rect=QRect(
                self.start,
                self.end
            ).normalized()


            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )

            painter.fillRect(
                rect,
                Qt.GlobalColor.transparent
            )


            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )


            painter.setPen(
                QPen(
                    QColor(0,180,255),
                    2
                )
            )


            painter.drawRect(rect)





# ==========================
# 翻译结果窗口
# ==========================


class ResultWindow(QWidget):


    def __init__(self,result):

        super().__init__()


        self.setWindowTitle(
            "屏幕翻译"
        )


        self.resize(
            550,
            450
        )


        layout=QVBoxLayout(self)



        title=QLabel(
            "识别结果"
        )


        self.original=QPlainTextEdit()

        self.original.setPlainText(
            result.text
        )


        self.original.setReadOnly(True)



        title2=QLabel(
            "翻译结果"
        )


        self.trans=QPlainTextEdit()

        self.trans.setPlainText(
            result.translate
        )


        self.trans.setReadOnly(True)




        btn=QPushButton(
            "复制翻译"
        )


        btn.clicked.connect(
            self.copy
        )



        layout.addWidget(title)

        layout.addWidget(
            self.original
        )


        layout.addWidget(title2)

        layout.addWidget(
            self.trans
        )


        layout.addWidget(btn)



    def copy(self):

        QApplication.clipboard().setText(
            self.trans.toPlainText()
        )





# ==========================
# 主程序窗口
# ==========================


class MainWindow(QWidget):


    def __init__(self):

        super().__init__()


        self.setWindowTitle(
            APP_NAME
        )


        self.resize(
            350,
            200
        )



        self.overlay=None

        self.thread=None

        self.worker=None


        self.hotkey=HotkeyBridge()

        self.hotkey.triggered.connect(
            self.start_capture
        )


        self.setup_ui()


        self.setup_tray()


        self.setup_hotkey()





    def setup_ui(self):


        layout=QVBoxLayout(self)


        label=QLabel(
            "Ctrl + Shift + T\n框选屏幕文字翻译"
        )


        btn=QPushButton(
            "开始翻译"
        )


        btn.clicked.connect(
            self.start_capture
        )


        layout.addWidget(label)

        layout.addWidget(btn)





    # -----------------------
    # 托盘
    # -----------------------


    def setup_tray(self):


        if not QSystemTrayIcon.isSystemTrayAvailable():

            return



        self.tray=QSystemTrayIcon(
            self
        )


        self.tray.setToolTip(
            APP_NAME
        )



        menu=QMenu()


        start=QAction(
            "开始翻译",
            self
        )


        show=QAction(
            "打开窗口",
            self
        )


        quit=QAction(
            "退出",
            self
        )



        start.triggered.connect(
            self.start_capture
        )


        show.triggered.connect(
            self.show
        )


        quit.triggered.connect(
            QApplication.quit
        )



        menu.addAction(start)

        menu.addAction(show)

        menu.addSeparator()

        menu.addAction(quit)



        self.tray.setContextMenu(
            menu
        )


        self.tray.show()






    # -----------------------
    # 全局热键
    # -----------------------


    def setup_hotkey(self):


        if keyboard is None:

            return



        def callback():

            self.hotkey.triggered.emit()



        keyboard.add_hotkey(
            "ctrl+shift+t",
            callback
        )






    # -----------------------
    # 开始框选
    # -----------------------


    @Slot()
    def start_capture(self):


        self.hide()


        self.overlay=SelectionOverlay()


        self.overlay.selected.connect(
            self.capture
        )


        self.overlay.show()






    # -----------------------
    # 截图
    # -----------------------


    def capture(self,rect):


        with mss.mss() as sct:


            img=sct.grab(
                {
                    "left":rect.x(),
                    "top":rect.y(),
                    "width":rect.width(),
                    "height":rect.height()
                }
            )


            image=Image.frombytes(
                "RGB",
                img.size,
                img.rgb
            )



        self.thread=QThread()


        self.worker=Worker(
            image
        )


        self.worker.moveToThread(
            self.thread
        )


        self.thread.started.connect(
            self.worker.run
        )


        self.worker.finished.connect(
            self.show_result
        )


        self.worker.error.connect(
            self.show_error
        )


        self.worker.finished.connect(
            self.thread.quit
        )


        self.thread.start()





    def show_result(self,result):


        self.show()


        self.result=ResultWindow(
            result
        )


        self.result.show()





    def show_error(self,msg):


        self.show()


        QMessageBox.warning(
            self,
            "错误",
            msg
        )




    def closeEvent(self,event):

        if hasattr(self,"tray"):

            self.hide()

            event.ignore()

        else:

            event.accept()
            # ==========================
# 程序入口
# ==========================


def main():


    # Windows 高DPI支持

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )


    app=QApplication(
        sys.argv
    )


    # 关闭最后一个窗口不退出

    app.setQuitOnLastWindowClosed(
        False
    )


    window=MainWindow()


    window.show()



    sys.exit(
        app.exec()
    )





# ==========================
# 启动
# ==========================


if __name__=="__main__":

    main()
