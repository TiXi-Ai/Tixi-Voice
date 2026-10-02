# SPDX-License-Identifier: GPL-3.0-or-later
"""Local reference-tone cloning with pinned OpenVoice V2 ONNX graphs.

A separate worker keeps native inference failures away from the GUI. Piper still
supplies pronunciation/rhythm; OpenVoice transfers tone, not a perfect identity.
"""
from __future__ import annotations
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from core import ROOT,MANIFEST,UserError,Cancelled,atomic_bytes,check_cancel
from models import Models
from hardware import plan

KEY='clone-openvoice'
RATE=22050
PROFILE_LIMIT=20
MIN_REFERENCE=8.0
MAX_REFERENCE=60.0

REFERENCE_TEXT_FA=('سلام، این یک نمونه از صدای طبیعی من است. امروز می‌خواهم چند جمله را آرام و روشن بخوانم. '
    'گاهی دربارهٔ کار و یادگیری صحبت می‌کنم و گاهی از سفر و خاطره‌ها می‌گویم. '
    'چه چیزی یک روز معمولی را زیباتر می‌کند؟ شاید یک گفت‌وگوی خوب، یک کتاب تازه، یا کمی موسیقی. '
    'صدایم را بدون عجله، بدون تقلید و با لحن معمول خودم ضبط می‌کنم.')
REFERENCE_TEXT_EN=('Hello. This is a sample of my natural speaking voice. Today I am reading a few sentences clearly and at a comfortable pace. '
    'Sometimes I talk about work and learning, and sometimes I share stories about travel and everyday life. '
    'What makes an ordinary day better? Perhaps a good conversation, a new book, or a little music. '
    'I am speaking naturally, without rushing or trying to imitate anyone.')

def prepare_reference(source,destination,cancel):
    import numpy as np
    import soundfile as sf
    import soxr
    from audio import inspect_audio
    info=inspect_audio(source)
    if not MIN_REFERENCE<=info.duration<=MAX_REFERENCE:
        raise UserError('نمونهٔ صدا باید بین ۸ تا ۶۰ ثانیه باشد؛ ۲۰ تا ۴۵ ثانیه گفتار طبیعی و بدون موسیقی پیشنهاد می‌شود.')
    check_cancel(cancel)
    data,sr=sf.read(str(source),dtype='float32',always_2d=True)
    data=np.nan_to_num(data.mean(axis=1),nan=0.0,posinf=0.0,neginf=0.0)
    if not len(data):raise UserError('نمونهٔ صدا خالی است.')
    clipping=float(np.mean(np.abs(data)>0.995))
    if clipping>0.02:raise UserError('صدای نمونه بیش از حد بلند و دچار بریدگی است. فاصلهٔ میکروفون یا بلندی ورودی را تنظیم و دوباره ضبط کن.')
    if sr!=RATE:data=soxr.resample(data,sr,RATE,quality='HQ').astype('float32')
    size=int(RATE*.04);count=len(data)//size
    rms=np.sqrt(np.mean(data[:count*size].reshape(count,size)**2,axis=1)+1e-12)
    peak=float(np.max(np.abs(data)))
    threshold=max(.003,min(.025,float(np.percentile(rms,85))*.12))
    active=np.flatnonzero(rms>threshold)
    if len(active)*.04<5 or peak<.015:
        raise UserError('گفتار کافی و واضح در نمونه پیدا نشد. دست‌کم چند جمله با صدای طبیعی و بدون سکوت طولانی ضبط کن.')
    start=max(0,int(active[0]*size-.15*RATE));end=min(len(data),int((active[-1]+1)*size+.15*RATE))
    data=data[start:end]
    if len(data)/RATE<6:raise UserError('بخش قابل استفادهٔ نمونه خیلی کوتاه است؛ چند جملهٔ دیگر بخوان.')
    # Preserve a reference for listening/re-enrollment; never modify the imported original.
    gain=min(3.0,.90/max(peak,.001));data=np.clip(data*gain,-.98,.98).astype('float32')
    check_cancel(cancel);sf.write(str(destination),data,RATE,subtype='PCM_16')
    return {'duration':len(data)/RATE,'clipping':clipping,'active_seconds':len(active)*.04}

