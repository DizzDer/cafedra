import io,json,os,tempfile,unittest,zipfile
from pathlib import Path
from datetime import date
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app,documents,assistant_service as ai

class UpgradeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();app.DATA=Path(self.tmp.name);app.init_db();app.demo()
  self.sid=app.records('students')[0]['id'];self.plan=app.records('plans')[0];self.year=self.plan['academic_year']
  self.org=documents.settings(app);self.org.update(department_ru='Кафедра информационных систем',department_kk='Ақпараттық жүйелер кафедрасы',head_name='Тестовый Подписант');documents.save_settings(app,self.org)
 def tearDown(self):self.tmp.cleanup()
 def make(self,kind='plan',lang='ru'):
  return documents.create(app,dict(kind=kind,language=lang,student_id=self.sid,academic_year=self.year,plan_id=self.plan['id'],basis='Для рассмотрения кафедрой'))
 def test_all_document_types_and_languages(self):
  for kind in documents.KINDS:
   for lang in ('ru','kk'):
    result=self.make(kind,lang);snap=documents.snapshot(app,result['id']);output=documents.html_document(snap).decode()
    self.assertIn(documents.WORDS[lang]['draft'],output)
    self.assertIn(self.org['university_'+lang],output)
  self.assertEqual(len(documents.listing(app)),6)
 def test_unconfigured_department_rejected(self):
  documents.save_settings(app,{**self.org,'department_kk':''})
  with self.assertRaises(app.ValidationError): self.make(lang='kk')
 def test_snapshot_survives_profile_and_setting_changes(self):
  result=self.make();before=documents.html_document(documents.snapshot(app,result['id']))
  student=app.get_record('students',self.sid);app.save('students',{**student,'name':'Новое имя пользователя'},self.sid)
  documents.save_settings(app,{**self.org,'university_ru':'Иное название организации'})
  after=documents.html_document(documents.snapshot(app,result['id']))
  self.assertEqual(before,after)
 def test_wrong_plan_student_is_rejected(self):
  other=app.records('students')[1]['id']
  with self.assertRaises(app.ValidationError):documents.create(app,dict(kind='plan',language='ru',student_id=other,academic_year=self.year,plan_id=self.plan['id']))
 def test_document_escaping_and_word_tables(self):
  st=app.get_record('students',self.sid);app.save('students',{**st,'name':'<script>alert(1)</script>'},self.sid)
  result=self.make();snap=documents.snapshot(app,result['id'])
  self.assertNotIn('<script>alert(1)</script>',documents.html_document(snap).decode())
  import xml.etree.ElementTree as ET
  with zipfile.ZipFile(io.BytesIO(documents.docx_document(snap))) as z:
   xml=z.read('word/document.xml');ET.fromstring(xml)
   self.assertIn(b'w:tblHeader',xml);self.assertIn(b'Times New Roman',xml)
 def test_no_data_sent_without_consent(self):
  with patch.dict(os.environ,{'OPENAI_API_KEY':'test','OPENAI_MODEL':'test-model'}),patch.object(ai,'provider_answer') as provider:
   with self.assertRaises(app.ValidationError):ai.chat(app,{'mode':'cloud','message':'Проверь сроки','academic_year':self.year},'session')
   provider.assert_not_called()
 def test_no_data_sent_when_not_configured(self):
  with patch.dict(os.environ,{},clear=True),patch.object(ai,'provider_answer') as provider:
   with self.assertRaises(app.ValidationError):ai.chat(app,{'mode':'cloud','message':'Проверь сроки','academic_year':self.year,'consent':True},'session')
   provider.assert_not_called()
 def test_context_omits_names_email_and_titles(self):
  ctx=ai.context(app,self.sid,self.year);raw=json.dumps(ctx,ensure_ascii=False)
  for st in app.records('students'):self.assertNotIn(st['name'],raw)
  for topic in app.records('topics'):self.assertNotIn(topic['title'],raw)
  self.assertNotIn(self.sid,raw)
 def test_advice_does_not_mutate_records(self):
  before={k:app.records(k) for k in app.TABLES}
  result=ai.chat(app,dict(mode='local',message='Проверь работу кафедры',student_id=self.sid,academic_year=self.year),'session')
  self.assertEqual(before,{k:app.records(k) for k in app.TABLES});self.assertIn('по правилам',result['answer'])
 def test_proposal_requires_approval_and_is_idempotent(self):
  n=len(app.records('plans'))
  result=ai.chat(app,dict(mode='local',message='Сформируй индивидуальный план',student_id=self.sid,academic_year=self.year,suggest_plan=True),'owner')
  ident=result['proposal']['id'];self.assertEqual(len(app.records('plans')),n)
  with self.assertRaises(app.ValidationError):ai.approve(app,ident,'different-session')
  first=ai.approve(app,ident,'owner');second=ai.approve(app,ident,'owner')
  self.assertEqual(first,second);self.assertEqual(len(app.records('plans')),n+1)
 def test_expired_proposal_rejected(self):
  result=ai.chat(app,dict(mode='local',message='Сформируй план',student_id=self.sid,academic_year=self.year,suggest_plan=True),'s')
  ident=result['proposal']['id']
  with app.db('audit') as con:con.execute('UPDATE assistant_proposals SET expires=0 WHERE id=?',(ident,))
  with self.assertRaises(app.ValidationError):ai.approve(app,ident,'s')
 def test_cloud_mock_response_and_proposal(self):
  with patch.dict(os.environ,{'OPENAI_API_KEY':'test','OPENAI_MODEL':'test-model'}),patch.object(ai,'provider_answer',return_value={'answer':'Проверено по контексту. Предлагаю план.','action':'create_plan'}) as provider:
   result=ai.chat(app,dict(mode='cloud',message='Создай план',student_id=self.sid,academic_year=self.year,consent=True),'session')
   self.assertEqual(result['mode'],'cloud');self.assertIsNotNone(result['proposal']);provider.assert_called_once()
 def test_provider_request_shape_and_output_parsing(self):
  output={'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'answer':'Готово','action':'none'})}]}]}
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,*args):return json.dumps(output).encode()
  with patch.dict(os.environ,{'OPENAI_API_KEY':'test','OPENAI_MODEL':'test-model'}),patch('urllib.request.urlopen',return_value=Response()) as call:
   r=ai.provider_answer('Проверь сроки',{'exam_count':1});self.assertEqual(r['action'],'none')
   request=call.call_args.args[0];body=json.loads(request.data);self.assertFalse(body['store']);self.assertEqual(body['text']['format']['type'],'json_schema')
 def test_interrupted_proposal_can_resume_without_duplicate(self):
  result=ai.chat(app,dict(mode='local',message='Создай план',student_id=self.sid,academic_year=self.year,suggest_plan=True),'session')
  ident=result['proposal']['id']
  created=app.create_plan({'student_id':self.sid,'academic_year':self.year},request_id=ident)
  count=len(app.records('plans'))
  resumed=ai.approve(app,ident,'session')
  self.assertEqual(created['id'],resumed['plan_id']);self.assertEqual(len(app.records('plans')),count)
 def test_new_tables_keep_seven_databases(self):
  self.assertEqual(len(list(app.DATA.glob('*.sqlite3'))),7)

if __name__=='__main__':unittest.main(verbosity=2)
