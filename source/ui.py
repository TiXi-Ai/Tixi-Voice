# SPDX-License-Identifier: GPL-3.0-or-later
"""Persian RTL neon desktop interface; all inference/downloads run outside the GUI thread."""
from __future__ import annotations
import json
import logging
import math
import shutil
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize, QUrl
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QFont, QDesktopServices, QShortcut, QKeySequence, QTextOption
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QPlainTextEdit, QLineEdit, QComboBox, QSlider, QStackedWidget, QScrollArea,
    QFileDialog, QMessageBox, QDialog, QProgressBar, QSizePolicy, QToolButton)
import theme
from core import (ASSETS, ROOT, MANIFEST, MAX_TEXT, UserError, Cancelled, atomic_bytes, atomic_copy, make_srt, valid_text)
from models import Models
from audio import Recorder, Player, inspect_audio
from engines import synthesize, transcribe
from personal_ui import PersonalVoiceMixin
from personal_voice import KEY as PERSONAL_KEY, personalized_speech
from effects import EFFECTS
from dictation import DictationController, HOTKEY_LABEL
from dictation_overlay import DictationOverlay

LOG=logging.getLogger('tixi.voice')
theme.PATHS.update({
 'mic':'<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
 'wave':'<path d="M3 9v6M7 5v14M12 2v20M17 6v12M21 9v6"/>',
 'play':'<path d="m8 4 13 8-13 8z"/>',
 'stop':'<rect x="5" y="5" width="14" height="14" rx="2"/>',
 'clock':'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 3"/>',
 'refresh':'<path d="M20 7a9 9 0 1 0 1 7M20 3v5h-5"/>',
 'headphones':'<path d="M3 14v-3a9 9 0 0 1 18 0v3"/><rect x="3" y="12" width="4" height="8" rx="2"/><rect x="17" y="12" width="4" height="8" rx="2"/>',
})

def label(text, role='', name=''):
    w=QLabel(text);w.setTextFormat(Qt.TextFormat.PlainText);w.setWordWrap(True)
    if role:w.setProperty('role',role)
    if name:w.setObjectName(name)
    return w

def row(*widgets, stretch=False):
    l=QHBoxLayout();l.setSpacing(10)
    for w in widgets:l.addWidget(w)
    if stretch:l.addStretch()
    return l

def card():
    w=QFrame();w.setObjectName('voiceCard');l=QVBoxLayout(w);l.setContentsMargins(22,20,22,20);l.setSpacing(14)
    return w,l

def duration(seconds):
    s=max(0,int(seconds));return f'{s//60:02}:{s%60:02}'

def message(parent,text,title='Tixi Voice',question=False):
    box=QMessageBox(parent);box.setWindowTitle(title);box.setTextFormat(Qt.TextFormat.PlainText);box.setText(text)
    if question:
        yes=box.addButton('بله، ادامه بده',QMessageBox.ButtonRole.AcceptRole)
        no=box.addButton('انصراف',QMessageBox.ButtonRole.RejectRole);box.setDefaultButton(no);box.exec()
        return box.clickedButton()==yes
    box.addButton('باشه',QMessageBox.ButtonRole.AcceptRole);box.exec()

class Job(QThread):
    progress=Signal(int,str);success=Signal(object);failure=Signal(str);cancelled=Signal()
    def __init__(self,fn,parent):
        super().__init__(parent);self.fn=fn;self.cancel=threading.Event()
    def run(self):
        try:
            result=self.fn(self.cancel,self.progress.emit)
            # The function owns cancellation cleanup. Never discard a completed audio result here.
            self.success.emit(result)
        except Cancelled:self.cancelled.emit()
        except UserError as e:self.failure.emit(str(e))
        except Exception:
            LOG.exception('Background task failed')
            self.failure.emit('عملیات کامل نشد؛ فضای خالی و دسترسی به فایل‌ها را بررسی کن. جزئیات در application.log ثبت شده است.')