def spectrogram(audio):
    """Matches OpenVoice spectrogram_torch: periodic Hann, reflect padding, no centering."""
    import numpy as np
    x=np.asarray(audio,dtype='float32').reshape(-1)
    if len(x)<1024:x=np.pad(x,(0,1024-len(x)))
    x=np.pad(x,(384,384),mode='reflect')
    frames=np.lib.stride_tricks.sliding_window_view(x,1024)[::256]
    window=(.5-.5*np.cos(2*np.pi*np.arange(1024)/1024)).astype('float32')
    f=np.fft.rfft(frames*window,axis=-1)
    mag=np.sqrt(f.real*f.real+f.imag*f.imag+1e-6).astype('float32')
    return np.ascontiguousarray(mag)

def unpack_gpu_runtime(model_dir,root):
    """Only an official, hash-verified wheel from our fixed catalog can reach this path."""
    info=next(f for f in MANIFEST[KEY]['files'] if f['name'].endswith('.whl'))
    folder=Path(root)/'runtimes'/'directml-1.24.4'
    marker=folder/'installed.json'
    try:
        if json.loads(marker.read_text())['wheel_sha256']==info['sha256'] and (folder/'onnxruntime/capi/onnxruntime_pybind11_state.pyd').is_file():return folder
    except (OSError,ValueError,KeyError):pass
    staging=folder.with_name('directml-stage-'+str(os.getpid()))
    shutil.rmtree(staging,ignore_errors=True);staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(Path(model_dir)/info['name']) as archive:
            members=archive.infolist()
            if len(members)>2500 or sum(x.file_size for x in members)>200*1024*1024:
                raise UserError('بستهٔ شتاب‌دهنده معتبر نیست.')
            for entry in members:
                name=entry.filename.replace('\\','/')
                p=Path(name)
                if p.is_absolute() or '..' in p.parts or ':' in name or (entry.external_attr>>16)&0o170000==0o120000:
                    raise UserError('مسیر داخل بستهٔ شتاب‌دهنده معتبر نیست.')
                if not name.startswith(('onnxruntime/','onnxruntime_directml-1.24.4.dist-info/')):continue
                out=staging/p
                if entry.is_dir():out.mkdir(parents=True,exist_ok=True)
                else:out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(archive.read(entry))
        atomic_bytes(staging/'installed.json',json.dumps({'wheel_sha256':info['sha256']}).encode())
        folder.parent.mkdir(parents=True,exist_ok=True)
        if folder.exists():shutil.rmtree(folder)
        os.replace(staging,folder)
    finally:shutil.rmtree(staging,ignore_errors=True)
    return folder

def import_runtime(use_gpu,model_dir,root):
    if use_gpu:
        import importlib.util
        package=unpack_gpu_runtime(model_dir,root)/'onnxruntime'
        # Explicit path prevents the frozen CPU package from winning module resolution.
        spec=importlib.util.spec_from_file_location('onnxruntime',package/'__init__.py',submodule_search_locations=[str(package)])
        module=importlib.util.module_from_spec(spec);sys.modules['onnxruntime']=module;spec.loader.exec_module(module)
        ort=module
        if 'DmlExecutionProvider' not in ort.get_available_providers():raise RuntimeError('DirectML is unavailable')
    else:
        import onnxruntime as ort
    ort.disable_telemetry_events();return ort

def session(ort,path,settings,gpu):
    options=ort.SessionOptions();options.intra_op_num_threads=settings['threads'];options.inter_op_num_threads=1
    options.enable_mem_pattern=False;options.enable_cpu_mem_arena=False
    options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_BASIC if settings['low_memory'] else ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.log_severity_level=3
    providers=[('DmlExecutionProvider',{'device_id':0}),'CPUExecutionProvider'] if gpu else ['CPUExecutionProvider']
    result=ort.InferenceSession(str(path),sess_options=options,providers=providers)
    return result

