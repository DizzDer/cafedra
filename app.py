"""АРМ Кафедра — локальный учебный проект. Python 3.10+, без зависимостей."""
import argparse
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('ARM_DATA_DIR', ROOT / 'data'))
LOCK = threading.RLock()
TOKEN = secrets.token_urlsafe(32)
SESSIONS = set()
TABLES = {
 'students': ('StudentsDb', 'Магистранты', {
  'name': 'TEXT NOT NULL', 'program': 'TEXT NOT NULL', 'year': 'INTEGER NOT NULL',
  'supervisor': 'TEXT NOT NULL', 'email': 'TEXT NOT NULL', 'status': 'TEXT NOT NULL'}),
 'exams': ('ExamsDb', 'Экзамены', {
  'student_id': 'TEXT NOT NULL', 'subject': 'TEXT NOT NULL', 'semester': 'INTEGER NOT NULL',
  'attempt': 'INTEGER NOT NULL', 'exam_date': 'TEXT', 'grade': 'INTEGER', 'status': 'TEXT NOT NULL'}),
 'topics': ('TopicsDb', 'Диссертации', {
  'student_id': 'TEXT NOT NULL', 'title': 'TEXT NOT NULL', 'status': 'TEXT NOT NULL', 'approved_on': 'TEXT'}),
 'publications': ('PublicationsDb', 'Публикации', {
  'title': 'TEXT NOT NULL', 'journal': 'TEXT NOT NULL', 'year': 'INTEGER NOT NULL',
  'doi': 'TEXT', 'vak': 'INTEGER NOT NULL DEFAULT 0', 'scopus': 'INTEGER NOT NULL DEFAULT 0',
  'verified': 'INTEGER NOT NULL DEFAULT 0'}),
 'attestations': ('AttestationDb', 'Аттестации', {
  'student_id': 'TEXT NOT NULL', 'academic_year': 'TEXT NOT NULL', 'due_date': 'TEXT NOT NULL',
  'status': 'TEXT NOT NULL', 'result': 'TEXT'}),
 'plans': ('PlansDb', 'Индивидуальные планы', {
  'student_id': 'TEXT NOT NULL', 'academic_year': 'TEXT NOT NULL', 'version': 'INTEGER NOT NULL',
  'status': 'TEXT NOT NULL', 'created_at': 'TEXT NOT NULL'}),
}
STATUSES = {
 'students': ['Обучается', 'Выпущен', 'Академический отпуск'],
 'exams': ['Запланирован', 'Сдан', 'Не сдан'],
 'topics': ['Черновик', 'На согласовании', 'Утверждена'],
 'attestations': ['Запланирована', 'Пройдена', 'Не пройдена'],
 'plans': ['Черновик', 'На согласовании', 'Утверждён', 'Завершён'],
}


class ValidationError(Exception):
    pass


def now():
    return datetime.now().isoformat(timespec='seconds')