class Waveform(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.setMinimumHeight(84);self.levels=[];self.position=0;self.colors=theme.DARK
    def load(self,path):
        import soundfile as sf
        import numpy as np
        with sf.SoundFile(str(path)) as f:
            count=80;levels=[]
            for i in range(count):
                f.seek(min(max(0,len(f)-1),int(i*len(f)/count)))
                x=f.read(min(512,len(f)-f.tell()),dtype='float32',always_2d=True)
                levels.append(float(np.max(np.abs(x))) if len(x) else 0)
        peak=max(levels,default=1) or 1;self.levels=[v/peak for v in levels];self.position=0;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        vals=self.levels or [0.07]*80
        width=self.width();height=self.height();step=width/len(vals)
        for i,v in enumerate(vals):
            c=self.colors['accent'] if self.position==0 or i/len(vals)<=self.position else self.colors['line']
            pen=QPen(QColor(c),max(2,min(4,step*0.48)),Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap)
            p.setPen(pen);h=max(4,v*(height-12));x=int((i+.5)*step)
            p.drawLine(x,int((height-h)/2),x,int((height+h)/2))
        p.end()

class MainWindow(PersonalVoiceMixin,QMainWindow):
    def __init__(self,store):
        super().__init__();self.store=store;self.models=Models(store.root);self.worker=None;self.recording=False;self.record_owner="stt";self.closing=False
        self.player=Player();self.recorder=Recorder();self.source=None;self.result_item=None;self.stt_item=None;self.play_wave=None
        self.dictation=DictationController(store,self._dictation_options,self)
        self.buttons=[];self.model_widgets={};self.nav=[];self.history_rows=[];self.colors=theme.DARK
        self.setWindowTitle('Tixi Voice — استودیوی گفتار آفلاین');self.resize(1320,890);self.setMinimumSize(1020,720)
        outer=QWidget();outer.setObjectName('mainPanel');self.setCentralWidget(outer)
        layout=QHBoxLayout(outer);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        self.sidebar=QFrame();self.sidebar.setObjectName('sidebar');self.sidebar.setFixedWidth(232)
        side=QVBoxLayout(self.sidebar);side.setContentsMargins(18,26,18,22);side.setSpacing(12)
        logo=QLabel();logo.setPixmap(QPixmap(str(ASSETS/'logo.png')).scaled(52,52,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));logo.setFixedSize(52,52)
        brand=QVBoxLayout();brand.addWidget(label('Tixi Voice',name='brandTitle'));brand.addWidget(label('YOUR OFFLINE VOICE STUDIO',name='brandSub'))
        brandrow=QHBoxLayout();brandrow.addWidget(logo);brandrow.addLayout(brand);side.addLayout(brandrow);side.addSpacing(28)
        side.addWidget(label('فضای شخصی صدای تو','faint'))
        self.pages=QStackedWidget()
        self.nav_indexes=[0,1,4,2,3,5]
        self.nav_icon_png={'wave':'waveform_outline','mic':'microphone_outline','headphones':'profile_circle','clock':'history_small','download':'download_outline','layers':'settings_gear_outline_circle'}
        for i,text,ic in [(0,'متن به صدا','wave'),(1,'صدا به متن','mic'),(4,'صدای من','headphones'),(2,'تاریخچه','clock'),(3,'مدل‌های آفلاین','download'),(5,'حالت نمایش','layers')]:
            b=self.nav_button(text,lambda i=i:self.navigate(i),png=self.nav_icon_png.get(ic,''));b.setObjectName('navButton');b.setMinimumWidth(190);side.addWidget(b);self.nav.append(b)
        side.addStretch()
        side.addWidget(label('قالب فعال','faint'))
        self.active_theme_label=label('','accent');self.active_theme_label.setWordWrap(True);side.addWidget(self.active_theme_label)
        private=QFrame();private.setObjectName('notice');pl=QVBoxLayout(private);pl.setSpacing(3)
        pl.addWidget(label('●  محلی و خصوصی','accent'));pl.addWidget(label('بدون حساب، بدون ارسال متن و صوت','faint'));side.addWidget(private)
        self.model_count=label('','faint');side.addWidget(self.model_count)
        side.addWidget(self.button('راهنما و اطلاعات',self.about,'shield',role='ghost'))
        side.addWidget(label('DESKTOP 1.1   /   LOCAL · OFFLINE','faint'))
        layout.addWidget(self.sidebar)
        body=QWidget();body_layout=QVBoxLayout(body);body_layout.setContentsMargins(28,20,28,15);body_layout.setSpacing(15)
        top=QHBoxLayout();kicker=label('●  استودیوی گفتار شخصی','accent');kicker.setMinimumWidth(240);top.addWidget(kicker);top.addStretch();top.addWidget(label('فارسی  /  ENGLISH','faint'));body_layout.addLayout(top)
        body_layout.addWidget(self.pages,1)
        self.status=label('آماده · مدل‌ها را یک‌بار نصب کن؛ بعد کاملاً آفلاین کار کن.','muted')
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);self.progress.setTextVisible(False);self.progress.setFixedHeight(5);self.progress.hide()
        self.cancel_btn=self.button('لغو عملیات',self.cancel_job,'x');self.cancel_btn.hide()
        statusrow=QHBoxLayout();statusrow.addWidget(self.status,1);statusrow.addWidget(self.cancel_btn)
        body_layout.addWidget(self.progress);body_layout.addLayout(statusrow);layout.addWidget(body,1)
        self.dictation.status.connect(self.status.setText)
        self.dictation.failed.connect(self.status.setText)
        self.dictation.recording.connect(lambda on:self._dictation_phase(on,'در حال ضبط…'))
        self.dictation.working.connect(lambda on:self._dictation_phase(on,'در حال تبدیل…'))
        self.dictation_overlay=DictationOverlay()
        self.dictation.recording.connect(self._dictation_overlay)
        self.dictation.working.connect(self._dictation_working_overlay)
        self.build_tts();self.build_stt();self.build_history();self.build_models();self.build_personal_voice_page();self.build_appearance()
        self.set_theme(store.setting('theme','midnight'));self.navigate(0);self.refresh_models();self.refresh_history()
        self.tts_text.setPlainText(store.setting('tts_draft',''))
        self.stt_text.setPlainText(store.setting('stt_draft',''))
        if store.setting('dictation_enabled','1')=='1' and not self.dictation.install(QApplication.instance()):
            self.store.set_setting('dictation_enabled','0')
        self._refresh_dictation_state()
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(120)
        self.save_timer=QTimer(self);self.save_timer.setSingleShot(True);self.save_timer.setInterval(800);self.save_timer.timeout.connect(self.save_drafts)
        self.tts_text.textChanged.connect(self.save_timer.start);self.stt_text.textChanged.connect(self.save_timer.start)
        QShortcut(QKeySequence('Ctrl+Return'),self,activated=lambda:self.guard(self.run_tts if self.pages.currentIndex()==0 else self.run_stt if self.pages.currentIndex()==1 else lambda:None))
        QShortcut(QKeySequence('Escape'),self,activated=lambda:self.guard(self.cancel_job if self.worker else self.stop_playback))
        QShortcut(QKeySequence('Ctrl+O'),self,activated=lambda:self.guard(self.choose_audio if self.pages.currentIndex()==1 and not self.worker and not self.recording else self.import_text if self.pages.currentIndex()==0 else lambda:None))
        for page_index in range(5):
            QShortcut(QKeySequence(f'Ctrl+{page_index+1}'),self,activated=lambda i=page_index:self.navigate(i))

    def guard(self,fn):
        try:return fn()
        except UserError as e:message(self,str(e))
        except Exception:
            LOG.exception('UI action failed');message(self,'عملیات انجام نشد. دسترسی فایل‌ها، فضای دیسک و تنظیمات صدا را بررسی کن. جزئیات در application.log ثبت شده است.')

    def button(self,text,fn,ic='',role='',png=''):
        b=QPushButton(text);b.setCursor(Qt.CursorShape.PointingHandCursor);b.setMinimumHeight(36)
        if role:b.setProperty('role',role)
        if png:
            b.setIcon(theme.icon_file(png));b.setIconSize(QSize(22,22))
        elif ic:
            self.buttons.append((b,ic,role))
            b.setIcon(theme.icon(ic,self.colors['accentText'] if role=='primary' else self.colors['muted']));b.setIconSize(QSize(18,18))
        b.clicked.connect(lambda checked=False:self.guard(fn));return b

    def nav_button(self,text,fn,png=''):
        b=QToolButton();b.setText(text);b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setIconSize(QSize(26,26))
        if png:b.setIcon(theme.icon_file(png))
        b.setMinimumHeight(44)
        b.clicked.connect(lambda checked=False:self.guard(fn));return b

    def page(self,title,sub):
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        w=QWidget();v=QVBoxLayout(w);v.setContentsMargins(0,0,9,12);v.setSpacing(18)
        v.addWidget(label(title,name='heading'));v.addWidget(label(sub,'muted'));scroll.setWidget(w);self.pages.addWidget(scroll)
        return v

    def text_edit(self,placeholder):
        w=QPlainTextEdit();w.setPlaceholderText(placeholder);w.setTabChangesFocus(True)
        option=w.document().defaultTextOption();option.setTextDirection(Qt.LayoutDirection.LayoutDirectionAuto);w.document().setDefaultTextOption(option)
        w.setMinimumHeight(235);return w

    def build_tts(self):
        v=self.page('از کلمه، صدا بساز.','متن فارسی یا انگلیسی را وارد کن؛ صدایت همین‌جا و روی کامپیوتر ساخته می‌شود.')
        grid=QHBoxLayout();grid.setSpacing(16)
        textcard,t=card();h=QHBoxLayout();h.addWidget(label('۰۱  /  متن تو','accent'));h.addStretch()
        h.addWidget(self.button('فایل متنی',self.import_text,'upload',role='ghost'));t.addLayout(h)
        self.tts_text=self.text_edit('اینجا بنویس…\n\nبرای مثال: سلام! به استودیوی صدای تیکسی خوش آمدی.');self.tts_text.setObjectName('ttsText');t.addWidget(self.tts_text,1)
        bottom=QHBoxLayout();self.text_count=label('۰ / ۱۰٬۰۰۰ کاراکتر','faint');self.text_count.setMinimumWidth(220);bottom.addWidget(self.text_count);bottom.addStretch();bottom.addWidget(self.button('پاک کردن',self.clear_tts,'trash',role='ghost'));t.addLayout(bottom)
        self.tts_text.textChanged.connect(lambda:self.text_count.setText(f'{len(self.tts_text.toPlainText()):,} کاراکتر · سقف 10,000'))
        controls,c=card();controls.setFixedWidth(258);c.addWidget(label('۰۲  /  تنظیمات صدا','accent'));c.addWidget(label('زبان و گوینده','muted'))
        self.voice=QComboBox();self.voice.addItem('فارسی · امیر (آقا)','tts-fa');self.voice.addItem('English · LJ Speech (خانم)','tts-en');self.voice.addItem('English · Lessac (خانم)','tts-en-lessac');self.voice.addItem('English · Amy (خانم)','tts-en-amy');c.addWidget(self.voice)
        self.tts_ready=label('','faint');c.addWidget(self.tts_ready);self.voice.currentIndexChanged.connect(self.refresh_models)
        self.install_personal_selector(c)
        c.addSpacing(8);c.addWidget(label('افکت صدا','muted'))
        self.effect=QComboBox()
        for key,(name,_desc) in EFFECTS.items():self.effect.addItem(name,key)
        self.effect.setCurrentIndex(max(0,self.effect.findData(self.store.setting('effect','none'))))
        self.effect.currentIndexChanged.connect(lambda *_:self.store.set_setting('effect',self.effect.currentData()))
        c.addWidget(self.effect)
        self.effect_hint=label(EFFECTS[self.effect.currentData()][1],'faint');c.addWidget(self.effect_hint)
        self.effect.currentIndexChanged.connect(lambda *_:self.effect_hint.setText(EFFECTS[self.effect.currentData()][1]))
        c.addSpacing(8);self.speed_label=label('سرعت خواندن  ·  1.00×','muted');c.addWidget(self.speed_label)
        self.speed=QSlider(Qt.Orientation.Horizontal);self.speed.setLayoutDirection(Qt.LayoutDirection.RightToLeft);self.speed.setRange(60,160);self.speed.setValue(100);self.speed.valueChanged.connect(lambda x:self.speed_label.setText(f'سرعت خواندن  ·  {x/100:.2f}×'));c.addWidget(self.speed)
        speedends=QHBoxLayout();speedends.setDirection(QHBoxLayout.Direction.LeftToRight);speedends.addWidget(label('آرام‌تر','faint'));speedends.addStretch();speedends.addWidget(label('سریع‌تر','faint'));c.addLayout(speedends);c.addStretch();c.addWidget(label('تلفظ از مدل پایه؛ با انتخاب پروفایل، رنگ صدای شخصی به آن نزدیک می‌شود.','faint'))
        self.tts_run=self.button('ساخت صدا',self.run_tts,'sparkles','primary');self.tts_run.setMinimumHeight(43);c.addWidget(self.tts_run)
        c.addWidget(label('Ctrl + Enter   ·   خروجی WAV','faint'));grid.addWidget(textcard,1);grid.addWidget(controls);v.addLayout(grid,1)
        result,r=card();head=QHBoxLayout();head.addWidget(label('۰۳  /  بشنو و ذخیره کن','accent'));head.addStretch();self.audio_time=label('هنوز صدایی ساخته نشده','faint');self.audio_time.setMinimumWidth(255);head.addWidget(self.audio_time);r.addLayout(head)
        self.wave=Waveform();r.addWidget(self.wave)
        self.play_btn=self.button('پخش صدا',self.play_result,'play');self.stop_btn=self.button('توقف',self.stop_playback,'stop');self.save_audio_btn=self.button('ذخیرهٔ WAV',self.export_result,'download','primary')
        controlsrow=row(self.play_btn,self.stop_btn);controlsrow.addStretch();controlsrow.addWidget(self.save_audio_btn);r.addLayout(controlsrow)
        self.play_btn.setEnabled(False);self.stop_btn.setEnabled(False);self.save_audio_btn.setEnabled(False);v.addWidget(result)

    def build_stt(self):
        v=self.page('صدایت را به متن بسپار.','فایل صوتی انتخاب کن یا ضبط کن؛ تشخیص گفتار آفلاین است و نتیجه نیاز به بازبینی دارد.')
        inputs,il=card();top=QHBoxLayout();self.file_btn=self.button('انتخاب فایل صوتی',self.choose_audio,'upload');top.addWidget(self.file_btn)
        self.file_label=label('WAV · MP3 · FLAC · OGG  /  حداکثر ۹۰ دقیقه','muted');top.addWidget(self.file_label,1);il.addLayout(top)
        mic=QHBoxLayout();self.device=QComboBox();self.device.addItem('میکروفون پیش‌فرض',None);mic.addWidget(self.device,1)
        self.device_refresh=self.button('ورودی‌ها',self.refresh_devices,'refresh');mic.addWidget(self.device_refresh)
        self.record_btn=self.button('شروع ضبط',self.toggle_record,'mic');mic.addWidget(self.record_btn)
        self.record_export=self.button('ذخیرهٔ ضبط',self.export_recording,'download');self.record_export.setEnabled(False);mic.addWidget(self.record_export);il.addLayout(mic)
        self.mic_status=label('میکروفون فقط با زدن «شروع ضبط» فعال می‌شود. ضبط زنده به متن نیست؛ پس از توقف، تبدیل را بزن.','faint');il.addWidget(self.mic_status)
        self.mic_meter=QProgressBar();self.mic_meter.setRange(0,100);self.mic_meter.setValue(0);self.mic_meter.setTextVisible(False);self.mic_meter.setFixedHeight(5);il.addWidget(self.mic_meter)
        settings=QHBoxLayout();self.stt_language=QComboBox()
        for name,key in [('فارسی','fa'),('English','en'),('تشخیص خودکار زبان','auto')]:self.stt_language.addItem(name,key)
        self.stt_model=QComboBox();self.stt_model.addItem('Small · دقت بیشتر','stt-small');self.stt_model.addItem('Base · سبک‌تر','stt-base');self.stt_model.addItem('Medium · دقت بالاتر / کندتر','stt-medium')
        settings.addWidget(label('زبان','muted'));settings.addWidget(self.stt_language,1);settings.addWidget(label('مدل','muted'));settings.addWidget(self.stt_model,1)
        self.stt_run=self.button('تبدیل به متن',self.run_stt,'sparkles','primary');settings.addWidget(self.stt_run);il.addLayout(settings);v.addWidget(inputs)
        dict_card,dl=card();dh=QHBoxLayout();dh.addWidget(label('تایپ با صدا، در هر برنامه‌ای','accent'));dh.addStretch()
        self.dictation_hotkey=label(HOTKEY_LABEL,'faint');dh.addWidget(self.dictation_hotkey);dl.addLayout(dh)
        dl.addWidget(label('دیکتهٔ سراسری: در هر اپی (مرورگر، ورد، تلگرام و هر جای قابل تایپ) کلید را نگه دار و صحبت کن؛ بعد از رها کردن، متن در همان پنجره تایپ می‌شود. زبان فارسی و انگلیسی خودکار تشخیص داده می‌شود و صدا از اینترنت عبور نمی‌کند.','muted'))
        drow=QHBoxLayout();self.dictation_toggle=self.button('فعال‌سازی دیکته',self.toggle_dictation,'mic','primary')
        drow.addWidget(self.dictation_toggle);self.dictation_state=label('○  غیرفعال','faint');drow.addWidget(self.dictation_state);drow.addStretch()
        dl.addLayout(drow);dl.addWidget(label('کلید سراسری حتی وقتی این برنامه پشت پنجرهٔ دیگری است کار می‌کند؛ ورودی صدا همان میکروفون انتخاب‌شده در بالاست.','faint'))
        v.addWidget(dict_card)
        result,rl=card();head=QHBoxLayout();head.addWidget(label('متن پیاده‌شده','accent'));head.addStretch();self.stt_meta=label('متن تولیدشده به‌صورت خودکار در تاریخچه ذخیره می‌شود.','faint');self.stt_meta.setMinimumWidth(320);head.addWidget(self.stt_meta);rl.addLayout(head)
        self.stt_text=self.text_edit('متن گفتار اینجا ظاهر می‌شود…');self.stt_text.setObjectName('sttText');rl.addWidget(self.stt_text,1)
        self.stt_text.textChanged.connect(self.transcript_changed)
        self.copy_text_btn=self.button('کپی متن',lambda:self.copy_text(self.stt_text.toPlainText()),'copy')
        self.export_text_btn=self.button('ذخیرهٔ TXT',lambda:self.export_text(self.stt_text.toPlainText()),'download')
        self.export_srt_btn=self.button('زیرنویس SRT',self.export_current_srt,'download');self.export_srt_btn.setEnabled(False)
        rl.addLayout(row(self.copy_text_btn,self.export_text_btn,self.export_srt_btn,stretch=True))
        rl.addWidget(label('می‌توانی متن را ویرایش و TXT ذخیره کنی. تاریخچه متن اولیه را نگه می‌دارد؛ SRT فقط پیش از ویرایش متن فعال است. زمان‌بندی‌ها تقریبی‌اند.','faint'));v.addWidget(result,1)

    def build_history(self):
        v=self.page('هر تبدیل، یک جای امن.','متن‌های پیاده‌شده و صداهای ساخته‌شده فقط روی این کامپیوتر ذخیره می‌شوند.')
        h=QHBoxLayout();self.history_search=QLineEdit();self.history_search.setPlaceholderText('جست‌وجو در متن یا نام فایل…');self.history_search.setObjectName('search');h.addWidget(self.history_search,1)
        self.history_kind=QComboBox();self.history_kind.addItem('همهٔ تبدیل‌ها','');self.history_kind.addItem('متن به صدا','tts');self.history_kind.addItem('صدا به متن','stt');h.addWidget(self.history_kind);v.addLayout(h)
        self.history_count=label('','faint');v.addWidget(self.history_count)
        self.history_area=QWidget();self.history_layout=QVBoxLayout(self.history_area);self.history_layout.setContentsMargins(0,0,0,0);self.history_layout.setSpacing(12);v.addWidget(self.history_area);v.addStretch()
        self.history_debounce=QTimer(self);self.history_debounce.setSingleShot(True);self.history_debounce.setInterval(230);self.history_debounce.timeout.connect(self.refresh_history)
        self.history_search.textChanged.connect(lambda *_:self.history_debounce.start());self.history_kind.currentIndexChanged.connect(self.refresh_history)

    def build_models(self):
        v=self.page('یک‌بار دانلود؛ همیشه آفلاین.','فقط نصب مدل اینترنت می‌خواهد. متن، صدا و تاریخچه به هیچ سرویس ابری ارسال نمی‌شوند.')
        notice,nl=card();nl.addWidget(label('شروع پیشنهادی','accent'));nl.addWidget(label('برای متن به صدا: مدل فارسی یا انگلیسی. برای صدا به متن: Whisper Small. اگر سرعت مهم‌تر است Base را انتخاب کن. مدل‌ها اختیاری و جداگانه نصب می‌شوند.','muted'));nl.addWidget(label('صدای عادی و تشخیص روی CPU؛ صدای شخصی با DirectML سازگار یا CPU کم‌مصرف. ویندوز x64 و ترجیحاً ۸GB RAM؛ ضبط و پخش به دستگاه صوتی فعال نیاز دارند.','faint'));v.addWidget(notice)
        for key,info in MANIFEST.items():
            w,l=card();h=QHBoxLayout();h.addWidget(label(info['title'],name='sectionTitle'),1);size=sum(f['size'] for f in info['files'])/1048576;h.addWidget(label(f'{size:.1f} MB','faint'));l.addLayout(h)
            l.addWidget(label(info['description'],'muted'));status=label('','accent');l.addWidget(status)
            install=self.button('دانلود و نصب',lambda key=key:self.install_model(key),'download','primary')
            local=self.button('نصب از پوشه',lambda key=key:self.install_local(key),'folder')
            remove=self.button('حذف مدل',lambda key=key:self.remove_model(key),'trash',role='ghost')
            l.addLayout(row(install,local,remove,stretch=True));self.model_widgets[key]=(status,install,local,remove);v.addWidget(w)
        v.addWidget(self.button('باز کردن پوشهٔ داده‌ها',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root))),'folder'))
        v.addWidget(label('دانلودها از Hugging Face با نسخهٔ ثابت و بررسی SHA-256 هستند. اگر دسترسی نداری، فایل‌های رسمی معرفی‌شده در راهنما را روی دستگاه دیگری بگیر و با «نصب از پوشه» وارد کن. هیچ مدل تصادفی یا تغییرکرده‌ای پذیرفته نمی‌شود.','faint'));v.addStretch()

    def navigate(self,index):
        self.pages.setCurrentIndex(index)
        for i,b in zip(self.nav_indexes,self.nav):
            b.setProperty('active',i==index);b.style().unpolish(b);b.style().polish(b)
        if index==2 and hasattr(self,'history_layout'):self.refresh_history()
        if index==4 and hasattr(self,'profile_layout'):self.refresh_profiles()
        if index==3:self.refresh_models()
        if index==5 and hasattr(self,'refresh_theme_cards'):self.refresh_theme_cards()

    def build_appearance(self):
        v=self.page('ظاهر را رنگ بزن.','یک قالب رنگی انتخاب کن تا حال‌وهوای کل برنامه تغییر کند؛ همه‌چیز روی همین کامپیوتر است.')
        self.theme_icon={
            'ios':'waveform_tile','mint':'microphone_tile','peach':'user_tile','lavender':'settings_gear_circle',
            'ocean':'download_tile','rose':'notification_bell_circle','golden':'history_tile',
            'midnight':'models_cube_tile','royal':'user_tile','emerald':'waveform_tile'}
        v.addWidget(label('قالب‌های روشن','accent'))
        grid=QGridLayout();grid.setSpacing(12)
        self.theme_cards={}
        light_keys=['ios','mint','peach','lavender','ocean','rose','golden']
        for i,key in enumerate(light_keys):
            card_w=self.theme_card(key)
            grid.addWidget(card_w,i//3,i%3)
        v.addLayout(grid)
        v.addSpacing(8);v.addWidget(label('قالب‌های تیره','accent'))
        dgrid=QGridLayout();dgrid.setSpacing(12)
        dark_keys=['midnight','royal','emerald']
        for i,key in enumerate(dark_keys):
            dgrid.addWidget(self.theme_card(key),0,i)
        v.addLayout(dgrid)
        v.addStretch()

    def theme_card(self,key):
        p=theme.PALETTES[key]
        w=QFrame();w.setObjectName('themeCard');w.setProperty('selected','false')
        l=QVBoxLayout(w);l.setContentsMargins(14,14,14,14);l.setSpacing(10)
        prev=QFrame();prev.setFixedHeight(80)
        prev.setStyleSheet('''QFrame{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 %s,stop:0.55 %s,stop:1 %s);border:1px solid %s;border-radius:12px;}'''%(p['gradientA'],p['gradientB'],p['gradientC'],p['line']))
        pv=QVBoxLayout(prev);pv.setContentsMargins(0,0,0,0);pv.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_png=self.theme_icon.get(key)
        if preview_png:
            pic=QPixmap(str(theme._icon_dir/f'{preview_png}.png'))
            if not pic.isNull():
                pic=pic.scaled(46,46,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
                lab=QLabel();lab.setPixmap(pic);lab.setAlignment(Qt.AlignmentFlag.AlignCenter);pv.addWidget(lab)
        l.addWidget(prev)
        l.addWidget(label(theme.NAMES.get(key,key),name='sectionTitle'))
        choose=self.button('انتخاب',lambda k=key:self.set_theme(k),'check','primary' if self.store.setting('theme','midnight')==key else '')
        l.addWidget(choose)
        self.theme_cards[key]=(w,prev,choose)
        return w

    def refresh_theme_cards(self,*_):
        if not hasattr(self,'theme_cards'):return
        current=self.store.setting('theme','midnight')
        for key,(w,prev,choose) in self.theme_cards.items():
            sel=key==current
            w.setProperty('selected','true' if sel else 'false')
            w.style().unpolish(w);w.style().polish(w)
            choose.setProperty('role','primary' if sel else '')
            choose.setText('✓ انتخاب شد' if sel else 'انتخاب')
            choose.style().unpolish(choose);choose.style().polish(choose)

    def set_theme(self,key):
        if key not in theme.PALETTES:
            key={'light':'ios','dark':'midnight','system':('midnight' if QApplication.instance().styleHints().colorScheme()==Qt.ColorScheme.Dark else 'ios')}.get(key,'midnight')
        self.store.set_setting('theme',key)
        c=self.colors=theme.PALETTES[key]
        theme.apply_palette(QApplication.instance(),c)
        if hasattr(self,'dictation_overlay'):self.dictation_overlay.set_colors(c)
        extra='''QFrame#voiceCard {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %(glassTop)s,stop:1 %(glassBottom)s),qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 %(panel)s,stop:1 %(panel)s);border:1px solid %(line)s;border-top:1px solid %(glassTop)s;border-radius:18px;}
        QLabel#sectionTitle {font-size:17px;font-weight:bold;}
        QSlider::groove:horizontal {height:5px;background:%(line)s;border-radius:3px;}
        QSlider::sub-page:horizontal {background:%(accent)s;border-radius:3px;}
        QSlider::handle:horizontal {background:%(accent)s;border:3px solid %(panel)s;width:18px;height:18px;margin:-8px 0;border-radius:11px;}
        QProgressBar {border:0;background:%(line)s;border-radius:3px;}
        QProgressBar::chunk {background:%(accent)s;border-radius:3px;}
        QPlainTextEdit {font-size:14px;}
        QPushButton[role="primary"]:disabled {background:%(soft)s;color:%(faint)s;border-color:%(line)s;}
        QFrame#themeCard {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %(glassTop)s,stop:1 %(glassBottom)s),qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 %(panel)s,stop:1 %(panel)s);border:2px solid %(line)s;border-radius:16px;}
        QFrame#themeCard[selected="true"] {border:2px solid %(accent)s;}
        '''%c
        QApplication.instance().setStyleSheet(QApplication.instance().styleSheet()+extra)
        alive=[]
        for b,ic,role in self.buttons:
            try:b.setIcon(theme.icon(ic,c['accentText'] if role=='primary' else c['muted']));b.setIconSize(QSize(18,18));alive.append((b,ic,role))
            except RuntimeError:pass
        self.buttons=alive
        if hasattr(self,'wave'):self.wave.colors=c;self.wave.update()
        if hasattr(self,'active_theme_label'):self.active_theme_label.setText('●  '+theme.NAMES.get(key,key))
        self.refresh_theme_cards()

    def refresh_models(self,*_):
        count=sum(self.models.ready(k) for k in MANIFEST)
        self.model_count.setText(f'{count} از {len(MANIFEST)} مدل نصب‌شده')
        if hasattr(self,'voice'):self.tts_ready.setText('●  آمادهٔ ساخت صدای آفلاین' if self.models.ready(self.voice.currentData()) else '○  ابتدا این مدل را از بخش مدل‌ها نصب کن.')
        for key,(status,install,local,remove) in self.model_widgets.items():
            ready=self.models.ready(key);busy=bool(self.worker or self.recording)
            status.setText('●  نصب‌شده · سلامت فایل قبل از تبدیل بررسی می‌شود' if ready else '○  هنوز نصب نشده')
            install.setEnabled(not busy and not ready);local.setEnabled(not busy);remove.setEnabled(not busy and (self.models.root/key).exists())
        self.refresh_personal_resources()

    def busy_controls(self,busy):
        for b in [self.tts_run,self.stt_run,self.file_btn,self.device,self.device_refresh,self.voice,self.stt_model,self.stt_language,self.speed,self.effect]:b.setEnabled(not busy)
        self.record_btn.setEnabled(not self.worker)
        if hasattr(self,'dictation_toggle'):self.dictation_toggle.setEnabled(not busy)
        self.refresh_models();self.personal_busy_controls(busy)

    def start_job(self,fn,complete,text):
        if self.worker or self.recording:raise UserError('ابتدا عملیات یا ضبط جاری را تمام کن.')
        self.stop_playback();self.worker=Job(fn,self);job=self.worker
        job.progress.connect(self.job_progress);job.success.connect(lambda result:self.guard(lambda:complete(result)))
        job.failure.connect(lambda text:self.task_error(text));job.cancelled.connect(lambda:self.status.setText('عملیات لغو شد.'))
        job.finished.connect(self.job_finished);self.cancel_btn.show();self.progress.show();self.progress.setRange(0,0);self.status.setText(text);self.busy_controls(True);job.start()

    def job_progress(self,percent,text):
        if percent<0:self.progress.setRange(0,0)
        else:self.progress.setRange(0,100);self.progress.setValue(percent)
        self.status.setText(text)

    def task_error(self,text):
        self.status.setText('عملیات کامل نشد؛ جزئیات خطا را بررسی کن.')
        if not self.closing:message(self,text)

    def job_finished(self):
        job=self.worker;self.worker=None
        if job:job.deleteLater()
        self.progress.hide();self.cancel_btn.hide();self.busy_controls(False);self.refresh_history()
        if self.closing:QTimer.singleShot(0,self.close)

    def cancel_job(self):
        if self.worker:
            self.worker.cancel.set();self.status.setText('در حال لغو… دانلود ممکن است تا ۱۲ ثانیه برای قطع اتصال زمان بخواهد.')

    def install_model(self,key):
        size=sum(f['size'] for f in MANIFEST[key]['files'])/1048576
        text=f'مدل «{MANIFEST[key]["title"]}» با حجم حدود {size:.1f} MB از Hugging Face دانلود شود؟ متن یا صوت ارسال نمی‌شود.'
        if key==PERSONAL_KEY:text=f'مدل صدای شخصی از Hugging Face و افزونهٔ رسمی ONNX Runtime DirectML از PyPI دریافت شوند؟ مجموع حدود {size:.1f} MB. افزونه شامل کد اجرایی مایکروسافت برای شتاب‌دهی اختیاری است. فایل‌ها با SHA-256 بررسی می‌شوند؛ هیچ صوتی ارسال نمی‌شود.'
        if not message(self,text,question=True):return
        self.start_job(lambda c,p:self.models.install(key,c,p),lambda _:self.status.setText('مدل نصب شد؛ اکنون بدون اینترنت تبدیل کن.'),'آماده‌سازی دانلود…')

    def install_local(self,key):
        names='\n'.join(f['name'] for f in MANIFEST[key]['files'])
        message(self,'پوشه‌ای را انتخاب کن که این فایل‌های کامل و اصلی را دارد:\n'+names)
        folder=QFileDialog.getExistingDirectory(self,'پوشهٔ فایل‌های مدل')
        if folder:self.start_job(lambda c,p:self.models.install(key,c,p,Path(folder)),lambda _:self.status.setText('مدل محلی تأیید و نصب شد.'),'در حال نصب مدل از پوشه…')

    def remove_model(self,key):
        if self.worker or self.recording:return
        if message(self,'مدل حذف شود؟ تاریخچه و خروجی‌ها حذف نمی‌شوند؛ برای تبدیل بعدی باید دوباره مدل را نصب کنی.',question=True):
            self.models.remove(key);self.refresh_models()

    def require_model(self,key):
        if not self.models.ready(key):
            self.navigate(3);raise UserError('ابتدا مدل «'+MANIFEST[key]['title']+'» را دانلود یا از پوشه نصب کن.')

    def run_tts(self):
        if self.worker or self.recording:raise UserError('عملیات یا ضبط جاری را تمام کن.')
        text=valid_text(self.tts_text.toPlainText());key=self.voice.currentData();speed=self.speed.value()/100
        effect=self.effect.currentData()
        self.require_model(key)
        profile_id=self.persona.currentData()
        if profile_id:
            self.require_model(PERSONAL_KEY)
            profile=self.store.profile(profile_id)
            if not profile:raise UserError('پروفایل انتخاب‌شده وجود ندارد؛ دوباره انتخاب کن.')
            embedding=self.store.profile_directory(profile_id)/'embedding.npy';mode=self.store.setting('clone_mode','auto')
            self.start_job(lambda c,p:personalized_speech(self.store.root,key,text,speed,profile,embedding,mode,c,p,effect),self.tts_done,'شروع ساخت صدای شخصی؛ پردازش محلی…')
        else:
            self.start_job(lambda c,p:synthesize(self.store.root,key,text,speed,c,p,effect),self.tts_done,'شروع ساخت صدا…')

    def tts_done(self,result):
        try:
            item=self.store.add('tts',result['language'],result['model'],result['text'],source=result.get('source',''),audio=result['audio'],duration=result['duration'])
            self.result_item=item;path=self.store.audio_path(item);self.wave.load(path)
            self.audio_time.setText(f'WAV · {duration(item["duration"])} · ذخیره‌شده در تاریخچه')
            self.play_btn.setEnabled(True);self.save_audio_btn.setEnabled(True)
            if result['model']==PERSONAL_KEY:
                self.status.setText('صدای شخصیِ مصنوعی آماده است · '+result.get('provider','CPU')+(' · بازیابی خودکار کم‌مصرف' if result.get('fallback') else ''))
                self.audio_time.setText(f'صدای مصنوعی · {duration(item["duration"])} · '+result.get('provider','CPU'))
            else:self.status.setText('صدا آماده است؛ پخش کن یا خروجی WAV بگیر.')
            if result.get('effect'):self.status.setText(self.status.text()+' · افکت: '+EFFECTS[result['effect']][0])
            if self.pages.currentIndex()==0:self.pages.widget(0).ensureWidgetVisible(self.wave,0,90)
        finally:shutil.rmtree(result['job'],ignore_errors=True)

    def play_result(self):
        if not self.result_item:return
        p=self.store.audio_path(self.result_item)
        if not p:raise UserError('فایل صوتی این نتیجه پیدا نشد.')
        self.play_audio(p,self.wave)

    def play_audio(self,path,waveform=None):
        if self.recording:raise UserError('برای جلوگیری از ضبط صدای خروجی، ابتدا ضبط را متوقف کن.')
        self.player.play(path);self.play_wave=waveform;self.stop_btn.setEnabled(True);self.status.setText('در حال پخش… برای توقف Esc را بزن.')

    def stop_playback(self):
        self.player.stop()
        if self.play_wave:self.play_wave.position=0;self.play_wave.update()
        self.play_wave=None
        if hasattr(self,'stop_btn'):self.stop_btn.setEnabled(False)

    def clear_tts(self):
        if not self.tts_text.toPlainText() or message(self,'متن ورودی پاک شود؟ صداهای ذخیره‌شده در تاریخچه باقی می‌مانند.',question=True):self.tts_text.clear()

    def import_text(self):
        name,_=QFileDialog.getOpenFileName(self,'انتخاب فایل متنی','','Text (*.txt)')
        if not name:return
        p=Path(name)
        if p.stat().st_size>200000:raise UserError('فایل متنی بیش از حد بزرگ است؛ حداکثر متن ۱۰٬۰۰۰ کاراکتر است.')
        raw=p.read_bytes()
        try:text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
        except UnicodeError:raise UserError('فایل را با کدگذاری UTF-8 یا UTF-16 ذخیره کن.')
        valid_text(text)
        if self.tts_text.toPlainText().strip() and not message(self,'متن فعلی با محتوای فایل جایگزین شود؟',question=True):return
        self.tts_text.setPlainText(text)

    def set_source(self,path):
        info=inspect_audio(Path(path));self.source=Path(path)
        self.file_label.setText(f'{self.source.name}  ·  {duration(info.duration)}  ·  {info.samplerate:,} Hz')
        self.record_export.setEnabled(self.source.parent==self.store.root/'recordings')

    def choose_audio(self):
        name,_=QFileDialog.getOpenFileName(self,'انتخاب فایل صوتی','','Audio (*.wav *.mp3 *.flac *.ogg)')
        if name:self.set_source(Path(name))

    def refresh_devices(self):
        devices=self.recorder.devices()
        for combo in [self.device]+([self.clone_device] if hasattr(self,'clone_device') else []):
            current=combo.currentData();combo.clear();combo.addItem('میکروفون پیش‌فرض',None)
            for id,name in devices:combo.addItem(name,id)
            index=combo.findData(current)
            if index>=0:combo.setCurrentIndex(index)
        if not devices:raise UserError('ورودی میکروفون پیدا نشد. دستگاه و مجوزهای ویندوز را بررسی کن.')

    def toggle_record(self,owner='stt'):
        if self.worker:raise UserError('ابتدا عملیات جاری را تمام کن.')
        if self.dictation.busy:raise UserError('دیکتهٔ سراسری در حال کار است؛ صبر کن تا تمام شود.')
        if self.recording:
            if owner!=self.record_owner:raise UserError('ضبط را از همان بخشی که شروع کردی متوقف کن.')
            self.recording=False;self.record_btn.setText('شروع ضبط');self.clone_record_button.setText('ضبط نمونه')
            self.busy_controls(False);self.mic_meter.setValue(0);self.clone_meter.setValue(0)
            self.status.setText('ضبط متوقف شد.')
            try:
                path=self.recorder.stop()
                if owner=='clone':
                    self.set_reference(path);self.status.setText('نمونه آماده است؛ نام و مجوز را تنظیم کن و پروفایل بساز.')
                else:
                    self.set_source(path)
                    self.mic_status.setText('ضبط متوقف شد؛ «تبدیل به متن» را بزن. برای نگهداری فایل خام، «ذخیرهٔ ضبط» را انتخاب کن.'+(' هشدار: احتمال افت نمونه در ضبط وجود دارد.' if self.recorder.warning else ''))
            except Exception:
                if owner=='clone':self.clone_source_label.setText('نمونهٔ قابل استفاده ثبت نشد؛ دوباره ضبط کن.')
                else:self.mic_status.setText('ضبط معتبر ثبت نشد؛ دوباره تلاش کن.')
                raise
            return
        self.stop_playback()
        path=self.store.root/'recordings'/('rec-'+uuid.uuid4().hex+'.wav')
        device=self.clone_device.currentData() if owner=='clone' else self.device.currentData()
        self.recorder.start(path,device);self.recording=True;self.record_owner=owner;self.busy_controls(True)
        if owner=='clone':
            self.clone_record_button.setText('توقف ضبط');self.clone_source=None;self.clone_preview_button.setEnabled(False);self.clone_consent.setChecked(False)
        else:
            self.record_btn.setText('توقف ضبط');self.source=None;self.file_label.setText('در حال ضبط…');self.record_export.setEnabled(False)
        self.status.setText('میکروفون فعال است؛ برای پایان، توقف ضبط را بزن.')

    def _dictation_options(self):
        key=self.stt_model.currentData()
        if not self.models.ready(key):
            for alt in ('stt-small','stt-base','stt-medium'):
                if self.models.ready(alt):key=alt;break
        return {'device':self.device.currentData(),'model':key}

    def toggle_dictation(self):
        self._set_dictation(not self.dictation.enabled)

    def _set_dictation(self,on):
        if on:
            if not self.dictation.install(QApplication.instance()):
                self.store.set_setting('dictation_enabled','0');self._refresh_dictation_state();return
        else:
            self.dictation.cancel();self.dictation.uninstall()
        self.store.set_setting('dictation_enabled','1' if on else '0')
        self._refresh_dictation_state()

    def _refresh_dictation_state(self):
        if not hasattr(self,'dictation_state'):return
        if self.dictation.enabled:
            self.dictation_state.setText('●  فعال · '+HOTKEY_LABEL+' را نگه دار و صحبت کن')
            self.dictation_toggle.setText('غیرفعال کردن دیکته')
        else:
            self.dictation_state.setText('○  غیرفعال')
            self.dictation_toggle.setText('فعال‌سازی دیکته')

    def _dictation_phase(self,on,text):
        if not hasattr(self,'dictation_state'):return
        if on:self.dictation_state.setText('●  '+text)
        else:self._refresh_dictation_state()

    def _dictation_overlay(self,on):
        if on:self.dictation_overlay.show_recording(self._dictation_peak)

    def _dictation_peak(self):
        rec=self.dictation.recorder
        try:return float(rec.peak) if rec else 0.0
        except Exception:return 0.0

    def _dictation_working_overlay(self,on):
        if on:self.dictation_overlay.show_working()
        else:self.dictation_overlay.hide_overlay()

    def run_stt(self):
        if self.worker or self.recording:raise UserError('ابتدا ضبط یا عملیات جاری را تمام کن.')
        if not self.source:raise UserError('ابتدا فایل صوتی انتخاب کن یا با میکروفون ضبط کن.')
        key=self.stt_model.currentData();language=self.stt_language.currentData();source=self.source;self.require_model(key)
        if self.stt_text.toPlainText().strip() and not message(self,'متن کادر با نتیجهٔ جدید جایگزین شود؟ متن‌های تولیدشدهٔ قبلی در تاریخچه باقی می‌مانند، اما ویرایش‌های دستی را ابتدا TXT ذخیره کن.',question=True):return
        self.start_job(lambda c,p:transcribe(self.store.root,key,source,language,c,p),self.stt_done,'شروع تشخیص گفتار…')

    def stt_done(self,result):
        self.stt_item=self.store.add('stt',result['language'],result['model'],result['text'],source=result['source'],duration=result['duration'],segments=result['segments'])
        self.stt_text.setPlainText(result['text']);self.stt_meta.setText(f'{duration(result["duration"])} · ذخیره‌شده در تاریخچه · نتیجه را بازبینی کن')
        self.status.setText('پیاده‌سازی تمام شد؛ نام‌ها، اعداد و نشانه‌گذاری را بررسی کن.')

    def transcript_changed(self):
        if hasattr(self,'export_srt_btn'):
            self.export_srt_btn.setEnabled(bool(self.stt_item and self.stt_text.toPlainText()==self.stt_item['text']))

    def tick(self):
        if self.recording:
            elapsed=time.monotonic()-self.recorder.started;peak=int(min(1,self.recorder.peak)*100)
            if self.record_owner=='clone':
                self.clone_source_label.setText(f'● ضبط نمونهٔ شخصی  {duration(elapsed)}  /  سقف ۶۰ ثانیه');self.clone_meter.setValue(peak);limit=59.5
            else:
                self.mic_status.setText(f'● ضبط میکروفون  {duration(elapsed)}  /  سقف ۹۰ دقیقه');self.mic_meter.setValue(peak);limit=5399.5
            if self.recorder.error or elapsed>=limit:self.guard(lambda:self.toggle_record(self.record_owner))
        if self.player.stream:
            if self.play_wave:
                self.play_wave.position=self.player.frames/max(1,self.player.total);self.play_wave.update()
            if self.player.finished:self.stop_playback();self.status.setText('پخش پایان یافت.')

    def safe_save_path(self,title,default,filter,extension):
        name,_=QFileDialog.getSaveFileName(self,title,default,filter)
        if not name:return None
        p=Path(name)
        if p.suffix.lower()!=extension:p=Path(str(p)+extension)
        resolved=p.resolve()
        if resolved.is_relative_to(self.store.root) or resolved.is_relative_to(ROOT):
            raise UserError('برای خروجی، پوشه‌ای خارج از فایل‌های داخلی برنامه و داده‌های آن انتخاب کن؛ مثلاً Documents یا Desktop.')
        if p.exists() and not message(self,'فایل موجود جایگزین شود؟\n'+str(p),question=True):return None
        return p

    def export_audio(self,path):
        if not path or not Path(path).exists():raise UserError('فایل صوتی موجود نیست.')
        dest=self.safe_save_path('ذخیرهٔ فایل صوتی','Tixi-Voice.wav','WAV (*.wav)','.wav')
        if dest:atomic_copy(Path(path),dest);self.status.setText('فایل صوتی ذخیره شد.')

    def export_result(self):
        if self.result_item:self.export_audio(self.store.audio_path(self.result_item))

    def export_recording(self):
        if self.source and not self.recording:self.export_audio(self.source)

    def export_text(self,text):
        if not text.strip():raise UserError('متنی برای ذخیره وجود ندارد.')
        dest=self.safe_save_path('ذخیرهٔ متن','Tixi-Transcript.txt','Text (*.txt)','.txt')
        if dest:atomic_bytes(dest,text.encode('utf-8-sig'));self.status.setText('متن با کدگذاری UTF-8 ذخیره شد.')

    def export_srt(self,item):
        segments=json.loads(item['segments']);text=make_srt(segments)
        if not text:raise UserError('این نتیجه زمان‌بندی زیرنویس ندارد.')
        dest=self.safe_save_path('ذخیرهٔ زیرنویس','Tixi-Transcript.srt','SubRip (*.srt)','.srt')
        if dest:atomic_bytes(dest,text.encode('utf-8-sig'));self.status.setText('زیرنویس SRT ذخیره شد؛ زمان‌بندی‌ها تقریبی‌اند.')

    def export_current_srt(self):
        if self.stt_item and self.stt_text.toPlainText()==self.stt_item['text']:self.export_srt(self.stt_item)

    def copy_text(self,text):
        if not text.strip():raise UserError('متنی برای کپی وجود ندارد.')
        QApplication.clipboard().setText(text);self.status.setText('متن کپی شد.')

    def refresh_history(self,*_):
        if not hasattr(self,'history_layout'):return
        while self.history_layout.count():
            item=self.history_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        rows=self.store.search(self.history_search.text(),self.history_kind.currentData());self.history_rows=rows
        self.history_count.setText(f'{len(rows)} نتیجهٔ نمایشی از {self.store.count()} تبدیل · حداکثر ۳۰۰ نتیجهٔ تازه در هر جست‌وجو')
        if not rows:
            w,l=card();l.addWidget(label('هنوز چیزی اینجا نیست.','accent'));l.addWidget(label('اولین تبدیل را انجام بده یا عبارت جست‌وجو را تغییر بده.','muted'));self.history_layout.addWidget(w);return
        for item in rows:
            w,l=card();h=QHBoxLayout();kind='متن به صدا' if item['kind']=='tts' else 'صدا به متن';h.addWidget(label(kind+'  /  '+item['language'],'accent'));h.addStretch()
            h.addWidget(label(datetime.fromtimestamp(item['created']/1000).strftime('%Y/%m/%d  %H:%M'),'faint'));l.addLayout(h)
            preview=item['text'].replace('\n',' ');l.addWidget(label(preview[:180]+('…' if len(preview)>180 else ''),'muted'))
            buttons=QHBoxLayout();b=self.button('باز کردن نتیجه',lambda id=item['id']:self.open_history(id),'folder');buttons.addWidget(b)
            buttons.addStretch();buttons.addWidget(label(duration(item['duration'])+('  ·  '+item['source'] if item['source'] else ''),'faint'));l.addLayout(buttons);self.history_layout.addWidget(w)

    def open_history(self,id):
        item=self.store.get(id)
        if not item:return
        d=QDialog(self);d.setWindowTitle('Tixi Voice — نتیجهٔ ذخیره‌شده');d.resize(720,600)
        l=QVBoxLayout(d);l.setContentsMargins(24,24,24,24);l.setSpacing(15);l.addWidget(label('نتیجهٔ ذخیره‌شده',name='dialogHeading'))
        l.addWidget(label(MANIFEST.get(item['model'],{}).get('title',item['model'])+'  ·  '+duration(item['duration']),'muted'))
        text=self.text_edit('');text.setReadOnly(True);text.setPlainText(item['text']);l.addWidget(text,1)
        h=QHBoxLayout();h.addWidget(self.button('کپی',lambda:self.copy_text(item['text']),'copy'));h.addWidget(self.button('TXT',lambda:self.export_text(item['text']),'download'))
        if item['kind']=='tts':
            h.addWidget(self.button('پخش',lambda:self.play_audio(self.store.audio_path(item)),'play'));h.addWidget(self.button('توقف',self.stop_playback,'stop'));h.addWidget(self.button('WAV',lambda:self.export_audio(self.store.audio_path(item)),'download'))
        else:h.addWidget(self.button('SRT',lambda:self.export_srt(item),'download'))
        l.addLayout(h);bottom=QHBoxLayout();bottom.addWidget(self.button('حذف نتیجه',lambda:self.delete_history(id,d),'trash','danger'));bottom.addStretch();bottom.addWidget(self.button('بستن',d.accept,'x'));l.addLayout(bottom)
        d.exec();self.stop_playback();d.deleteLater()

    def delete_history(self,id,dialog):
        if self.worker or self.recording:
            raise UserError('پیش از حذف نتیجه، عملیات یا ضبط جاری را تمام کن.')
        if not message(dialog,'این نتیجه و فایل صوتی تولیدشدهٔ مربوط به آن برای همیشه حذف شود؟ فایل صوتی ورودی اصلی تغییر نمی‌کند.',question=True):return
        self.stop_playback();self.store.remove(id)
        if self.result_item and self.result_item['id']==id:
            self.result_item=None;self.play_btn.setEnabled(False);self.save_audio_btn.setEnabled(False);self.wave.levels=[];self.wave.update();self.audio_time.setText('نتیجه حذف شد.')
        if self.stt_item and self.stt_item['id']==id:self.stt_item=None;self.transcript_changed()
        dialog.accept();self.refresh_history()

    def save_drafts(self):
        try:
            # Bounded storage; oversized input is rejected for synthesis but stays visible until closed.
            self.store.set_setting('tts_draft',self.tts_text.toPlainText()[:100000])
            self.store.set_setting('stt_draft',self.stt_text.toPlainText()[:2000000])
        except Exception:LOG.exception('Draft save failed')

    def about(self):
        message(self,'Tixi Voice 1.1\nفارسی و انگلیسی؛ بدون حساب و API.\n\nموتورها: Piper، whisper.cpp و OpenVoice ONNX؛ CPU و شتاب‌دهندهٔ اختیاری DirectML. اینترنت فقط برای دانلود اختیاری مدل‌هاست. تبدیل بلادرنگ در این نسخه نیست. بخش «صدای من» رنگ صدای نمونهٔ دارای مجوز را با OpenVoice ONNX نزدیک‌سازی می‌کند؛ شباهت کامل تضمین نمی‌شود.\n\nمتن‌ها، فایل‌های ساخته‌شده و پیش‌نویس‌ها در تاریخچه/پوشهٔ دادهٔ محلی و بدون رمزگذاری نگهداری می‌شوند. فایل‌های ضبط موقت با بستن برنامه پاک می‌شوند؛ برای نگهداری از «ذخیرهٔ ضبط» استفاده کن.\n\nهدف: Windows 10/11 x64، AVX2، ترجیحاً 8GB RAM. نسخهٔ EXE بدون Python اجرا می‌شود.\n\nگزارش آزمون و مجوزها همراه بسته است. برنامه تحت GPLv3 عرضه شده؛ حقوق لوگوی ارسالی و مجوز مدل‌ها جداست.\n\nپوشهٔ داده:\n'+str(self.store.root))

    def closeEvent(self,event):
        if self.worker:
            if self.closing or message(self,'عملیات جاری لغو شود و برنامه بسته شود؟',question=True):self.closing=True;self.cancel_job()
            event.ignore();return
        if self.recording:
            if not message(self,'ضبط متوقف و برنامه بسته شود؟ فایل خامِ ذخیره‌نشده حذف می‌شود.',question=True):event.ignore();return
            self.recording=False
            try:self.recorder.stop()
            except UserError:pass
        self.stop_playback();self.save_drafts();self.timer.stop()
        try:
            self.dictation.uninstall();self.dictation.wait(2.0)
        except Exception:LOG.exception('Dictation shutdown failed')
        try:
            if hasattr(self,'dictation_overlay'):self.dictation_overlay.close()
        except Exception:LOG.exception('Overlay shutdown failed')
        # No engine is active now; only app-owned temporary data are removed.
        for folder in ('temp','recordings'):
            for p in (self.store.root/folder).iterdir():
                try:
                    if p.is_dir():shutil.rmtree(p)
                    else:p.unlink()
                except OSError:LOG.warning('Could not remove temporary file')
        event.accept()
