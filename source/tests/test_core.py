# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import Store,UserError,Cancelled,atomic_bytes,atomic_copy,valid_text,chunks,make_srt,parse_transcript,stamp
from models import Models

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root)
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def wav(self):
        p=self.root/'original.wav'
        with wave.open(str(p),'wb') as f:
            f.setparams((1,2,16000,0,'NONE',''));f.writeframes(b'\x10\x00'*16000)
        return p
    def test_durable_history_and_settings(self):
        row=self.store.add('stt','fa','stt-small','متن فارسی',source='آزمون.wav',segments=[{'start':0,'end':1000,'text':'متن فارسی'}])
        self.store.set_setting('theme','light');self.store.close();self.store=Store(self.root)
        self.assertEqual(self.store.get(row['id'])['text'],'متن فارسی');self.assertEqual(self.store.setting('theme'),'light')
    def test_audio_owned_copy_and_delete(self):
        original=self.wav();row=self.store.add('tts','en','tts-en','hello',audio=original,duration=1)
        p=self.store.audio_path(row);self.assertEqual(p.read_bytes(),original.read_bytes())
        self.store.remove(row['id']);self.assertFalse(p.exists());self.assertTrue(original.exists());self.assertEqual(self.store.count(),0)
    def test_path_traversal_guard(self):
        with self.assertRaises(UserError):self.store.audio_path({'audio':'../../someone.wav'})
    def test_search_and_filter(self):
        self.store.add('stt','fa','stt-small','کتاب یک');self.store.add('tts','en','tts-en','Hello WORLD')
        self.assertEqual(len(self.store.search('كتاب يك')),1);self.assertEqual(len(self.store.search('world')),1)
        self.assertEqual(len(self.store.search(kind='tts')),1);self.assertEqual(self.store.search("' OR 1=1 --"),[])
    def test_chunks_and_text_limit(self):
        text=('سلام به شما. '*400).strip();parts=list(chunks(text));self.assertTrue(parts);self.assertTrue(all(len(p)<=350 for p in parts))
        with self.assertRaises(UserError):valid_text('x'*10001)
        with self.assertRaises(UserError):valid_text(' ... ')
        with self.assertRaises(UserError):valid_text('hello\x00')
    def test_srt_and_parse(self):
        text,segs=parse_transcript({'transcription':[{'offsets':{'from':1234,'to':4567},'text':' سلام '} ]})
        self.assertEqual(text,'سلام');self.assertIn('00:00:01,234 --> 00:00:04,567',make_srt(segs));self.assertEqual(stamp(3600001),'01:00:00,001')
    def test_atomic_failure_keeps_original(self):
        path=self.root/'existing.txt';path.write_bytes(b'old')
        with patch('core.os.replace',side_effect=OSError('disk error')):
            with self.assertRaises(OSError):atomic_bytes(path,b'new')
        self.assertEqual(path.read_bytes(),b'old');self.assertEqual(list(self.root.glob('.tixi-*.tmp')),[])
    def test_model_install_verifies_hash(self):
        import hashlib
        source=self.root/'incoming';source.mkdir();data=b'test model';(source/'voice.onnx').write_bytes(data)
        manifest={'test':{'files':[{'name':'voice.onnx','size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'url':'https://example.invalid/test'}]}}
        with patch('models.MANIFEST',manifest):
            m=Models(self.root);m.install('test',threading.Event(),lambda *_:None,source)
            self.assertTrue(m.ready('test'));self.assertEqual(m.verify('test',threading.Event()).read_bytes(),data)
            m.primary('test').write_bytes(b'other file')
            with self.assertRaises(UserError):m.verify('test',threading.Event())
            m.remove('test');self.assertFalse(m.ready('test'))
    def test_cancel_before_model_copy(self):
        c=threading.Event();c.set()
        with self.assertRaises(Cancelled):Models(self.root).verify('tts-fa',c)
    def test_future_schema_rejected(self):
        self.store.db.execute('PRAGMA user_version=9');self.store.db.commit()
        with self.assertRaises(UserError):Store(self.root)

class AudioTests(unittest.TestCase):
    def test_resample_stereo_and_silence(self):
        import numpy as np
        import soundfile as sf
        from audio import prepare_audio
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'source.wav';dest=root/'out.wav'
            t=np.arange(48000)/48000
            sf.write(str(src),np.column_stack([0.2*np.sin(2*np.pi*440*t)]*2),48000)
            duration=prepare_audio(src,dest,threading.Event(),lambda *_:None)
            info=sf.info(str(dest));self.assertAlmostEqual(duration,1);self.assertEqual(info.channels,1);self.assertEqual(info.samplerate,16000)
            self.assertAlmostEqual(info.duration,1,places=2)
            sf.write(str(src),np.zeros(48000),48000)
            with self.assertRaises(UserError):prepare_audio(src,dest,threading.Event(),lambda *_:None)
    def test_unsupported_audio_and_cancel(self):
        from audio import inspect_audio,prepare_audio
        with self.assertRaises(UserError):inspect_audio(Path('something.m4a'))
        import numpy as np,soundfile as sf
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'x.wav';sf.write(str(p),np.ones(16000,dtype='float32')*0.1,16000)
            c=threading.Event();c.set()
            with self.assertRaises(Cancelled):prepare_audio(p,Path(tmp)/'out.wav',c,lambda *_:None)

if __name__=='__main__':unittest.main()
