# SPDX-License-Identifier: GPL-3.0-or-later
"""Local paths, durable history, exports and shared validation. No network here."""
from __future__ import annotations
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / 'assets'
MANIFEST = json.loads((ASSETS / 'models.json').read_text(encoding='utf-8'))
MAX_TEXT = 10000
MAX_AUDIO_SECONDS = 90 * 60
MAX_AUDIO_BYTES = 512 * 1024 * 1024

class UserError(Exception):
    pass

class Cancelled(Exception):
    pass

def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled()

def data_directory():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'TixiVoice'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'TixiVoice'

def atomic_bytes(path: Path, data: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.tixi-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)

def atomic_copy(source: Path, dest: Path):
    source, dest = Path(source), Path(dest)
    if source.resolve() == dest.resolve():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.tixi-', suffix='.tmp', dir=dest.parent)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as inp:
            shutil.copyfileobj(inp, out, 1024 * 1024)
            out.flush(); os.fsync(out.fileno())
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)

def valid_text(text: str):
    text = text.strip()
    if not text or not any(c.isalnum() for c in text):
        raise UserError('متن خالی است یا حرف و عدد قابل خواندن ندارد.')
    if len(text) > MAX_TEXT:
        raise UserError('حداکثر طول متن در هر تبدیل ۱۰٬۰۰۰ کاراکتر است؛ متن را به چند بخش تقسیم کن.')
    if '\x00' in text:
        raise UserError('متن شامل کاراکتر نامعتبر است.')
    return text

def chunks(text, limit=350):
    """Bound synthesis memory, splitting at punctuation/whitespace where possible."""
    for sentence in re.split(r'(?<=[.!?؟؛\n])\s*', text):
        sentence = sentence.strip()
        while sentence:
            if len(sentence) <= limit:
                yield sentence; break
            cut = sentence.rfind(' ', 0, limit + 1)
            if cut < limit // 3:
                cut = limit
            yield sentence[:cut].strip()
            sentence = sentence[cut:].strip()

def stamp(ms):
    ms = max(0, int(ms)); seconds, milli = divmod(ms, 1000)
    hours, seconds = divmod(seconds, 3600); minutes, seconds = divmod(seconds, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02},{milli:03}'

def make_srt(segments):
    out = []
    for row in segments:
        text = str(row.get('text', '')).strip()
        if not text: continue
        start, end = int(row.get('start', 0)), int(row.get('end', 0))
        out.append(f'{len(out) + 1}\n{stamp(start)} --> {stamp(max(end, start+1))}\n{text}\n')
    return '\n'.join(out)

def parse_transcript(obj):
    segments = []
    for row in obj.get('transcription', []):
        offsets = row.get('offsets', {})
        text = str(row.get('text', '')).strip()
        if text:
            segments.append({'start': max(0, int(offsets.get('from', 0))),
                             'end': max(0, int(offsets.get('to', 0))), 'text': text})
    return '\n'.join(x['text'] for x in segments), segments

