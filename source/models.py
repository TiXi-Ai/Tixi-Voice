# SPDX-License-Identifier: GPL-3.0-or-later
"""Only explicit model installation uses network. Pinned sources, SHA-256, atomic files."""
from __future__ import annotations
import hashlib
import os
import shutil
from pathlib import Path
from core import MANIFEST, UserError, check_cancel

class Models:
    def __init__(self, root):
        self.root=Path(root)/'models';self.root.mkdir(parents=True,exist_ok=True)

    def files(self, key):
        return [(self.root/key/f['name'], f) for f in MANIFEST[key]['files']]

    def ready(self, key):
        return all(p.is_file() and p.stat().st_size==f['size'] for p,f in self.files(key))

    def primary(self, key):
        return self.files(key)[0][0]

    def verify(self, key, cancel, progress=lambda *_:None):
        for p,f in self.files(key):
            check_cancel(cancel)
            if not p.is_file() or p.stat().st_size!=f['size']:
                raise UserError('مدل نصب نیست یا ناقص است؛ از بخش «مدل‌های آفلاین» آن را نصب کن.')
            progress(-1,'بررسی سلامت مدل…')
            if self.hash_file(p,cancel)!=f['sha256']:
                raise UserError('مدل آسیب دیده یا نسخهٔ دیگری است؛ آن را از بخش مدل‌ها حذف و دوباره نصب کن.')
        return self.primary(key)

    @staticmethod
    def hash_file(path, cancel):
        h=hashlib.sha256()
        with Path(path).open('rb') as f:
            while True:
                check_cancel(cancel);chunk=f.read(1024*1024)
                if not chunk:break
                h.update(chunk)
        return h.hexdigest()

    def install(self,key,cancel,progress,local=None):
        total=sum(f['size'] for f in MANIFEST[key]['files']);complete=0
        folder=self.root/key;folder.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(folder).free < total + 20*1024*1024:
            raise UserError('فضای خالی کافی برای نصب مدل وجود ندارد.')
        for target,f in self.files(key):
            check_cancel(cancel)
            if target.is_file() and target.stat().st_size==f['size'] and self.hash_file(target,cancel)==f['sha256']:
                complete+=f['size'];continue
            part=target.with_name(target.name+'.part')
            if local:
                source=Path(local)/f['name']
                if not source.is_file() or source.stat().st_size!=f['size']:
                    raise UserError('فایل درست در پوشه پیدا نشد:\n'+f['name'])
                if source.resolve()==part.resolve():
                    raise UserError('پوشهٔ فایل‌های کامل مدل را انتخاب کن، نه فایل موقت دانلود.')
                with source.open('rb') as src,part.open('wb') as out:
                    done=0
                    while True:
                        check_cancel(cancel);chunk=src.read(1024*1024)
                        if not chunk:break
                        out.write(chunk);done+=len(chunk)
                        progress(int(100*(complete+done)/total),'کپی و بررسی مدل…')
                    out.flush();os.fsync(out.fileno())
            else:
                self._download(f,part,complete,total,cancel,progress)
            progress(-1,'تأیید SHA-256 مدل…')
            if part.stat().st_size!=f['size'] or self.hash_file(part,cancel)!=f['sha256']:
                part.unlink(missing_ok=True)
                raise UserError('سلامت فایل دانلودشده تأیید نشد. دوباره تلاش کن یا فایل رسمی را از پوشه نصب کن.')
            check_cancel(cancel);os.replace(part,target);complete+=f['size']
        progress(100,'مدل آمادهٔ استفادهٔ آفلاین است.')
        return key

    def _download(self,f,part,complete,total,cancel,progress):
        import requests
        offset=part.stat().st_size if part.exists() else 0
        if offset>f['size']:part.unlink();offset=0
        if offset==f['size']:return
        headers={'User-Agent':'TixiVoice/1.0','Accept-Encoding':'identity'}
        if offset:headers['Range']=f'bytes={offset}-'
        try:
            with requests.get(f['url'],headers=headers,stream=True,timeout=(12,12)) as r:
                r.raise_for_status()
                if offset and r.status_code==206:
                    if not r.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
                        raise UserError('پاسخ ادامهٔ دانلود معتبر نیست؛ دوباره تلاش کن.')
                elif r.status_code==200:offset=0
                else:raise UserError('پاسخ دانلود مدل قابل استفاده نیست.')
                done=offset
                with part.open('ab' if offset else 'wb') as out:
                    for chunk in r.iter_content(128*1024):
                        check_cancel(cancel)
                        if not chunk:continue
                        done+=len(chunk)
                        if done>f['size']:
                            raise UserError('اندازهٔ دانلود با مدل رسمی مطابقت ندارد.')
                        out.write(chunk)
                        progress(int(100*(complete+done)/total),f'دانلود مدل: {(complete+done)/1048576:.1f} / {total/1048576:.1f} MB')
                    out.flush();os.fsync(out.fileno())
        except requests.RequestException as e:
            check_cancel(cancel)
            raise UserError('دانلود انجام نشد. اتصال اینترنت یا دسترسی به Hugging Face را بررسی کن. فایل ناقص برای ادامهٔ دانلود نگه داشته می‌شود؛ نصب از پوشه هم ممکن است.') from e

    def remove(self,key):
        # Only known model IDs from our own immutable manifest are allowed.
        if key not in MANIFEST:raise ValueError('Unknown model')
        folder=self.root/key
        if key=='clone-openvoice':
            runtimes=self.root.parent/'runtimes'
            for runtime in [runtimes/'directml-1.24.4']+list(runtimes.glob('directml-stage-*')):
                if runtime.is_symlink():runtime.unlink()
                elif runtime.exists():shutil.rmtree(runtime)
            (runtimes/'gpu-cooldown.json').unlink(missing_ok=True)
        if folder.exists():shutil.rmtree(folder)
