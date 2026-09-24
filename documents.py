"""Настраиваемые документы вуза: реестр, неизменяемый снимок, HTML и DOCX."""
import html
import io
import json
import uuid
import zipfile
from datetime import date
from xml.sax.saxutils import escape

DEFAULTS = {
 'university_ru':'Международный Университет Астаны', 'university_kk':'Астана Халықаралық университеті', 'department_ru':'', 'department_kk':'',
 'city':'Астана', 'head_name':'', 'head_position':'Заведующий кафедрой', 'head_position_kk':'Кафедра меңгерушісі',
 'address':'', 'phone':'', 'email':'', 'document_prefix':'KAF',
}
KINDS = {'plan':'Индивидуальный план', 'attestation':'Лист аттестации', 'report':'Отчёт о научной работе'}
WORDS = {
 'ru': {'country':'РЕСПУБЛИКА КАЗАХСТАН','draft':'ПРОЕКТ ДОКУМЕНТА','plan':'Индивидуальный план магистранта','attestation':'Лист ежегодной аттестации','report':'Отчёт о научной работе магистранта','student':'Магистрант','program':'Образовательная программа','supervisor':'Научный руководитель','year':'Учебный год','topic':'Тема диссертации','number':'Внутренний номер проекта','date':'Дата формирования','work':'Наименование работы','due':'Срок','status':'Выполнение','sign':'Место для собственноручной подписи','head':'Согласование кафедры','notice':'Проект для проверки и утверждения уполномоченными лицами. Не содержит подписей, печати или ЭЦП. Внутренний номер не является государственной регистрацией.','basis':'Основание и примечание','result':'Результат','exam':'Дисциплина','grade':'Балл','publication':'Публикация','journal':'Издание','no_data':'Нет зарегистрированных данных','att_date':'Срок аттестации','generated':'Подготовлено в информационной системе кафедры'},
 'kk': {'country':'ҚАЗАҚСТАН РЕСПУБЛИКАСЫ','draft':'ҚҰЖАТ ЖОБАСЫ','plan':'Магистранттың жеке жоспары','attestation':'Жыл сайынғы аттестаттау парағы','report':'Магистранттың ғылыми жұмысы туралы есеп','student':'Магистрант','program':'Білім беру бағдарламасы','supervisor':'Ғылыми жетекші','year':'Оқу жылы','topic':'Диссертация тақырыбы','number':'Жобаның ішкі нөмірі','date':'Қалыптастыру күні','work':'Жұмыс атауы','due':'Мерзімі','status':'Орындалуы','sign':'Қол қоюға арналған орын','head':'Кафедрамен келісу','notice':'Уәкілетті тұлғалардың тексеруі мен бекітуіне арналған жоба. Қолтаңба, мөр және ЭЦҚ жоқ. Ішкі нөмір мемлекеттік тіркеу болып табылмайды.','basis':'Негіздеме және ескерту','result':'Нәтиже','exam':'Пән','grade':'Балл','publication':'Жарияланым','journal':'Басылым','no_data':'Тіркелген деректер жоқ','att_date':'Аттестаттау мерзімі','generated':'Кафедраның ақпараттық жүйесінде дайындалған'},
}
STATUS_KK={'Запланировано':'Жоспарланған','В работе':'Орындалуда','Выполнено':'Орындалды','Черновик':'Жоба','На согласовании':'Келісуде','Утверждён':'Бекітілген','Завершён':'Аяқталған','Сдан':'Тапсырылды','Не сдан':'Тапсырылмады','Запланирован':'Жоспарланған','Запланирована':'Жоспарланған','Пройдена':'Өтті','Не пройдена':'Өтпеді'}

