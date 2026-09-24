import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app.DATA = Path(self.tmp.name)
        app.init_db()
        self.student = app.save('students', dict(name='Тестовый Магистрант', program='Информатика', year=date.today().year, supervisor='Тестовый Руководитель', email='', status='Обучается'))
        self.sid = self.student['id']

    def tearDown(self):
        self.tmp.cleanup()

    def topic(self):
        return app.save('topics',dict(student_id=self.sid,title='Исследование информационных систем',status='Черновик',approved_on=''))

    def attestation(self, days=7):
        y=date.today().year
        return app.save('attestations',dict(student_id=self.sid,academic_year=f'{y}/{y+1}',due_date=(date.today()+timedelta(days=days)).isoformat(),status='Запланирована',result=''))

    def test_separate_persistent_databases(self):
        self.assertEqual(len(list(app.DATA.glob('*.sqlite3'))),7)
        app.init_db()
        self.assertEqual(app.get_record('students', self.sid)['name'],'Тестовый Магистрант')

    def test_invalid_external_reference_rejected(self):
        with self.assertRaises(app.ValidationError):
            app.save('topics',dict(student_id='missing',title='Исследование данных',status='Черновик'))
        self.assertFalse(app.records('topics'))

    def test_related_student_cannot_be_deleted(self):
        topic=self.topic()
        with self.assertRaises(app.ValidationError): app.delete('students',self.sid)
        app.delete('topics',topic['id'])
        app.delete('students',self.sid)
        self.assertFalse(app.records('students'))

    def test_topic_history_and_unique_topic(self):
        topic=self.topic()
        changed=app.save('topics',dict(student_id=self.sid,title='Новая тема исследования данных',status='Черновик'),topic['id'])
        self.assertEqual(changed['history'][0]['old_title'],topic['title'])
        with self.assertRaises(app.ValidationError): self.topic()

    def test_duplicate_exam_and_validation(self):
        data=dict(student_id=self.sid,subject='Методология',semester=1,attempt=1,exam_date=date.today().isoformat(),grade=90,status='Сдан')
        app.save('exams',data)
        with self.assertRaises(app.ValidationError): app.save('exams',data)
        with self.assertRaises(app.ValidationError): app.save('exams',{**data,'grade':101,'attempt':2})
        app.save('exams',{**data,'attempt':2})
        self.assertEqual(len(app.records('exams')),2)

    def test_shared_publication_doi_and_author_integrity(self):
        other=app.save('students',{**self.student,'name':'Второй Магистрант'})
        data=dict(title='Совместная научная статья',journal='Тестовый журнал',year=date.today().year,doi='https://doi.org/10.1234/Test',vak=True,scopus=True,verified=False,student_ids=[self.sid,other['id']])
        pub=app.save('publications',data)
        self.assertEqual(pub['doi'],'10.1234/test')
        self.assertEqual(len(pub['student_ids']),2)
        with self.assertRaises(app.ValidationError): app.save('publications',data)
        with self.assertRaises(app.ValidationError): app.delete('students',other['id'])
        app.delete('publications',pub['id'])
        with app.db('publications') as con: self.assertEqual(con.execute('SELECT COUNT(*) FROM authors').fetchone()[0],0)

    def test_reminder_idempotency_and_completion(self):
        item=self.attestation()
        app.reminders();app.reminders()
        self.assertEqual(len(app.snapshot()['notifications']),1)
        app.reminders(date.today()+timedelta(days=6))
        self.assertEqual(len(app.snapshot()['notifications']),2)
        app.save('attestations',{**item,'status':'Пройдена','result':'Аттестован'},item['id'])
        app.reminders()
        self.assertEqual(len(app.snapshot()['notifications']),0)

    def test_plan_versions_and_immutable_approval(self):
        self.topic(); self.attestation()
        year=f'{date.today().year}/{date.today().year+1}'
        p=app.create_plan(dict(student_id=self.sid,academic_year=year))
        self.assertEqual(len(p['items']),4)
        self.assertEqual(app.create_plan(dict(student_id=self.sid,academic_year=year))['version'],2)
        with self.assertRaises(app.ValidationError): app.update_plan(p['id'],{'status':'Утверждён'})
        app.update_plan(p['id'],{'status':'На согласовании'})
        app.update_plan(p['id'],{'status':'Утверждён'})
        edited=[dict(x) for x in p['items']];edited[0]['activity']='Незаконное изменение утверждённого плана'
        with self.assertRaises(app.ValidationError): app.update_plan(p['id'],{'status':'Утверждён','items':edited})
        with self.assertRaises(app.ValidationError): app.delete('plans',p['id'])

    def test_docx_export_is_valid_xml_archive(self):
        self.topic()
        y=date.today().year
        p=app.create_plan(dict(student_id=self.sid,academic_year=f'{y}/{y+1}'))
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(app.export_docx(p['id']))) as z:
            self.assertIsNone(z.testzip())
            ET.fromstring(z.read('word/document.xml'))
            self.assertIn('Индивидуальный план',z.read('word/document.xml').decode())

    def test_demo_dataset(self):
        app.delete('students',self.sid)
        app.demo()
        self.assertEqual(len(app.records('students')),3)
        self.assertEqual(len(app.records('plans')),1)
        self.assertEqual(len(app.snapshot()['notifications']),3)

    def test_authenticated_http_crud_and_csrf(self):
        salt='12'*16
        (app.DATA/'auth.json').write_text(json.dumps({'salt':salt,'hash':app.password_hash('test-password',salt)}))
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def req(path,method='GET',data=None,headers=None):
            request=urllib.request.Request(base+path,method=method,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json',**(headers or {})})
            try: return urllib.request.urlopen(request)
            except urllib.error.HTTPError as e: return e
        try:
            self.assertEqual(req('/api/state').status,401)
            self.assertEqual(req('/').status,200)
            self.assertEqual(req('/api/login','POST',{'password':'wrong'}).status,401)
            login=req('/api/login','POST',{'password':'test-password'})
            self.assertEqual(login.status,200)
            cookie=login.headers['Set-Cookie'].split(';')[0]
            headers={'Cookie':cookie}
            snapshot=json.load(req('/api/state',headers=headers))
            self.assertEqual(req('/api/students','POST',{},headers).status,403)
            headers['X-CSRF-Token']=snapshot['csrf']
            data={**self.student,'name':'HTTP Пользователь'}
            response=req('/api/students','POST',data,headers)
            self.assertEqual(response.status,201)
            ident=json.load(response)['id']
            self.assertEqual(req('/api/students/'+ident,'DELETE',headers=headers).status,200)
            self.assertEqual(req('/api/logout','POST',{},headers).status,200)
            self.assertEqual(req('/api/state',headers=headers).status,401)
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__': unittest.main(verbosity=2)