@contextmanager
def db(module):
    name = TABLES[module][0] if module in TABLES else 'AuditDb'
    con = sqlite3.connect(DATA / (name + '.sqlite3'), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        with con:
            yield con
    finally:
        con.close()


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    for module, (_, _, fields) in TABLES.items():
        columns = ', '.join(f'{k} {v}' for k, v in fields.items())
        with db(module) as con:
            con.execute(f'CREATE TABLE IF NOT EXISTS {module} (id TEXT PRIMARY KEY, {columns})')
    with db('exams') as con:
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS exam_attempt ON exams(student_id,subject,semester,attempt)')
    with db('topics') as con:
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_topic ON topics(student_id)')
        con.execute('CREATE TABLE IF NOT EXISTS topic_history (id TEXT PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE, old_title TEXT, new_title TEXT, changed_at TEXT)')
    with db('publications') as con:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS unique_doi ON publications(doi) WHERE doi IS NOT NULL AND doi <> ''")
        con.execute('CREATE TABLE IF NOT EXISTS authors (publication_id TEXT REFERENCES publications(id) ON DELETE CASCADE, student_id TEXT NOT NULL, PRIMARY KEY(publication_id,student_id))')
    with db('attestations') as con:
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS yearly_attestation ON attestations(student_id,academic_year)')
        con.execute('CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, attestation_id TEXT REFERENCES attestations(id) ON DELETE CASCADE, threshold INTEGER NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0, UNIQUE(attestation_id,threshold))')
    with db('plans') as con:
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS plan_version ON plans(student_id,academic_year,version)')
        con.execute('CREATE TABLE IF NOT EXISTS plan_items (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE, activity TEXT NOT NULL, due_date TEXT, status TEXT NOT NULL, source_type TEXT, source_id TEXT)')
    with db('audit') as con:
        con.execute('CREATE TABLE IF NOT EXISTS audit_events (id TEXT PRIMARY KEY, module TEXT, action TEXT, record_id TEXT, created_at TEXT)')


def audit(module, action, record_id):
    # Отказ журнала не должен превращать успешную запись в ошибку для пользователя.
    try:
        with db('audit') as con:
            con.execute('INSERT INTO audit_events VALUES (?,?,?,?,?)', (str(uuid.uuid4()), module, action, record_id, now()))
    except sqlite3.Error as exc:
        print('Audit log unavailable:', type(exc).__name__)


def records(module):
    if module not in TABLES:
        raise ValidationError('Неизвестный раздел')
    with db(module) as con:
        rows = [dict(r) for r in con.execute(f'SELECT * FROM {module} ORDER BY rowid DESC')]
        for row in rows:
            if module == 'publications':
                row['student_ids'] = [r[0] for r in con.execute('SELECT student_id FROM authors WHERE publication_id=?', (row['id'],))]
            if module == 'plans':
                row['items'] = [dict(r) for r in con.execute('SELECT * FROM plan_items WHERE plan_id=? ORDER BY rowid', (row['id'],))]
            if module == 'topics':
                row['history'] = [dict(r) for r in con.execute('SELECT * FROM topic_history WHERE topic_id=? ORDER BY rowid DESC', (row['id'],))]
    return rows


def get_record(module, ident):
    return next((r for r in records(module) if r['id'] == ident), None)


def required(value, label, minimum=1, maximum=300):
    value = str(value or '').strip()
    if not minimum <= len(value) <= maximum:
        raise ValidationError(f'{label}: требуется от {minimum} до {maximum} символов')
    return value


def number(value, label, low, high):
    try:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError()
        n = int(value)
    except (ValueError, TypeError):
        raise ValidationError(f'{label}: введите целое число')
    if not low <= n <= high:
        raise ValidationError(f'{label}: допустимо от {low} до {high}')
    return n


def valid_date(value, label, optional=False):
    if not value and optional:
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (ValueError, TypeError):
        raise ValidationError(f'{label}: укажите корректную дату')


def student_exists(ident):
    if not get_record('students', ident):
        raise ValidationError('Магистрант не найден')
    return ident


def academic_year(value):
    value = required(value, 'Учебный год', 9, 9)
    try:
        a, b = value.split('/')
        assert len(a) == 4 and len(b) == 4 and int(b) == int(a) + 1
    except (ValueError, AssertionError):
        raise ValidationError('Учебный год должен иметь вид 2026/2027')
    return value


def validate(module, data):
    r = {}
    if module in STATUSES:
        if data.get('status') not in STATUSES[module]:
            raise ValidationError('Выберите допустимый статус')
        r['status'] = data['status']
    if module in ('exams', 'topics', 'attestations'):
        r['student_id'] = student_exists(data.get('student_id'))
    if module == 'students':
        for key, title in [('name', 'ФИО'), ('program', 'Программа'), ('supervisor', 'Научный руководитель')]:
            r[key] = required(data.get(key), title, 2, 200)
        r['year'] = number(data.get('year'), 'Год поступления', 2000, date.today().year + 1)
        r['email'] = str(data.get('email', '')).strip()
        if r['email'] and ('@' not in r['email'] or len(r['email']) > 254):
            raise ValidationError('Некорректный адрес электронной почты')
    elif module == 'exams':
        r['subject'] = required(data.get('subject'), 'Дисциплина', 2, 150)
        r['semester'] = number(data.get('semester'), 'Семестр', 1, 8)
        r['attempt'] = number(data.get('attempt'), 'Попытка', 1, 20)
        r['exam_date'] = valid_date(data.get('exam_date'), 'Дата экзамена', r['status'] == 'Запланирован')
        r['grade'] = None if data.get('grade') in (None, '') else number(data['grade'], 'Балл', 0, 100)
        if r['status'] != 'Запланирован' and (r['grade'] is None or r['exam_date'] > date.today().isoformat()):
            raise ValidationError('Для результата укажите балл и дату не позднее сегодняшней')
    elif module == 'topics':
        r['title'] = required(data.get('title'), 'Тема диссертации', 10, 250)
        r['approved_on'] = valid_date(data.get('approved_on'), 'Дата утверждения', r['status'] != 'Утверждена')
        if r['status'] != 'Утверждена':
            r['approved_on'] = None
        elif r['approved_on'] > date.today().isoformat():
            raise ValidationError('Дата утверждения не может быть в будущем')
    elif module == 'publications':
        r['title'] = required(data.get('title'), 'Название статьи', 3, 300)
        r['journal'] = required(data.get('journal'), 'Журнал', 2, 200)
        r['year'] = number(data.get('year'), 'Год публикации', 1900, date.today().year)
        r['doi'] = str(data.get('doi') or '').strip().lower().removeprefix('https://doi.org/').removeprefix('http://doi.org/') or None
        if r['doi'] and (not r['doi'].startswith('10.') or '/' not in r['doi'] or ' ' in r['doi'] or len(r['doi']) > 250):
            raise ValidationError('DOI должен иметь вид 10.xxxx/xxxx')
        for key in ('vak', 'scopus', 'verified'):
            r[key] = int(bool(data.get(key)))
        authors = data.get('student_ids')
        if not isinstance(authors, list) or not authors or len(authors) > 100:
            raise ValidationError('Выберите хотя бы одного автора')
        for ident in authors:
            student_exists(ident)
    elif module == 'attestations':
        r['academic_year'] = academic_year(data.get('academic_year'))
        r['due_date'] = valid_date(data.get('due_date'), 'Срок аттестации')
        r['result'] = str(data.get('result') or '').strip()[:2000]
        if r['status'] != 'Запланирована' and not r['result']:
            raise ValidationError('Укажите результат аттестации')
    return r


def save(module, data, ident=None):
    if module not in TABLES or module == 'plans':
        raise ValidationError('Недопустимая операция')
    with LOCK:
        old = get_record(module, ident) if ident else None
        if ident and not old:
            raise ValidationError('Запись не найдена')
        r = validate(module, data)
        ident = ident or str(uuid.uuid4())
        try:
            with db(module) as con:
                if old:
                    con.execute(f'UPDATE {module} SET ' + ','.join(f'{k}=?' for k in r) + ' WHERE id=?', (*r.values(), ident))
                else:
                    con.execute(f'INSERT INTO {module} (id,{",".join(r)}) VALUES ({",".join("?" for _ in range(len(r)+1))})', (ident, *r.values()))
                if module == 'publications':
                    con.execute('DELETE FROM authors WHERE publication_id=?', (ident,))
                    con.executemany('INSERT INTO authors VALUES (?,?)', [(ident, sid) for sid in set(data['student_ids'])])
                if module == 'topics' and old and old['title'] != r['title']:
                    con.execute('INSERT INTO topic_history VALUES (?,?,?,?,?)', (str(uuid.uuid4()), ident, old['title'], r['title'], now()))
                if module == 'attestations' and old and (old['due_date'] != r['due_date'] or r['status'] != 'Запланирована'):
                    con.execute('DELETE FROM notifications WHERE attestation_id=?', (ident,))
        except sqlite3.IntegrityError:
            raise ValidationError('Такая запись уже существует: проверьте DOI, тему, учебный год или попытку экзамена')
        audit(module, 'update' if old else 'create', ident)
        return get_record(module, ident)


def delete(module, ident):
    if module not in TABLES:
        raise ValidationError('Неизвестный раздел')
    with LOCK:
        row = get_record(module, ident)
        if not row:
            raise ValidationError('Запись не найдена')
        if module == 'students':
            for other in ('exams', 'topics', 'attestations', 'plans'):
                if any(r['student_id'] == ident for r in records(other)):
                    raise ValidationError('У магистранта есть связанные записи. Сначала удалите их или измените статус магистранта')
            if any(ident in r['student_ids'] for r in records('publications')):
                raise ValidationError('Магистрант указан автором публикации')
        if module == 'plans' and row['status'] in ('Утверждён', 'Завершён'):
            raise ValidationError('Утверждённую версию плана удалять нельзя')
        with db(module) as con:
            con.execute(f'DELETE FROM {module} WHERE id=?', (ident,))
        audit(module, 'delete', ident)


def reminders(today=None):
    today = today or date.today()
    with LOCK, db('attestations') as con:
        for row in con.execute("SELECT * FROM attestations WHERE status='Запланирована'").fetchall():
            days = (date.fromisoformat(row['due_date']) - today).days
            threshold = -1 if days < 0 else 1 if days <= 1 else 7 if days <= 7 else 30 if days <= 30 else None
            if threshold is None:
                continue
            student = get_record('students', row['student_id'])
            name = student['name'] if student else 'Магистрант'
            message = f'{name}: аттестация {row["due_date"]}. ' + ('Срок истёк.' if days < 0 else f'Осталось дней: {days}.')
            con.execute('INSERT OR IGNORE INTO notifications VALUES (?,?,?,?,?,0)', (str(uuid.uuid4()), row['id'], threshold, message, now()))


def create_plan(data):
    with LOCK:
        sid = student_exists(data.get('student_id'))
        year = academic_year(data.get('academic_year'))
        start = date(int(year[:4]), 9, 1)
        end = date(int(year[-4:]), 8, 31)
        items = []
        topic = next((r for r in records('topics') if r['student_id'] == sid), None)
        if not topic:
            raise ValidationError('Сначала зарегистрируйте тему диссертации магистранта')
        for title, due in [('Обзор литературы и постановка задач', date(start.year, 12, 20)), ('Проведение исследования и подготовка диссертации', date(end.year, 5, 31))]:
            items.append((f'{title}: {topic["title"]}', due.isoformat(), 'Запланировано', 'topics', topic['id']))
        for row in records('exams'):
            if row['student_id'] == sid and row['exam_date'] and start.isoformat() <= row['exam_date'] <= end.isoformat():
                items.append((f'Дисциплина: {row["subject"]}', row['exam_date'], 'Выполнено' if row['status'] == 'Сдан' else 'Запланировано', 'exams', row['id']))
        items.append(('Подготовка научной публикации', date(end.year, 5, 1).isoformat(), 'Запланировано', None, None))
        for row in records('publications'):
            if sid in row['student_ids'] and int(year[:4]) <= row['year'] <= int(year[-4:]):
                items.append((f'Публикация (год {row["year"]}): {row["title"]}', None, 'Выполнено', 'publications', row['id']))
        for row in records('attestations'):
            if row['student_id'] == sid and row['academic_year'] == year:
                items.append(('Ежегодная аттестация', row['due_date'], 'Выполнено' if row['status'] == 'Пройдена' else 'Запланировано', 'attestations', row['id']))
        ident = str(uuid.uuid4())
        with db('plans') as con:
            version = con.execute('SELECT COALESCE(MAX(version),0)+1 FROM plans WHERE student_id=? AND academic_year=?', (sid, year)).fetchone()[0]
            con.execute('INSERT INTO plans VALUES (?,?,?,?,?,?)', (ident, sid, year, version, 'Черновик', now()))
            con.executemany('INSERT INTO plan_items VALUES (?,?,?,?,?,?,?)', [(str(uuid.uuid4()), ident, *i) for i in items])
        audit('plans', 'generate', ident)
        return get_record('plans', ident)


def update_plan(ident, data):
    with LOCK:
        old = get_record('plans', ident)
        if not old:
            raise ValidationError('План не найден')
        transitions = {'Черновик': ['Черновик', 'На согласовании'], 'На согласовании': ['На согласовании', 'Черновик', 'Утверждён'], 'Утверждён': ['Утверждён', 'Завершён'], 'Завершён': ['Завершён']}
        status = data.get('status')
        if status not in transitions[old['status']]:
            raise ValidationError('Недопустимый переход статуса плана')
        edited = data.get('items', old['items'])
        if not isinstance(edited, list) or {x.get('id') for x in edited} != {x['id'] for x in old['items']} or len(edited) != len(old['items']):
            raise ValidationError('Состав строк плана изменился. Обновите страницу')
        normalized = []
        for item in edited:
            title = required(item.get('activity'), 'Работа', 3, 600)
            due = valid_date(item.get('due_date'), 'Срок работы', True)
            state = item.get('status')
            if state not in ['Запланировано', 'В работе', 'Выполнено']:
                raise ValidationError('Некорректный статус работы')
            normalized.append((title, due, state, item['id']))
        if old['status'] in ('Утверждён', 'Завершён'):
            previous = {x['id']: (x['activity'], x['due_date'], x['status']) for x in old['items']}
            if any(previous[x[3]] != x[:3] for x in normalized):
                raise ValidationError('Утверждённая версия неизменна. Сформируйте новую версию')
        with db('plans') as con:
            con.execute('UPDATE plans SET status=? WHERE id=?', (status, ident))
            con.executemany('UPDATE plan_items SET activity=?,due_date=?,status=? WHERE id=?', normalized)
        audit('plans', 'update', ident)
        return get_record('plans', ident)


def export_docx(ident):
    plan = get_record('plans', ident)
    if not plan:
        raise ValidationError('План не найден')
    student = get_record('students', plan['student_id'])
    lines = ['Индивидуальный план магистранта', student['name'], 'Программа: ' + student['program'], 'Научный руководитель: ' + student['supervisor'], f'Учебный год: {plan["academic_year"]} | Версия: {plan["version"]} | {plan["status"]}']
    lines += [f'{i}. {r["activity"]}\nСрок: {r["due_date"] or "не указан"}. Статус: {r["status"]}.' for i, r in enumerate(plan['items'], 1)]
    lines += ['Магистрант __________________', 'Научный руководитель __________________']
    paragraphs = ''.join('<w:p><w:pPr><w:spacing w:after="180"/></w:pPr><w:r><w:t xml:space="preserve">' + escape(line).replace('\n', '</w:t><w:br/><w:t xml:space="preserve">') + '</w:t></w:r></w:p>' for line in lines)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraphs + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr></w:body></w:document>')
    return out.getvalue()


def snapshot():
    with LOCK:
        reminders()
        result = {m: records(m) for m in TABLES}
        with db('attestations') as con:
            result['notifications'] = [dict(r) for r in con.execute('SELECT * FROM notifications ORDER BY created_at DESC')]
        with db('audit') as con:
            result['audit'] = [dict(r) for r in con.execute('SELECT * FROM audit_events ORDER BY rowid DESC LIMIT 100')]
        result['databases'] = [{'name': name + '.sqlite3', 'module': title, 'bytes': (DATA / (name + '.sqlite3')).stat().st_size} for name, title, _ in TABLES.values()]
        result['databases'].append({'name': 'AuditDb.sqlite3', 'module': 'Журнал действий', 'bytes': (DATA / 'AuditDb.sqlite3').stat().st_size})
        result['csrf'] = TOKEN
        result['today'] = date.today().isoformat()
        return result


def demo():
    if records('students'):
        raise ValidationError('Демонстрационные данные можно загрузить только в пустую систему')
    today = date.today()
    start = today.year if today.month >= 9 else today.year - 1
    year = f'{start}/{start+1}'
    for i, (name, program, supervisor) in enumerate([
        ('Алина Серикова', 'Информационные системы', 'Ахметов М. К.'),
        ('Данияр Ибраев', 'Программная инженерия', 'Смагулова А. Т.'),
        ('Мария Волкова', 'Информационные системы', 'Ахметов М. К.')]):
        student = save('students', dict(name=name, program=program, supervisor=supervisor, email='', year=start, status='Обучается'))
        sid = student['id']
        save('topics', dict(student_id=sid, title=['Система мониторинга научной деятельности кафедры', 'Применение машинного обучения для анализа данных', 'Проектирование защищённых распределённых систем'][i], status='Утверждена', approved_on=today.isoformat()))
        save('exams', dict(student_id=sid, subject='Методология научных исследований', semester=1, attempt=1, grade=85+i*5, exam_date=today.isoformat(), status='Сдан'))
        save('attestations', dict(student_id=sid, academic_year=year, due_date=(today+timedelta(days=[7,25,-2][i])).isoformat(), status='Запланирована', result=''))
    people = records('students')
    save('publications', dict(student_ids=[p['id'] for p in people[:2]], title='Демонстрационная статья о цифровых сервисах кафедры', journal='Учебный пример журнала', year=today.year, doi='', vak=True, scopus=True, verified=False))
    create_plan(dict(student_id=people[0]['id'], academic_year=year))
    reminders()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200_000).hex()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def response(self, status, body, content_type='application/json; charset=utf-8', headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        cookie = self.headers.get('Cookie', '')
        return any(x.strip().startswith('arm_session=') and x.strip().split('=', 1)[1] in SESSIONS for x in cookie.split(';'))

    def body(self):
        n = int(self.headers.get('Content-Length', 0))
        if not 0 < n <= 1_000_000:
            raise ValidationError('Пустой или слишком большой запрос')
        obj = json.loads(self.rfile.read(n))
        if not isinstance(obj, dict):
            raise ValidationError('Некорректный формат запроса')
        return obj

    def dispatch(self, method):
        try:
            if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
                return self.response(403, {'error': 'Недопустимый адрес сервера'})
            path = urlparse(self.path).path
            if method == 'GET' and path in ('/', '/app.js', '/style.css'):
                filename = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
                mime = {'/': 'text/html', '/app.js': 'text/javascript', '/style.css': 'text/css'}[path]
                return self.response(200, (ROOT / 'static' / filename).read_bytes(), mime + '; charset=utf-8')
            if method == 'POST' and path == '/api/login':
                # JSON + проверка Origin не позволяют стороннему сайту отправлять формы входа.
                if not self.headers.get('Content-Type', '').startswith('application/json'):
                    return self.response(415, {'error': 'Требуется JSON'})
                origin = self.headers.get('Origin')
                if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                    return self.response(403, {'error': 'Недопустимый источник'})
                data = self.body()
                auth = json.loads((DATA / 'auth.json').read_text())
                if not hmac.compare_digest(password_hash(str(data.get('password', '')), auth['salt']), auth['hash']):
                    time.sleep(.3)
                    return self.response(401, {'error': 'Неверный пароль'})
                session = secrets.token_urlsafe(32)
                SESSIONS.add(session)
                return self.response(200, {'ok': True}, headers={'Set-Cookie': f'arm_session={session}; HttpOnly; SameSite=Strict; Path=/'})
            if not self.authorized():
                return self.response(401, {'error': 'Войдите в систему'})
            if method != 'GET' and not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), TOKEN):
                return self.response(403, {'error': 'Обновите страницу и повторите действие'})
            if method == 'GET' and path == '/api/state':
                return self.response(200, snapshot())
            if method == 'GET' and path.startswith('/api/export/'):
                ident = path.rsplit('/', 1)[1]
                return self.response(200, export_docx(ident), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', {'Content-Disposition': 'attachment; filename="individual-plan.docx"'})
            if method == 'POST' and path == '/api/logout':
                for cookie in self.headers.get('Cookie', '').split(';'):
                    if cookie.strip().startswith('arm_session='):
                        SESSIONS.discard(cookie.strip().split('=', 1)[1])
                return self.response(200, {'ok': True}, headers={'Set-Cookie': 'arm_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'})
            if method == 'POST' and path == '/api/demo':
                with LOCK:
                    demo()
                return self.response(200, {'ok': True})
            if method == 'POST' and path == '/api/read-notifications':
                with LOCK, db('attestations') as con:
                    con.execute('UPDATE notifications SET is_read=1')
                return self.response(200, {'ok': True})
            parts = path.strip('/').split('/')
            if len(parts) in (2, 3) and parts[0] == 'api' and parts[1] in TABLES:
                module = parts[1]
                ident = parts[2] if len(parts) == 3 else None
                if method == 'POST' and not ident:
                    data = self.body()
                    row = create_plan(data) if module == 'plans' else save(module, data)
                    return self.response(201, row)
                if method == 'PUT' and ident:
                    data = self.body()
                    row = update_plan(ident, data) if module == 'plans' else save(module, data, ident)
                    return self.response(200, row)
                if method == 'DELETE' and ident:
                    delete(module, ident)
                    return self.response(200, {'ok': True})
            return self.response(404, {'error': 'Страница не найдена'})
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            self.response(400, {'error': str(exc) if isinstance(exc, ValidationError) else 'Некорректные данные запроса'})
        except Exception as exc:
            error_id = str(uuid.uuid4())[:8]
            print('Error', error_id, type(exc).__name__, str(exc))
            self.response(500, {'error': f'Не удалось выполнить операцию. Код: {error_id}'})

    def do_GET(self): self.dispatch('GET')
    def do_POST(self): self.dispatch('POST')
    def do_PUT(self): self.dispatch('PUT')
    def do_DELETE(self): self.dispatch('DELETE')


def main():
    parser = argparse.ArgumentParser(description='АРМ Кафедра — управление магистратурой')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--demo', action='store_true', help='Загрузить вымышленные данные, если система пуста')
    args = parser.parse_args()
    init_db()
    auth_file = DATA / 'auth.json'
    if not auth_file.exists():
        import getpass
        password = os.environ.get('ARM_PASSWORD') or getpass.getpass('Создайте пароль администратора (не менее 8 символов): ')
        if len(password) < 8:
            raise SystemExit('Пароль должен содержать не менее 8 символов. Запустите программу заново.')
        salt = secrets.token_hex(16)
        auth_file.write_text(json.dumps({'salt': salt, 'hash': password_hash(password, salt)}))
    if args.demo and not records('students'):
        demo()
    stop = threading.Event()
    def scheduler():
        while not stop.is_set():
            try:
                reminders()
            except Exception as exc:
                print('Reminder error:', type(exc).__name__)
            stop.wait(60)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    threading.Thread(target=scheduler, daemon=True).start()
    print(f'АРМ Кафедра: http://127.0.0.1:{args.port}  |  Остановка: Ctrl+C', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