def init(core):
 with core.db('audit') as con:
  con.execute('CREATE TABLE IF NOT EXISTS organization_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
  con.execute('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, number TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, student_id TEXT NOT NULL, created_at TEXT NOT NULL, language TEXT NOT NULL, snapshot TEXT NOT NULL)')

def settings(core):
 with core.db('audit') as con:
  row=con.execute('SELECT payload FROM organization_settings WHERE id=1').fetchone()
 return {**DEFAULTS,**(json.loads(row[0]) if row else {})}

def save_settings(core,data):
 clean={}
 for key,default in DEFAULTS.items():
  val=str(data.get(key,default)).strip()
  if len(val)>300: raise core.ValidationError('Реквизит слишком длинный: '+key)
  clean[key]=val
 if not clean['document_prefix'] or not all(c.isalnum() or c in '-_' for c in clean['document_prefix']) or len(clean['document_prefix'])>12:
  raise core.ValidationError('Префикс: от 1 до 12 букв, цифр, дефисов или подчёркиваний')
 with core.LOCK,core.db('audit') as con:
  con.execute('INSERT INTO organization_settings VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(json.dumps(clean,ensure_ascii=False),))
 core.audit('documents','settings','organization')
 return clean

def listing(core):
 with core.db('audit') as con:
  return [dict(r) for r in con.execute('SELECT id,number,kind,student_id,created_at,language FROM documents ORDER BY rowid DESC')]

def create(core,data):
 kind=data.get('kind'); lang=data.get('language','ru')
 if kind not in KINDS or lang not in WORDS: raise core.ValidationError('Выберите вид и язык документа')
 with core.LOCK:
  org=settings(core)
  for key in ('university_'+lang,'department_'+lang,'city'):
   if not org[key]: raise core.ValidationError('Заполните название вуза, кафедры на выбранном языке и город в настройках')
  sid=core.student_exists(data.get('student_id')); student=core.get_record('students',sid)
  year=core.academic_year(data.get('academic_year'))
  note=core.required(data.get('basis','Подготовлено для рассмотрения на кафедре'),'Основание',1,1000)
  topic=next((r for r in core.records('topics') if r['student_id']==sid),None)
  snap={'organization':org,'student':student,'academic_year':year,'topic':topic,'basis':note,'kind':kind,'language':lang}
  if kind=='plan':
   plan=core.get_record('plans',data.get('plan_id'))
   if not plan or plan['student_id']!=sid or plan['academic_year']!=year: raise core.ValidationError('Выберите существующую версию плана этого магистранта и учебного года')
   snap['plan']=plan
  elif kind=='attestation':
   a=next((r for r in core.records('attestations') if r['student_id']==sid and r['academic_year']==year),None)
   if not a: raise core.ValidationError('Сначала зарегистрируйте аттестацию на выбранный учебный год')
   snap['attestation']=a
  else:
   start=year[:4]+'-09-01'; end=year[-4:]+'-08-31'
   snap['exams']=[r for r in core.records('exams') if r['student_id']==sid and r['exam_date'] and start<=r['exam_date']<=end]
   snap['publications']=[r for r in core.records('publications') if sid in r['student_ids'] and int(year[:4])<=r['year']<=int(year[-4:])]
  ident=str(uuid.uuid4()); stamp=core.now()
  with core.db('audit') as con:
   seq=con.execute('SELECT COUNT(*)+1 FROM documents').fetchone()[0]
   number=f'{org["document_prefix"]}-{date.today().year}-{seq:05d}'
   snap.update(number=number,created_at=stamp)
   con.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?)',(ident,number,kind,sid,stamp,lang,json.dumps(snap,ensure_ascii=False)))
  core.audit('documents','create',ident)
  return {'id':ident,'number':number,'kind':kind,'language':lang}

def snapshot(core,ident):
 with core.db('audit') as con: row=con.execute('SELECT snapshot FROM documents WHERE id=?',(ident,)).fetchone()
 if not row: raise core.ValidationError('Документ не найден')
 return json.loads(row[0])

def blocks(s):
 w=WORDS[s['language']]; org=s['organization']; st=s['student']; lang=s['language']
 translate=lambda t: STATUS_KK.get(t,t) if lang=='kk' else t
 result=[('center',w['country']),('center',org['university_'+lang]),('center',org['department_'+lang]),('small', ' · '.join(x for x in (org['address'],org['phone'],org['email']) if x)),('draft',w['draft']),('title',w[s['kind']]),('text',f'{w["number"]}: {s["number"]}'),('text',f'{w["date"]}: {s["created_at"][:10]}'),('text',org['city']),('text',f'{w["student"]}: {st["name"]}'),('text',f'{w["program"]}: {st["program"]}'),('text',f'{w["supervisor"]}: {st["supervisor"]}'),('text',f'{w["year"]}: {s["academic_year"]}')]
 if s['topic']: result.append(('text',f'{w["topic"]}: {s["topic"]["title"]}'))
 if s['kind']=='plan':
  p=s['plan']; result.append(('text',f'v{p["version"]} · {translate(p["status"])}'))
  result.append(('table',[["№",w['work'],w['due'],w['status']]]+[[str(i),r['activity'],r['due_date'] or '—',translate(r['status'])] for i,r in enumerate(p['items'],1)]))
 elif s['kind']=='attestation':
  a=s['attestation'];result.extend([('text',f'{w["att_date"]}: {a["due_date"]}'),('text',f'{w["status"]}: {translate(a["status"])}'),('text',f'{w["result"]}: {a["result"] or "____________________________"}')])
 else:
  result.append(('table',[[w['exam'],w['date'],w['grade'],w['status']]]+[[r['subject'],r['exam_date'],str(r['grade'] if r['grade'] is not None else '—'),translate(r['status'])] for r in s['exams']]))
  result.append(('table',[[w['publication'],w['journal'],w['year']]]+[[r['title'],r['journal'],str(r['year'])] for r in s['publications']]))
  result.append(('small','Публикации отобраны по календарным годам; соответствие учебному периоду проверяется перед подписанием.' if lang=='ru' else 'Жарияланымдар күнтізбелік жылдар бойынша іріктелді; оқу кезеңіне сәйкестігін қол қою алдында тексеру қажет.'))
 result.extend([('text',w['basis']+': '+s['basis']),('text',w['head']),('text',f"{org['head_position_kk'] if lang=='kk' else org['head_position']}: ____________________ {org['head_name']}"),('text',f'{w["supervisor"]}: ____________________ {st["supervisor"]}'),('text',f'{w["student"]}: ____________________ {st["name"]}'),('small',w['sign']),('small',w['notice'])])
 return result

def html_document(s):
 parts=[]
 for kind,value in blocks(s):
  if kind=='table':
   parts.append('<table>'+('<colgroup><col class="number-col"><col class="work-col"><col class="date-col"><col class="status-col"></colgroup>' if value[0][0]=='№' else '')+'<thead><tr>'+''.join('<th>'+html.escape(c)+'</th>' for c in value[0])+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in row)+'</tr>' for row in value[1:])+'</tbody></table>')
  else: parts.append(f'<p class="{kind}">'+html.escape(value)+'</p>')
 return ('<!doctype html><html lang="'+s['language']+'"><head><meta charset="utf-8"><title>'+html.escape(s['number'])+'</title><link rel="stylesheet" href="/document.css"><script src="/document.js" defer></script></head><body><nav><button id="print-document">Печать / сохранить PDF</button><span>Проект для проверки и подписания</span></nav><article>'+''.join(parts)+'</article></body></html>').encode()

