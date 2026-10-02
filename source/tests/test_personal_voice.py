# SPDX-License-Identifier: GPL-3.0-or-later
import sys,unittest,tempfile,sqlite3,threading,struct,json,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
from core import Store,UserError,Cancelled
from hardware import Resources,plan
from personal_voice import prepare_reference,spectrogram,add_ai_notice,load_embedding,unpack_gpu_runtime,_run,KEY

class PersonalVoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root)
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def fixture(self,seconds=10):
        path=self.root/'source.wav';x=np.sin(np.arange(int(22050*seconds),dtype='float32')*.05)*.2
        sf.write(str(path),x,22050);return path
    def embedding(self):
        p=self.root/'embedding.npy';np.save(p,np.ones((1,256,1),dtype='float32'),allow_pickle=False);return p
    def test_profile_consent_and_delete(self):
        source=self.fixture();emb=self.embedding()
        with self.assertRaises(UserError):self.store.add_profile('test','fa',source,emb,'rev',10,False)
        p=self.store.add_profile('صدای من','fa',source,emb,'rev',10,True)
        folder=self.store.profile_directory(p['id']);self.assertTrue((folder/'reference.wav').exists());self.assertTrue((folder/'embedding.npy').exists())
        self.assertEqual(len(self.store.profiles()),1);self.store.remove_profile(p['id']);self.assertFalse(folder.exists());self.assertTrue(source.exists());self.assertEqual(self.store.profiles(),[])
    def test_profile_delete_failure_keeps_row(self):
        p=self.store.add_profile('test','en',self.fixture(),self.embedding(),'rev',10,True)
        with patch('core.shutil.rmtree',side_effect=PermissionError):
            with self.assertRaises(PermissionError):self.store.remove_profile(p['id'])
        self.assertIsNotNone(self.store.profile(p['id']))
    def test_profile_path_escape_rejected(self):
        for text in ['../outputs','bad','c:\\file','a'*33]:
            with self.assertRaises(UserError):self.store.profile_directory(text)
    def test_profile_validation(self):
        with self.assertRaises(UserError):self.store.add_profile('','en',self.fixture(),self.embedding(),'rev',10,True)
        p=self.embedding();np.save(p,np.array([float('nan')]))
        with self.assertRaises(UserError):load_embedding(p)
    def test_reference_validation(self):
        src=self.fixture();dest=self.root/'clean.wav';q=prepare_reference(src,dest,threading.Event());self.assertGreater(q['duration'],8);self.assertTrue(src.exists())
        self.fixture(3)
        with self.assertRaises(UserError):prepare_reference(src,dest,threading.Event())
        sf.write(str(src),np.zeros(220500),22050)
        with self.assertRaises(UserError):prepare_reference(src,dest,threading.Event())
    def test_cancel_reference(self):
        c=threading.Event();c.set()
        with self.assertRaises(Cancelled):prepare_reference(self.fixture(),self.root/'clean.wav',c)
    def test_spectrogram_shape_dtype(self):
        x=np.sin(np.arange(22050,dtype='float32')*.03);s=spectrogram(x)
        self.assertEqual(s.shape,(86,513));self.assertEqual(s.dtype,np.float32);self.assertTrue(np.isfinite(s).all())
    def test_wav_notice_preserves_audio(self):
        path=self.fixture();before,sr=sf.read(str(path));add_ai_notice(path);after,_=sf.read(str(path))
        np.testing.assert_array_equal(before,after);raw=path.read_bytes();self.assertIn(b'AI-generated personalized voice',raw[-512:]);self.assertEqual(struct.unpack('<I',raw[4:8])[0],len(raw)-8)
    def test_conservative_resource_plan(self):
        low=plan('auto',Resources(4096,1000,2,True,64));self.assertFalse(low['try_gpu']);self.assertEqual(low['threads'],1)
        normal=plan('auto',Resources(8192,3500,8,True,64));self.assertTrue(normal['try_gpu']);self.assertLessEqual(normal['threads'],4)
        cpu=plan('lite',Resources(16000,9000,16,True,64));self.assertFalse(cpu['try_gpu'])
        self.assertTrue(plan('auto',Resources(2000,400,2,True,64))['insufficient'])
    def test_retry_uses_low_memory_cpu(self):
        job=self.root/'temp'/'retry';job.mkdir();calls=[]
        def fake(cmd,cwd,cancel,progress,**kwargs):
            r=json.loads((cwd/'clone-request.json').read_text(encoding='utf-8'));calls.append(r['plan'])
            if len(calls)==1:raise UserError('simulated GPU failure')
            (cwd/'result.json').write_text(json.dumps({'provider':'CPU','duration':1}))
        first=plan('auto',Resources(8192,4000,8,True,64));retry=plan('lite',Resources(8192,4000,8,True,64),True)
        with patch('personal_voice.plan',side_effect=[first,retry]),patch('engines.run_process',side_effect=fake):
            result=_run(self.root,job,'enroll','auto',threading.Event(),lambda *_:None)
        self.assertTrue(result['fallback']);self.assertTrue(calls[0]['try_gpu']);self.assertFalse(calls[1]['try_gpu']);self.assertEqual(calls[1]['threads'],1)
    def test_cancel_does_not_retry(self):
        job=self.root/'temp'/'cancel';job.mkdir()
        settings=plan('lite',Resources(8192,4000,8,True,64))
        with patch('personal_voice.plan',return_value=settings),patch('engines.run_process',side_effect=Cancelled) as run:
            with self.assertRaises(Cancelled):_run(self.root,job,'enroll','lite',threading.Event(),lambda *_:None)
        self.assertEqual(run.call_count,1)
    def test_remove_clone_cleans_optional_runtime(self):
        from models import Models
        folder=self.root/'models'/KEY;folder.mkdir();(folder/'dummy').write_text('model')
        runtime=self.root/'runtimes'/'directml-1.24.4';runtime.mkdir();(runtime/'dummy').write_text('runtime')
        Models(self.root).remove(KEY);self.assertFalse(folder.exists());self.assertFalse(runtime.exists())
    def test_runtime_archive_traversal_rejected(self):
        model=self.root/'bad-model';model.mkdir();wheel=model/'runtime.whl'
        with zipfile.ZipFile(wheel,'w') as z:z.writestr('../escape.dll',b'bad')
        with patch('personal_voice.MANIFEST',{KEY:{'files':[{'name':'runtime.whl','sha256':'fake'}]}}):
            with self.assertRaises(UserError):unpack_gpu_runtime(model,self.root)
        self.assertFalse((self.root/'escape.dll').exists())

class MigrationTests(unittest.TestCase):
    def test_v1_history_preserved_and_backed_up(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);db=sqlite3.connect(root/'history.sqlite3')
            db.executescript('''CREATE TABLE history(id TEXT PRIMARY KEY,kind TEXT,language TEXT,model TEXT,text TEXT,source TEXT,audio TEXT,duration REAL,segments TEXT,created INTEGER);
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
            INSERT INTO history VALUES('old','stt','fa','stt-base','متن قبلی','','',1,'[]',1000);
            INSERT INTO settings VALUES('theme','light');PRAGMA user_version=1;''');db.commit();db.close()
            store=Store(root);self.assertEqual(store.get('old')['text'],'متن قبلی');self.assertEqual(store.setting('theme'),'light');self.assertEqual(store.profiles(),[]);store.close()
            backup=root/'backups/history-before-v1.1.sqlite3';self.assertTrue(backup.exists())
            db=sqlite3.connect(backup);self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1);self.assertEqual(db.execute('select count(*) from history').fetchone()[0],1);db.close()

if __name__=='__main__':unittest.main()
