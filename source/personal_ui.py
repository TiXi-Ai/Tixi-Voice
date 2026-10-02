# SPDX-License-Identifier: GPL-3.0-or-later
"""Personal voice UI, kept separate so the original TTS/STT workflow stays intact."""
from pathlib import Path
import shutil
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox,QLineEdit,QCheckBox,QHBoxLayout,QVBoxLayout,QWidget,QFileDialog,QProgressBar
from core import MANIFEST,UserError
from hardware import resources,plan
from personal_voice import KEY,REFERENCE_TEXT_FA,REFERENCE_TEXT_EN,enroll,personalized_speech

class PersonalVoiceMixin:
    def install_personal_selector(self,layout):
        from ui import label
        layout.addWidget(label('شخصی‌سازی صدا','muted'))
        self.persona=QComboBox();self.persona.addItem('صدای آمادهٔ مدل',None)
        self.persona.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.persona.setMinimumContentsLength(12)
        layout.addWidget(self.persona)
        self.persona_hint=label('برای افزودن صدای خودت، بخش «صدای من» را باز کن.','faint');layout.addWidget(self.persona_hint)
        self.persona.currentIndexChanged.connect(self.personal_choice_changed)

    def personal_choice_changed(self,*_):
        selected=bool(self.persona.currentData())
        self.persona_hint.setText('خروجی مصنوعی با رنگ صدای پروفایل؛ تلفظ و لحن از مدل پایه می‌آید.' if selected else 'برای افزودن صدای خودت، بخش «صدای من» را باز کن.')
        self.persona.setToolTip(self.persona.currentText())

    def build_personal_voice_page(self):
        from ui import label,card,row
        self.clone_source=None
        v=self.page('صدای من، در کلمه‌های تازه.','یک نمونهٔ تمیز ضبط کن؛ رنگ صدایت را به خروجی فارسی یا انگلیسی نزدیک کن. این قابلیت آزمایشی است.')
        intro,l=card();l.addWidget(label('۰۱  /  نمونهٔ مرجع','accent'))
        l.addWidget(label('۸ تا ۶۰ ثانیه، فقط یک گوینده، بدون موسیقی و اکو؛ ۲۰ تا ۴۵ ثانیه توصیه می‌شود. با لحن طبیعی بخوان، نه تقلید یا نجوا.','muted'))
        script_row=QHBoxLayout();script_row.addWidget(label('متن پیشنهادی برای خواندن','muted'),1)
        self.clone_language=QComboBox();self.clone_language.addItem('فارسی','fa');self.clone_language.addItem('English','en');script_row.addWidget(self.clone_language);l.addLayout(script_row)
        self.reference_script=self.text_edit('');self.reference_script.setReadOnly(True);self.reference_script.setMinimumHeight(150);self.reference_script.setMaximumHeight(190);l.addWidget(self.reference_script)
        self.clone_language.currentIndexChanged.connect(self.set_reference_script);self.set_reference_script()
        controls=QHBoxLayout();self.clone_device=QComboBox();self.clone_device.addItem('میکروفون پیش‌فرض',None);controls.addWidget(self.clone_device,1)
        self.clone_devices_button=self.button('ورودی‌ها',self.refresh_devices,'refresh');controls.addWidget(self.clone_devices_button)
        self.clone_record_button=self.button('ضبط نمونه',lambda:self.toggle_record('clone'),'mic');controls.addWidget(self.clone_record_button)
        self.clone_file_button=self.button('ورود فایل',self.choose_reference,'upload');controls.addWidget(self.clone_file_button);l.addLayout(controls)
        self.clone_source_label=label('هنوز نمونه‌ای انتخاب نشده. ضبطِ ثبت‌نشده موقت است و با خروج پاک می‌شود؛ برای نگهداری آن پروفایل بساز.','faint');l.addWidget(self.clone_source_label)
        self.clone_meter=QProgressBar();self.clone_meter.setRange(0,100);self.clone_meter.setValue(0);self.clone_meter.setFixedHeight(5);self.clone_meter.setTextVisible(False);l.addWidget(self.clone_meter)
        self.clone_preview_button=self.button('شنیدن نمونه',self.preview_reference,'play');self.clone_preview_button.setEnabled(False)
        l.addLayout(row(self.clone_preview_button,self.button('توقف پخش',self.stop_playback,'stop'),stretch=True));v.addWidget(intro)
        setup,s=card();s.addWidget(label('۰۲  /  ثبت پروفایل','accent'))
        name_row=QHBoxLayout();self.clone_name=QLineEdit();self.clone_name.setMaxLength(60);self.clone_name.setPlaceholderText('نام پروفایل، مثلاً صدای من');name_row.addWidget(self.clone_name,1)
        self.clone_mode=QComboBox();self.clone_mode.addItem('اجرای خودکار · پیشنهادی','auto');self.clone_mode.addItem('CPU کم‌مصرف','lite')
        self.clone_mode.setCurrentIndex(max(0,self.clone_mode.findData(self.store.setting('clone_mode','auto'))));self.clone_mode.currentIndexChanged.connect(self.save_clone_mode);name_row.addWidget(self.clone_mode);s.addLayout(name_row)
        self.clone_consent=QCheckBox('این صدای خودم است یا اجازهٔ روشنِ صاحب صدا را برای شبیه‌سازی دارم.');s.addWidget(self.clone_consent)
        s.addWidget(label('نمونه و ویژگی‌های صوتی در همین کامپیوتر و بدون رمزگذاری نگه داشته می‌شوند. از این قابلیت برای فریب، جعل هویت یا دورزدن تأیید صوتی استفاده نکن.','faint'))
        self.clone_hardware=label('','muted');s.addWidget(self.clone_hardware)
        actions=QHBoxLayout();self.clone_enroll_button=self.button('ساخت پروفایل صدا',self.create_personal_profile,'sparkles','primary');actions.addWidget(self.clone_enroll_button)
        self.clone_install_button=self.button('مدل و افزونهٔ لازم',lambda:self.navigate(3),'download');actions.addWidget(self.clone_install_button)
        actions.addWidget(self.button('آزمایش مجدد GPU',self.reset_gpu_cache,'refresh',role='ghost'));actions.addStretch();s.addLayout(actions)
        self.clone_ready_label=label('','faint');s.addWidget(self.clone_ready_label);v.addWidget(setup)
        v.addWidget(label('۰۳  /  پروفایل‌های صدای تو','accent'))
        self.profile_list=QWidget();self.profile_layout=QVBoxLayout(self.profile_list);self.profile_layout.setContentsMargins(0,0,0,0);self.profile_layout.setSpacing(12);v.addWidget(self.profile_list)
        v.addWidget(label('پس از ساخت پروفایل، در «متن به صدا» زبان و پروفایل را انتخاب کن. زبان/تلفظ از Piper و رنگ صدا از نمونه می‌آید؛ به‌ویژه برای فارسی یا تغییر زبان، شباهت و کیفیت تضمین نمی‌شود. خروجی‌ها با عنوان صدای مصنوعی ثبت می‌شوند.','faint'));v.addStretch()
        self.refresh_personal_resources();self.refresh_profiles()

    def set_reference_script(self,*_):
        self.reference_script.setPlainText(REFERENCE_TEXT_FA if self.clone_language.currentData()=='fa' else REFERENCE_TEXT_EN)

    def save_clone_mode(self,*_):
        self.store.set_setting('clone_mode',self.clone_mode.currentData());self.refresh_personal_resources()

    def refresh_personal_resources(self):
        if not hasattr(self,'clone_hardware'):return
        r=resources();p=plan(self.store.setting('clone_mode','auto'),r)
        ram=f'{r.total_mb/1024:.1f} GB RAM · {r.available_mb/1024:.1f} GB آزاد' if r.total_mb else 'حافظهٔ سیستم قابل اندازه‌گیری نبود؛ حالت محافظه‌کارانه'
        strategy='GPU سازگار در صورت امکان، با بازگشت به CPU' if p['try_gpu'] else 'CPU · مصرف حافظهٔ کمتر' if p['low_memory'] else 'CPU · پردازش بخش‌بندی‌شده'
        self.clone_hardware.setText(ram+'\n'+strategy+' · تنظیم خودکار بدون نیاز به مشخصات کارت')
        ready=self.models.ready(KEY)
        size=sum(f['size'] for f in MANIFEST[KEY]['files'])/1048576
        self.clone_ready_label.setText('● مدل شخصی نصب است؛ سلامت فایل پیش از استفاده بررسی می‌شود.' if ready else f'○ یک‌بار نصب مدل و افزونهٔ رسمی DirectML، حدود {size:.0f} MB؛ پس از آن تبدیل‌ها آفلاین‌اند.')

    def refresh_profiles(self):
        from ui import label,card,row,duration
        if not hasattr(self,'profile_layout'):return
        selected=self.persona.currentData();self.persona.blockSignals(True);self.persona.clear();self.persona.addItem('صدای آمادهٔ مدل',None)
        profiles=self.store.profiles()
        for item in profiles:self.persona.addItem('شخصی · '+item['name'],item['id'])
        self.persona.setCurrentIndex(max(0,self.persona.findData(selected)));self.persona.blockSignals(False);self.personal_choice_changed()
        while self.profile_layout.count():
            child=self.profile_layout.takeAt(0)
            if child.widget():child.widget().deleteLater()
        if not profiles:
            w,l=card();l.addWidget(label('هنوز پروفایلی نساخته‌ای.','muted'));l.addWidget(label('نمونه را ضبط/انتخاب کن، مجوز را تأیید کن و ساخت پروفایل را بزن.','faint'));self.profile_layout.addWidget(w)
        for item in profiles:
            w,l=card();l.addWidget(label(item['name'],name='sectionTitle'))
            l.addWidget(label(f'{duration(item["duration"])} نمونه · '+('فارسی' if item['language']=='fa' else 'English')+' · ذخیرهٔ محلی','faint'))
            l.addLayout(row(self.button('استفاده از این صدا',lambda id=item['id']:self.use_profile(id),'wave','primary'),
                self.button('شنیدن نمونه',lambda id=item['id']:self.play_profile(id),'play'),
                self.button('حذف پروفایل',lambda id=item['id']:self.delete_profile(id),'trash',role='ghost'),stretch=True))
            self.profile_layout.addWidget(w)
        self.refresh_personal_resources()

    def choose_reference(self):
        if self.worker or self.recording:raise UserError('ابتدا عملیات یا ضبط جاری را تمام کن.')
        name,_=QFileDialog.getOpenFileName(self,'نمونهٔ صدای خودت یا فرد دارای مجوز','','Audio (*.wav *.mp3 *.flac *.ogg)')
        if name:self.set_reference(Path(name))

    def set_reference(self,path):
        from audio import inspect_audio
        from ui import duration
        info=inspect_audio(path)
        if not 8<=info.duration<=60:raise UserError('نمونه باید بین ۸ تا ۶۰ ثانیه باشد؛ ۲۰ تا ۴۵ ثانیه پیشنهاد می‌شود.')
        self.clone_source=Path(path);self.clone_source_label.setText(f'{self.clone_source.name} · {duration(info.duration)} · آمادهٔ بررسی')
        self.clone_preview_button.setEnabled(True)
        # Selecting a new recording requires an explicit confirmation for that recording.
        self.clone_consent.setChecked(False)
        self.pages.widget(4).ensureWidgetVisible(self.clone_enroll_button,0,50)

    def preview_reference(self):
        if self.clone_source:self.play_audio(self.clone_source)

    def create_personal_profile(self):
        if self.worker or self.recording:raise UserError('ابتدا ضبط یا عملیات جاری را تمام کن.')
        if not self.clone_source:raise UserError('اول نمونهٔ صدایت را ضبط کن یا فایل بده.')
        if not self.clone_consent.isChecked():raise UserError('تأیید کن که صدای خودت است یا اجازهٔ روشنِ صاحب صدا را داری.')
        name=self.clone_name.text().strip()
        if not name:raise UserError('یک نام برای پروفایل وارد کن.')
        if len(self.store.profiles())>=20:raise UserError('حداکثر ۲۰ پروفایل مجاز است؛ ابتدا یکی را حذف کن.')
        self.require_model(KEY);source=self.clone_source;language=self.clone_language.currentData();mode=self.clone_mode.currentData()
        self.start_job(lambda c,p:enroll(self.store.root,source,mode,c,p),lambda result:self.personal_profile_done(result,name,language),'بررسی نمونهٔ صدا…')

    def personal_profile_done(self,result,name,language):
        try:
            item=self.store.add_profile(name,language,result['reference'],result['embedding'],result['revision'],result['duration'],True)
            self.refresh_profiles();self.persona.setCurrentIndex(self.persona.findData(item['id']))
            self.clone_name.clear();self.clone_consent.setChecked(False)
            self.navigate(0);self.pages.widget(0).verticalScrollBar().setValue(0)
            self.status.setText('پروفایل ساخته شد · '+result['provider']+' · در «متن به صدا» متن جدیدت را وارد کن.')
        finally:shutil.rmtree(result['job'],ignore_errors=True)

    def use_profile(self,id):
        if not self.store.profile(id):return
        self.persona.setCurrentIndex(self.persona.findData(id));self.navigate(0);self.pages.widget(0).verticalScrollBar().setValue(0)
        self.status.setText('پروفایل شخصی انتخاب شد؛ زبان پایه و متن جدید را تنظیم کن.')

    def play_profile(self,id):
        self.play_audio(self.store.profile_directory(id)/'reference.wav')

    def delete_profile(self,id):
        from ui import message
        if self.worker or self.recording:raise UserError('ابتدا عملیات یا ضبط جاری را تمام کن.')
        if not message(self,'این پروفایل، نسخهٔ ذخیره‌شدهٔ نمونه و ویژگی‌های صوتی آن حذف شوند؟ فایل ورودی اصلی، خروجی‌های قدیمی و کپی‌های بکاپ جداگانه حذف نمی‌شوند.',question=True):return
        self.stop_playback();self.store.remove_profile(id);self.refresh_profiles();self.status.setText('پروفایل و نمونهٔ داخلی آن حذف شدند.')

    def reset_gpu_cache(self):
        if self.worker or self.recording:raise UserError('ابتدا عملیات جاری را تمام کن.')
        (self.store.root/'runtimes/gpu-cooldown.json').unlink(missing_ok=True)
        self.clone_mode.setCurrentIndex(0);self.status.setText('در عملیات بعد، شتاب‌دهندهٔ سازگار دوباره امتحان می‌شود؛ نبود GPU مانع اجرای CPU نیست.')

    def personal_busy_controls(self,busy):
        if not hasattr(self,'clone_record_button'):return
        for w in [self.clone_device,self.clone_devices_button,self.clone_file_button,self.clone_name,self.clone_language,self.clone_consent,self.clone_enroll_button,self.clone_mode,self.persona]:w.setEnabled(not busy)
        self.clone_record_button.setEnabled(not self.worker and (not self.recording or self.record_owner=='clone'))
        self.record_btn.setEnabled(not self.worker and (not self.recording or self.record_owner=='stt'))
        self.refresh_personal_resources()