def extract_embedding(session,audio):
    import numpy as np
    data=np.asarray(audio,dtype='float32').reshape(-1)
    if 0<len(data)<RATE:
        data=np.tile(data,int(np.ceil(2*RATE/len(data))))[:2*RATE]
    values=[];step=RATE*5
    for start in range(0,len(data),step):
        piece=data[start:start+step]
        if len(piece)<RATE or float(np.sqrt(np.mean(piece**2)))<.002:continue
        value=session.run(None,{'input':spectrogram(piece)[None,:,:]})[0]
        value=np.asarray(value,dtype='float32').reshape(1,256,1)
        if not np.all(np.isfinite(value)):raise UserError('اثر صوتی معتبر از نمونه استخراج نشد.')
        values.append(value)
    if not values:raise UserError('نمونه برای ساخت پروفایل کافی نیست.')
    return np.mean(np.stack(values),axis=0,dtype='float32')

def load_embedding(path):
    import numpy as np
    if Path(path).stat().st_size>16384:raise UserError('فایل پروفایل معتبر نیست.')
    value=np.load(str(path),allow_pickle=False)
    if value.shape!=(1,256,1) or not np.all(np.isfinite(value)) or np.max(np.abs(value))>100:
        raise UserError('پروفایل آسیب دیده؛ آن را از نمونهٔ اصلی دوباره بساز.')
    return value.astype('float32')

def add_ai_notice(path):
    """Standard RIFF INFO metadata; no audible watermark or claim of tamper resistance."""
    text=b'Tixi Voice 1.1 - AI-generated personalized voice; reference used with consent.\0'
    info=b'INFO'+b'ICMT'+struct.pack('<I',len(text))+text+(b'\0' if len(text)%2 else b'')
    with Path(path).open('r+b') as f:
        head=f.read(12)
        if head[:4]!=b'RIFF' or head[8:12]!=b'WAVE':return
        f.seek(0,2);f.write(b'LIST'+struct.pack('<I',len(info))+info)
        size=f.tell()-8;f.seek(4);f.write(struct.pack('<I',size));f.flush();os.fsync(f.fileno())

