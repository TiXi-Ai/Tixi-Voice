# SPDX-License-Identifier: GPL-3.0-or-later
"""Cancellable local inference. A subprocess isolates native engine crashes from the UI."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from core import ROOT, UserError, Cancelled, check_cancel, atomic_bytes, valid_text, chunks, parse_transcript
from models import Models


def worker_tts(request_file):
    """Entry used by this EXE itself before importing any Qt GUI components."""
    request_file=Path(request_file).resolve()
    job=request_file.parent
    try:
        from piper import PiperVoice, SynthesisConfig
        from piper.config import PiperConfig
        import onnxruntime as ort
        ort.disable_telemetry_events()
        req=json.loads(request_file.read_text(encoding='utf-8'))
        text=valid_text(req['text'])
        speed=float(req['speed'])
        if not 0.6<=speed<=1.6:raise ValueError('Invalid speed')
        model=Path(req['model']).resolve()
        config=json.loads(Path(str(model)+'.json').read_text(encoding='utf-8'))
        if config.get('espeak',{}).get('voice') not in ('fa','en-us','en'):
            raise ValueError('This offline edition only supports its Persian and English models')
        from hardware import plan
        sizing=plan()
        opts=ort.SessionOptions();opts.intra_op_num_threads=sizing['threads'];opts.inter_op_num_threads=1
        opts.enable_mem_pattern=False;opts.enable_cpu_mem_arena=False
        opts.log_severity_level=3
        # eSpeak's narrow Windows fopen cannot open an absolute Persian path.
        # A worker-only Unicode-safe chdir + ASCII relative data path avoids that limitation.
        from piper.phonemize_espeak import ESPEAK_DATA_DIR
        os.chdir(Path(ESPEAK_DATA_DIR).resolve().parent)
        voice=PiperVoice(config=PiperConfig.from_dict(config),session=ort.InferenceSession(str(model),sess_options=opts,providers=['CPUExecutionProvider']),espeak_data_dir=Path('espeak-ng-data'))
        voice.use_tashkeel=False # Never download additional diacritization models.
        segments=list(chunks(text,180 if sizing['low_memory'] else 350))
        with wave.open(str(job/'speech.wav'),'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(voice.config.sample_rate)
            for i,part in enumerate(segments):
                for chunk in voice.synthesize(part,SynthesisConfig(length_scale=1.0/speed)):
                    out.writeframes(chunk.audio_int16_bytes)
                if i<len(segments)-1:
                    out.writeframes(b'\0\0'*int(voice.config.sample_rate*0.12))
                atomic_bytes(job/'progress.json',json.dumps({'percent':int(100*(i+1)/len(segments))}).encode())
        with wave.open(str(job/'speech.wav'),'rb') as f:duration=f.getnframes()/f.getframerate()
        if duration<0.05:raise UserError('برای این متن صوتی تولید نشد.')
        atomic_bytes(job/'result.json',json.dumps({'duration':duration},ensure_ascii=False).encode('utf-8'))
        return 0
    except Exception as e:
        atomic_bytes(job/'error.json',json.dumps({'type':type(e).__name__,'message':str(e)},ensure_ascii=False).encode('utf-8'))
        return 1


def run_process(cmd,cwd,cancel,progress,tts=False,timeout=7200):
    log=Path(cwd)/'engine.log'
    env=os.environ.copy();env['OMP_NUM_THREADS']='4';env['PYTHONUTF8']='1'
    # Never invoke cmd.exe or interpolate user text into a command.
    kwargs={'cwd':str(cwd),'stdin':subprocess.DEVNULL,'env':env}
    if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
    process=None;started=time.monotonic();last=-2;offset=0
    try:
        with log.open('wb') as out:
            process=subprocess.Popen([str(x) for x in cmd],stdout=out,stderr=subprocess.STDOUT,**kwargs)
            while process.poll() is None:
                check_cancel(cancel)
                if time.monotonic()-started>timeout:
                    raise UserError('پردازش بیش از حد طول کشید؛ فایل کوتاه‌تر یا مدل سبک‌تر را امتحان کن.')
                percent=-1;progress_message=''
                if tts:
                    try:
                        item=json.loads((Path(cwd)/'progress.json').read_text(encoding='utf-8'));percent=int(item['percent']);progress_message=item.get('message','')
                    except (OSError,ValueError,KeyError):pass
                else:
                    try:
                        with log.open('rb') as r:r.seek(offset);data=r.read();offset=r.tell()
                        hits=re.findall(rb'progress\s*=\s*(\d+)%',data)
                        if hits:percent=8+int(hits[-1])*90//100
                    except OSError:pass
                if percent>=0 and percent!=last:
                    last=percent;progress(percent,progress_message or ('در حال ساخت صدا…' if tts else 'در حال تبدیل صدا به متن…'))
                cancel.wait(0.15)
            check_cancel(cancel)
        if process.returncode:
            error=Path(cwd)/'error.json'
            detail=''
            if error.exists():
                try:detail=json.loads(error.read_text(encoding='utf-8')).get('message','')[:450]
                except (OSError,ValueError):pass
            raise UserError('موتور گفتار اجرا نشد یا پردازش را کامل نکرد. سلامت مدل و فضای خالی را بررسی کن. پردازندهٔ x64 با AVX2 لازم است.\n'+detail)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def synthesize(root,key,text,speed,cancel,progress,effect='none'):
    text=valid_text(text)
    if key not in ('tts-fa','tts-en','tts-en-lessac','tts-en-amy'):raise UserError('مدل صدا معتبر نیست.')
    model=Models(root).verify(key,cancel,progress)
    job=Path(tempfile.mkdtemp(prefix='tts-',dir=Path(root)/'temp'))
    try:
        request=job/'request.json'
        atomic_bytes(request,json.dumps({'text':text,'speed':speed,'model':str(model)},ensure_ascii=False).encode('utf-8'))
        if getattr(sys,'frozen',False):cmd=[sys.executable,'--tts-worker',str(request)]
        else:cmd=[sys.executable,str(ROOT/'main.py'),'--tts-worker',str(request)]
        progress(-1,'بارگذاری موتور صدا روی همین کامپیوتر…')
        run_process(cmd,job,cancel,progress,tts=True,timeout=1800)
        result=json.loads((job/'result.json').read_text(encoding='utf-8'))
        if effect!='none':
            from effects import apply_effect
            apply_effect(job/'speech.wav',effect,cancel,progress)
            result['effect']=effect
        result.update({'job':str(job),'audio':str(job/'speech.wav'),'text':text,'model':key,'language':'fa' if key=='tts-fa' else 'en'})
        return result
    except BaseException:
        shutil.rmtree(job,ignore_errors=True);raise


def transcribe(root,key,source,language,cancel,progress):
    from audio import prepare_audio
    if key not in ('stt-small','stt-base','stt-medium') or language not in ('fa','en','auto'):
        raise UserError('مدل یا زبان تشخیص معتبر نیست.')
    model=Models(root).verify(key,cancel,progress)
    engine_root=Path(os.environ.get('TIXI_ENGINE_DIR',ROOT/'engines'))
    executable=engine_root/'whisper'/('whisper-cli.exe' if os.name=='nt' else 'whisper-cli')
    if not executable.is_file():
        raise UserError('موتور whisper-cli پیدا نشد. کل بستهٔ برنامه و پوشهٔ _internal را دوباره استخراج کن.')
    job=Path(tempfile.mkdtemp(prefix='stt-',dir=Path(root)/'temp'))
    try:
        duration=prepare_audio(Path(source),job/'input.wav',cancel,progress)
        # Relative ASCII input/output/model paths also work with Persian Windows usernames.
        cmd=[executable,'--model',os.path.relpath(model,job),'--file','input.wav','--language',language,
             '--threads',str(max(1,min(4,(os.cpu_count() or 2)-1))), '--no-gpu','--print-progress',
             '--output-json','--output-file','result','--suppress-nst']
        progress(-1,'تشخیص گفتار آفلاین؛ فایل طولانی ممکن است چند دقیقه زمان ببرد…')
        run_process(cmd,job,cancel,progress)
        result_file=job/'result.json'
        if not result_file.is_file() or result_file.stat().st_size>25*1024*1024:
            raise UserError('خروجی معتبر از موتور تشخیص دریافت نشد.')
        obj=json.loads(result_file.read_text(encoding='utf-8'))
        text,segments=parse_transcript(obj)
        if not text:raise UserError('گفتار قابل تشخیصی پیدا نشد؛ زبان، بلندی صدا و کیفیت ضبط را بررسی کن.')
        return {'text':text,'segments':segments,'duration':duration,'source':Path(source).name,
                'language':obj.get('result',{}).get('language',language),'model':key}
    finally:
        shutil.rmtree(job,ignore_errors=True)
