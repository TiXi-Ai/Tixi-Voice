# SPDX-License-Identifier: GPL-3.0-or-later
"""Tixi Voice 1.1 — offline speech studio. GPL-3.0-or-later; see LICENSE.txt."""
from __future__ import annotations
import argparse
import logging
import os
import shutil
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

def main():
    parser=argparse.ArgumentParser(description='Tixi Voice offline speech studio')
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--tts-worker',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--clone-worker',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if (args.tts_worker or args.clone_worker) and os.name=='nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetErrorMode(0x0001|0x0002|0x8000)
        except (OSError,AttributeError):pass
    if args.clone_worker:
        from personal_voice import worker
        return worker(args.clone_worker)
    if args.tts_worker:
        from engines import worker_tts
        return worker_tts(args.tts_worker)
    from core import Store,ASSETS,data_directory
    from PySide6.QtCore import Qt,QLockFile
    from PySide6.QtGui import QIcon,QFont,QFontDatabase
    from PySide6.QtWidgets import QApplication
    from ui import MainWindow,message
    app=QApplication(sys.argv[:1]);app.setApplicationName('Tixi Voice');app.setOrganizationName('Tixi');app.setApplicationVersion('1.1.0')
    app.setStyle('Fusion');app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setWindowIcon(QIcon(str(ASSETS/('tixi.ico' if os.name=='nt' else 'logo.png'))))
    font_id=QFontDatabase.addApplicationFont(str(ASSETS/'fonts'/'Vazirmatn-Regular.ttf'))
    families=QFontDatabase.applicationFontFamilies(font_id)
    app.setFont(QFont(families[0] if families else 'Segoe UI',10))
    if os.name=='nt':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Tixi.Voice.Desktop.1')
        except Exception:pass
    root=(args.data_dir or data_directory()).resolve()
    try:root.mkdir(parents=True,exist_ok=True)
    except OSError:
        message(None,'پوشهٔ داده قابل ساخت نیست. فضای دیسک و دسترسی حساب ویندوز را بررسی کن.');return 1
    logger=logging.getLogger('tixi.voice');logger.setLevel(logging.WARNING)
    handler=RotatingFileHandler(root/'application.log',maxBytes=1024*1024,backupCount=2,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));logger.addHandler(handler)
    lock=QLockFile(str(root/'desktop.lock'));lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        message(None,'برنامه برای این پوشه باز است یا پوشه قابل نوشتن نیست. پنجرهٔ قبلی را بررسی کن.');return 1
    store=None
    def unhandled(kind,value,tb):
        logger.error('Unhandled exception',exc_info=(kind,value,tb));message(app.activeWindow(),'خطای غیرمنتظره رخ داد. داده‌های ثبت‌شده حذف نشده‌اند؛ application.log را بررسی کن.')
    sys.excepthook=unhandled
    try:
        store=Store(root)
        # Purge only transient app-owned jobs left by an abnormal exit, after taking the lock.
        for folder in ('temp','recordings'):
            for p in (root/folder).iterdir():
                try:
                    if p.is_dir():shutil.rmtree(p)
                    else:p.unlink()
                except OSError:logger.warning('Temporary cleanup skipped')
        w=MainWindow(store);w.show();return app.exec()
    except Exception:
        logger.exception('Startup failed');message(None,'برنامه باز نشد. پایگاه داده بازنشانی نشده است. جزئیات در:\n'+str(root/'application.log'));return 1
    finally:
        if store:store.close()
        lock.unlock()

if __name__=='__main__':
    raise SystemExit(main())