def worker(request_file):
    job=Path(request_file).resolve().parent
    def progress(n,message):atomic_bytes(job/'progress.json',json.dumps({'percent':n,'message':message},ensure_ascii=False).encode('utf-8'))
    try:
        import numpy as np
        import soundfile as sf
        req=json.loads(Path(request_file).read_text(encoding='utf-8'));settings=req['plan'];gpu=bool(settings['try_gpu'])
        model_dir=Path(req['model_dir']);root=Path(req['root'])
        if settings['insufficient']:raise UserError('حافظهٔ آزاد برای صدای شخصی کافی نیست؛ برنامه‌های دیگر را ببند. صدای آماده همچنان قابل استفاده است.')
        progress(2,'بررسی شتاب‌دهنده و بارگذاری مدل شخصی…' if gpu else 'بارگذاری مدل شخصی روی CPU…')
        ort=import_runtime(gpu,model_dir,root)
        if req['action']=='probe':
            # Internal diagnostic used to verify frozen optional-runtime resolution.
            atomic_bytes(job/'result.json',json.dumps({'version':ort.__version__,'module':ort.__file__,'providers':ort.get_available_providers()}).encode('utf-8'))
            return 0
        extractor=session(ort,model_dir/'tone_extract.onnx',settings,gpu)
        provider='DirectML + CPU' if 'DmlExecutionProvider' in extractor.get_providers() else 'CPU'
        if req['action']=='enroll':
            audio,sr=sf.read(str(job/'reference.wav'),dtype='float32')
            if sr!=RATE:raise UserError('نرخ نمونهٔ مرجع معتبر نیست.')
            progress(25,'استخراج ویژگی‌های صدا · '+provider)
            vector=extract_embedding(extractor,audio)
            np.save(str(job/'embedding.npy'),vector,allow_pickle=False)
            result={'duration':len(audio)/RATE,'provider':provider,'action':'enroll'}
        elif req['action']=='render':
            target=load_embedding(req['embedding'])
            with sf.SoundFile(req['source']) as source:
                if source.samplerate!=RATE or source.channels!=1:raise UserError('صدای پایه باید WAV مونو با نرخ ۲۲۰۵۰ باشد.')
                # An utterance-derived source embedding works with both of our Piper base voices.
                base=source.read(min(len(source),RATE*18),dtype='float32')
                progress(8,'تطبیق گویندهٔ پایه با پروفایل…')
                source_vector=extract_embedding(extractor,base)
                del extractor
                converter=session(ort,model_dir/'tone_color.onnx',settings,gpu)
                provider='DirectML + CPU' if 'DmlExecutionProvider' in converter.get_providers() else 'CPU'
                total=len(source);block=max(RATE,int(RATE*float(settings['chunk_seconds'])))
                overlap=int(.07*RATE);context=int(.18*RATE);position=0;pending=None
                with sf.SoundFile(str(job/'personal.wav'),'w',samplerate=RATE,channels=1,subtype='PCM_16') as out:
                    while position<total:
                        body_len=min(block,total-position);left=max(0,position-context);right=min(total,position+body_len+context)
                        source.seek(left);audio=source.read(right-left,dtype='float32')
                        # Ensure the final frame covers the original samples; we trim back below.
                        audio=np.pad(audio,(0,512)).astype('float32')
                        spec=spectrogram(audio).T[None,:,:].copy()
                        converted=converter.run(None,{'audio':spec,'audio_length':np.array([spec.shape[2]],dtype='int64'),
                            'src_tone':source_vector,'dest_tone':target,'tau':np.array([.3],dtype='float32')})[0]
                        converted=np.asarray(converted,dtype='float32').reshape(-1)
                        offset=position-left;body=converted[offset:offset+body_len].copy()
                        if len(body)!=body_len or not np.all(np.isfinite(body)):raise UserError('خروجی مدل شخصی معتبر نیست.')
                        body=np.clip(body,-.98,.98)
                        if pending is not None:
                            n=min(len(pending),len(body));fade=np.linspace(0,1,n,dtype='float32');body[:n]=pending[-n:]*(1-fade)+body[:n]*fade
                        last=(position+body_len>=total)
                        if last:out.write(body)
                        else:out.write(body[:-overlap]);pending=body[-overlap:].copy()
                        progress(min(99,12+int(87*(position+body_len)/total)),f'صدای شخصی · {provider} · پردازش بخش‌بندی‌شده')
                        if last:break
                        position+=body_len-overlap
            add_ai_notice(job/'personal.wav')
            result={'duration':total/RATE,'provider':provider,'action':'render'}
        else:raise ValueError('Unknown worker action')
        result['gpu_requested']=gpu;result['low_memory']=settings['low_memory'];result['chunk_seconds']=settings['chunk_seconds'];result['threads']=settings['threads']
        atomic_bytes(job/'result.json',json.dumps(result,ensure_ascii=False).encode('utf-8'));progress(100,'صدای شخصی آماده است.');return 0
    except Exception as e:
        atomic_bytes(job/'error.json',json.dumps({'type':type(e).__name__,'message':str(e)},ensure_ascii=False).encode('utf-8'));return 1