def docx_document(s):
 def p(text,kind='text'):
  align='center' if kind in ('center','title','draft') else 'left'
  size='20' if kind=='small' else '28'
  bold='<w:b/>' if kind in ('title','draft','center') else ''
  keep='<w:keepNext/>' if kind in ('title','center','draft') else ''
  return f'<w:p><w:pPr>{keep}<w:jc w:val="{align}"/><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>{bold}<w:sz w:val="{size}"/></w:rPr><w:t xml:space="preserve">'+escape(str(text))+'</w:t></w:r></w:p>'
 body=''
 for kind,value in blocks(s):
  if kind!='table':body+=p(value,kind);continue
  widths=[450,4350,1800,2300] if len(value[0])==4 and value[0][0]=='№' else ([3400,2400,1000,2100] if len(value[0])==4 else [4200,3000,1700])
  borders=''.join(f'<w:{k} w:val="single" w:sz="4" w:color="808080"/>' for k in ('top','left','bottom','right','insideH','insideV'))
  body+='<w:tbl><w:tblPr><w:tblW w:w="8900" w:type="dxa"/><w:tblBorders>'+borders+'</w:tblBorders><w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:left w:w="100" w:type="dxa"/><w:bottom w:w="80" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>'+''.join(f'<w:gridCol w:w="{v}"/>' for v in widths)+'</w:tblGrid>'
  for i,row in enumerate(value):
   body+='<w:tr><w:trPr><w:cantSplit/>'+('<w:tblHeader/>' if i==0 else '')+'</w:trPr>'
   for j,cell in enumerate(row):body+=f'<w:tc><w:tcPr><w:tcW w:w="{widths[j]}" w:type="dxa"/>'+('<w:shd w:fill="EEEEEE"/>' if i==0 else '')+'</w:tcPr>'+p(cell,'small')+'</w:tc>'
   body+='</w:tr>'
  body+='</w:tbl>'+p('')
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  z.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>')
  z.writestr('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
  z.writestr('word/_rels/document.xml.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/></Relationships>')
  z.writestr('word/footer1.xml','<?xml version="1.0"?><w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p></w:ftr>')
  z.writestr('word/document.xml','<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'+body+'<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1701"/></w:sectPr></w:body></w:document>')
 return out.getvalue()