class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in ['models','outputs','temp','recordings','profiles','runtimes','backups']:
            (self.root / folder).mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / 'history.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version > 2:
            self.db.close(); raise UserError('نسخهٔ پایگاه داده از برنامه جدیدتر است؛ نسخهٔ قبلی را اجرا نکن.')
        if version == 1:
            backup=self.root/'backups'/'history-before-v1.1.sqlite3'
            if not backup.exists():
                temp=backup.with_suffix('.part')
                try:
                    target=sqlite3.connect(temp)
                    try:self.db.backup(target)
                    finally:target.close()
                    os.replace(temp,backup)
                finally:temp.unlink(missing_ok=True)
        self.db.executescript('''BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS history (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, language TEXT NOT NULL, model TEXT NOT NULL,
        text TEXT NOT NULL, source TEXT NOT NULL, audio TEXT NOT NULL DEFAULT '',
        duration REAL NOT NULL DEFAULT 0, segments TEXT NOT NULL DEFAULT '[]', created INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS history_created ON history(created);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS profiles (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, language TEXT NOT NULL, revision TEXT NOT NULL,
        duration REAL NOT NULL, created INTEGER NOT NULL, consent_at INTEGER NOT NULL);
        PRAGMA user_version=2;
        COMMIT;''')
        self.db.commit()

    def setting(self, key, default=''):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, str(value)))

    def add(self, kind, language, model, text, source='', audio=None, duration=0, segments=None):
        if kind not in ('tts','stt'): raise ValueError('Invalid history kind')
        id = uuid.uuid4().hex
        relative = ''
        dest = self.root / 'outputs' / (id + '.wav')
        try:
            if audio:
                atomic_copy(Path(audio), dest)
                relative = str(dest.relative_to(self.root))
            with self.db:
                self.db.execute('INSERT INTO history VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (id,kind,language,model,text,source,relative,float(duration),
                     json.dumps(segments or [], ensure_ascii=False),time.time_ns()//1000000))
        except Exception:
            dest.unlink(missing_ok=True); raise
        return self.get(id)

    def get(self, id):
        row = self.db.execute('SELECT * FROM history WHERE id=?',(id,)).fetchone()
        return dict(row) if row else None

    def search(self, term='', kind='', limit=300):
        term = term.replace('ي','ی').replace('ك','ک').casefold().strip()
        # Limit displayed results, not the underlying history; never interpolate queries.
        rows = self.db.execute('SELECT * FROM history WHERE (?="" OR kind=?) ORDER BY created DESC', (kind,kind))
        found=[]
        for row in rows:
            item=dict(row)
            hay=(item['text']+' '+item['source']).replace('ي','ی').replace('ك','ک').casefold()
            if not term or term in hay:
                found.append(item)
                if len(found)>=limit:break
        return found

    def count(self):
        return self.db.execute('SELECT count(*) FROM history').fetchone()[0]

    def audio_path(self, item):
        if not item or not item.get('audio'):return None
        p=(self.root / item['audio']).resolve()
        if not p.is_relative_to(self.root/'outputs') or p.suffix.lower()!='.wav':
            raise UserError('مسیر فایل تاریخچه معتبر نیست.')
        return p if p.is_file() else None

    def remove(self, id):
        item=self.get(id)
        if not item:return
        audio=self.audio_path(item)
        # Do not delete a DB row when its file cannot be removed.
        if audio:audio.unlink(missing_ok=True)
        with self.db:self.db.execute('DELETE FROM history WHERE id=?',(id,))

    def profiles(self):
        return [dict(x) for x in self.db.execute('SELECT * FROM profiles ORDER BY created DESC')]

    def profile(self, id):
        row=self.db.execute('SELECT * FROM profiles WHERE id=?',(id,)).fetchone()
        return dict(row) if row else None

    def profile_directory(self, id):
        if not isinstance(id,str) or not re.fullmatch(r'[0-9a-f]{32}',id):
            raise UserError('شناسهٔ پروفایل معتبر نیست.')
        p=(self.root/'profiles'/id).resolve()
        if not p.is_relative_to(self.root/'profiles'):
            raise UserError('مسیر پروفایل معتبر نیست.')
        return p

    def add_profile(self,name,language,reference,embedding,revision,duration,consent):
        from personal_voice import load_embedding
        if consent is not True:raise UserError('تأیید مالکیت صدا یا اجازهٔ صاحب صدا لازم است.')
        name=str(name).strip()
        if not name or len(name)>60 or '\x00' in name:raise UserError('نام پروفایل باید ۱ تا ۶۰ کاراکتر باشد.')
        if language not in ('fa','en'):raise UserError('زبان نمونه معتبر نیست.')
        if len(self.profiles())>=20:raise UserError('حداکثر ۲۰ پروفایل قابل نگهداری است؛ ابتدا یکی را حذف کن.')
        load_embedding(embedding)
        id=uuid.uuid4().hex;folder=self.profile_directory(id);folder.mkdir()
        now=time.time_ns()//1000000
        try:
            atomic_copy(Path(reference),folder/'reference.wav')
            atomic_copy(Path(embedding),folder/'embedding.npy')
            atomic_bytes(folder/'consent.json',json.dumps({'confirmed':True,'time':now,'scope':'own voice or explicit speaker permission','revision':revision},ensure_ascii=False).encode('utf-8'))
            with self.db:self.db.execute('INSERT INTO profiles VALUES (?,?,?,?,?,?,?)',(id,name,language,revision,float(duration),now,now))
        except BaseException:shutil.rmtree(folder,ignore_errors=True);raise
        return self.profile(id)

    def remove_profile(self,id):
        if not self.profile(id):return
        folder=self.profile_directory(id)
        # Do not report deletion or remove the row while reference data remain undeleted.
        # If a filesystem error interrupts removal, the row remains available for retry.
        if folder.exists():shutil.rmtree(folder)
        with self.db:self.db.execute('DELETE FROM profiles WHERE id=?',(id,))

    def close(self):
        self.db.close()