def _run(root,job,action,mode,cancel,progress,**kwargs):
    from engines import run_process
    model_dir=Models(root).primary(KEY).parent
    settings=plan(mode)
    if settings['insufficient']:raise UserError('کمتر از حدود ۶۵۰ مگابایت حافظه آزاد است؛ برای صدای شخصی برنامه‌های دیگر را ببند و دوباره تلاش کن.')
    cooldown=Path(root)/'runtimes/gpu-cooldown.json'
    try:
        if time.time()-json.loads(cooldown.read_text())['time']<3600:settings['try_gpu']=False
    except (OSError,ValueError,KeyError):pass
    for attempt in range(2):
        check_cancel(cancel)
        for name in ('result.json','error.json','progress.json','personal.wav','embedding.npy'):(job/name).unlink(missing_ok=True)
        request=job/'clone-request.json'
        atomic_bytes(request,json.dumps(dict(action=action,root=str(root),model_dir=str(model_dir),plan=settings,**kwargs),ensure_ascii=False).encode('utf-8'))
        cmd=[sys.executable,'--clone-worker',str(request)] if getattr(sys,'frozen',False) else [sys.executable,str(ROOT/'main.py'),'--clone-worker',str(request)]
        try:
            run_process(cmd,job,cancel,progress,tts=True,timeout=3600)
            result=json.loads((job/'result.json').read_text(encoding='utf-8'));result['fallback']=bool(attempt or (settings['try_gpu'] and result.get('provider')=='CPU'))
            if settings['try_gpu'] and result.get('provider')=='CPU':atomic_bytes(cooldown,json.dumps({'time':time.time()}).encode())
            return result
        except UserError:
            if attempt:raise UserError('صدای شخصی روی این سیستم کامل نشد، حتی در حالت CPU کم‌مصرف. برنامه‌های دیگر را ببند و نمونه/متن کوتاه‌تر را امتحان کن. صدای آماده و سایر بخش‌ها همچنان در دسترس‌اند.')
            if settings['try_gpu']:atomic_bytes(cooldown,json.dumps({'time':time.time()}).encode())
            progress(-1,'شتاب‌دهنده یا حافظه کافی نبود؛ یک تلاش دوباره با CPU و بخش‌های کوچک‌تر…')
            settings=plan('lite',force_retry=True)

def enroll(root,source,mode,cancel,progress):
    Models(root).verify(KEY,cancel,progress)
    job=Path(tempfile.mkdtemp(prefix='profile-',dir=Path(root)/'temp'))
    try:
        progress(-1,'بررسی نمونه و آماده‌سازی پروفایل…');quality=prepare_reference(Path(source),job/'reference.wav',cancel)
        result=_run(root,job,'enroll',mode,cancel,progress)
        result.update(job=str(job),reference=str(job/'reference.wav'),embedding=str(job/'embedding.npy'),revision=MANIFEST[KEY]['revision'],quality=quality)
        return result
    except BaseException:shutil.rmtree(job,ignore_errors=True);raise

def render(root,source,embedding,mode,cancel,progress):
    Models(root).verify(KEY,cancel,progress)
    job=Path(tempfile.mkdtemp(prefix='personal-',dir=Path(root)/'temp'))
    try:
        result=_run(root,job,'render',mode,cancel,progress,source=str(source),embedding=str(embedding))
        result.update(job=str(job),audio=str(job/'personal.wav'));return result
    except BaseException:shutil.rmtree(job,ignore_errors=True);raise

def personalized_speech(root,key,text,speed,profile,embedding,mode,cancel,progress,effect='none'):
    from engines import synthesize
    if profile['revision']!=MANIFEST[KEY]['revision']:
        raise UserError('این پروفایل برای نسخهٔ دیگری از مدل ساخته شده است؛ با نمونهٔ مرجع دوباره آن را بساز.')
    base=None
    try:
        base=synthesize(root,key,text,speed,cancel,lambda n,t:progress(-1 if n<0 else n//2,'مرحلهٔ ۱/۲ · '+t))
        check_cancel(cancel)
        result=render(root,base['audio'],embedding,mode,cancel,lambda n,t:progress(-1 if n<0 else 50+n//2,'مرحلهٔ ۲/۲ · '+t))
        if effect!='none':
            from effects import apply_effect
            apply_effect(Path(result['audio']),effect,cancel,progress)
            result['effect']=effect
        result.update(text=base['text'],language=base['language'],model=KEY,source='صدای مصنوعی · '+profile['name'])
        return result
    finally:
        if base:shutil.rmtree(base['job'],ignore_errors=True)
