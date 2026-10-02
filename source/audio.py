# SPDX-License-Identifier: GPL-3.0-or-later
"""Local audio I/O; no microphone is opened until the user presses Record."""
from __future__ import annotations
import queue
import threading
import time
from pathlib import Path
from core import UserError, MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, check_cancel

SUPPORTED={'.wav','.mp3','.flac','.ogg'}

def inspect_audio(path):
    import soundfile as sf
    path=Path(path)
    if path.suffix.lower() not in SUPPORTED:
        raise UserError('فرمت پشتیبانی‌شده: WAV، MP3، FLAC یا OGG. برای M4A/MP4 ابتدا فایل را به WAV تبدیل کن.')
    if not path.is_file() or path.stat().st_size>MAX_AUDIO_BYTES:
        raise UserError('فایل در دسترس نیست یا بزرگ‌تر از ۵۱۲ مگابایت است.')
    try:info=sf.info(str(path))
    except Exception as e:raise UserError('فایل صوتی خوانده نشد؛ ممکن است فرمت یا کدک آن پشتیبانی نشود یا خراب باشد.') from e
    if not info.frames or not info.samplerate or info.duration<0.2:
        raise UserError('فایل صوتی خالی یا خیلی کوتاه است.')
    if info.duration>MAX_AUDIO_SECONDS:
        raise UserError('حداکثر طول هر فایل ۹۰ دقیقه است؛ فایل را به چند بخش تقسیم کن.')
    if info.channels>8:
        raise UserError('فایل بیش از ۸ کانال دارد؛ ابتدا آن را به مونو یا استریو تبدیل کن.')
    return info

def prepare_audio(source,dest,cancel,progress):
    import numpy as np
    import soundfile as sf
    import soxr
    info=inspect_audio(source)
    energy=0.0;samples=0
    with sf.SoundFile(str(source)) as src, sf.SoundFile(str(dest),'w',samplerate=16000,channels=1,subtype='PCM_16') as out:
        stream=soxr.ResampleStream(src.samplerate,16000,1,dtype='float32',quality='HQ') if src.samplerate!=16000 else None
        while True:
            check_cancel(cancel)
            block=src.read(65536,dtype='float32',always_2d=True)
            if not len(block):break
            mono=np.nan_to_num(block.mean(axis=1),nan=0.0,posinf=1.0,neginf=-1.0)
            energy+=float(np.sum(mono.astype('float64')**2));samples+=len(mono)
            if stream:mono=stream.resample_chunk(mono,last=(src.tell()>=len(src)))
            out.write(np.clip(mono,-1.0,1.0))
            progress(min(8,int(8*src.tell()/len(src))),'آماده‌سازی صوت برای تشخیص…')
    if not samples or (energy/samples)**0.5<0.0002:
        raise UserError('این فایل تقریباً ساکت است. بلندی صدا یا ورودی میکروفون را بررسی کن.')
    return info.duration

class Recorder:
    """Bounded queue + writer thread; real-time callback never writes to disk."""
    def __init__(self):
        self.stream=None;self.thread=None;self.error='';self.peak=0.0;self.started=0;self.path=None
        self._stop=threading.Event();self._queue=queue.Queue(maxsize=128)
        self.frames=0;self.sample_rate=48000;self.warning=False

    @staticmethod
    def devices():
        import sounddevice as sd
        return [(i,x['name']) for i,x in enumerate(sd.query_devices()) if x['max_input_channels']>0]

    def start(self,path,device=None):
        import sounddevice as sd
        import soundfile as sf
        import numpy as np
        if self.stream:raise UserError('ضبط از قبل در حال اجراست.')
        self.path=Path(path);self.error='';self.warning=False;self.frames=0;self.peak=0.0
        self._stop.clear();self._queue=queue.Queue(maxsize=128)
        try:
            info=sd.query_devices(device,'input')
            self.sample_rate=int(info['default_samplerate'])
            sd.check_input_settings(device=device,channels=1,dtype='float32',samplerate=self.sample_rate)
            output=sf.SoundFile(str(self.path),'w',samplerate=self.sample_rate,channels=1,subtype='PCM_16')
        except Exception as e:
            self.path.unlink(missing_ok=True)
            raise UserError('میکروفون باز نشد. اتصال دستگاه و مجوز Microphone برای Desktop apps در تنظیمات Windows را بررسی کن.') from e
        def writer():
            try:
                with output:
                    while not self._stop.is_set() or not self._queue.empty():
                        try:block=self._queue.get(timeout=0.1)
                        except queue.Empty:continue
                        output.write(block);self.frames+=len(block)
            except Exception:
                self.error='ذخیرهٔ ضبط انجام نشد؛ فضای دیسک و دسترسی پوشه را بررسی کن.'
        def callback(indata,frames,time_info,status):
            self.peak=float(np.max(np.abs(indata))) if len(indata) else 0.0
            if status:self.warning=True
            try:self._queue.put_nowait(indata.copy())
            except queue.Full:self.error='ضبط به‌دلیل کندی دیسک متوقف شد؛ بخش‌هایی از صوت از دست رفتند.'
        self.thread=threading.Thread(target=writer,daemon=True);self.thread.start()
        try:
            self.stream=sd.InputStream(device=device,samplerate=self.sample_rate,channels=1,dtype='float32',callback=callback,blocksize=2048)
            self.stream.start();self.started=time.monotonic()
        except Exception as e:
            if self.stream:self.stream.close();self.stream=None
            self._stop.set();self.thread.join(timeout=10)
            self.path.unlink(missing_ok=True)
            raise UserError('شروع ضبط ناموفق بود؛ ورودی دیگری انتخاب کن و مجوز میکروفون را بررسی کن.') from e

    def stop(self):
        if self.stream:
            self.stream.stop();self.stream.close();self.stream=None
        self._stop.set()
        if self.thread:self.thread.join(timeout=10)
        if self.thread and self.thread.is_alive():
            raise UserError('پایان ذخیرهٔ ضبط طول کشید؛ دوباره تلاش کن.')
        if self.error:raise UserError(self.error)
        if self.frames/max(1,self.sample_rate)<0.5:
            if self.path:self.path.unlink(missing_ok=True)
            raise UserError('ضبط خیلی کوتاه است؛ حداقل نیم ثانیه صحبت کن.')
        return self.path

class Player:
    def __init__(self):
        self.stream=None;self.file=None;self.finished=False;self.frames=0;self.total=0;self.rate=1

    def stop(self):
        if self.stream:
            self.stream.abort();self.stream.close();self.stream=None
        if self.file:self.file.close();self.file=None
        self.finished=False

    def play(self,path):
        import sounddevice as sd
        import soundfile as sf
        self.stop()
        try:
            self.file=sf.SoundFile(str(path));self.total=len(self.file);self.rate=self.file.samplerate;self.frames=0
            def callback(outdata,frames,time_info,status):
                try:data=self.file.read(frames,dtype='float32',always_2d=True)
                except Exception:
                    outdata.fill(0);self.finished=True;raise sd.CallbackAbort
                outdata.fill(0);outdata[:len(data)]=data;self.frames+=len(data)
                if len(data)<frames:raise sd.CallbackStop
            def finish():self.finished=True
            self.stream=sd.OutputStream(samplerate=self.file.samplerate,channels=self.file.channels,dtype='float32',callback=callback,finished_callback=finish)
            self.stream.start()
        except Exception as e:
            self.stop();raise UserError('پخش انجام نشد؛ خروجی صدا یا اتصال هدفون را بررسی کن. همچنان می‌توانی فایل WAV را ذخیره کنی.') from e
