"""Read-only extraction of the local Soony archive; all outputs stay ignored by Git."""
import csv
import hashlib
import json
from pathlib import Path
from pypdf import PdfReader
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'customer-data' / 'soony-data'
OUT = ROOT / 'launchguard' / 'customer-data'
OUT.mkdir(parents=True, exist_ok=True)
records = {}
index_path=OUT/'portal-file-index.json'
active_paths=None
if index_path.exists():
    active_paths={row['path'] for row in json.loads(index_path.read_text(encoding='utf-8-sig'))['files']}
for file in sorted(SOURCE.rglob('*')):
    if not file.is_file():
        continue
    key = file.relative_to(SOURCE).as_posix()
    if active_paths is not None and key not in active_paths:
        continue
    record = {'path': key, 'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
    try:
        if file.suffix.lower() == '.pdf':
            reader = PdfReader(file)
            record['pages'] = [page.extract_text() or '' for page in reader.pages]
            record['text'] = '\n'.join(record['pages'])
            record['needs_ocr'] = len(record['text'].strip()) < 20
        elif file.suffix.lower() == '.xlsx':
            book = load_workbook(file, read_only=True, data_only=True)
            record['sheets'] = {sheet.title: list(sheet.values) for sheet in book}
            book.close()
        elif file.suffix.lower() == '.csv':
            with file.open(encoding='utf-8-sig', newline='') as stream:
                record['rows'] = list(csv.DictReader(stream, delimiter=';' if file.name.startswith('purchase_orders_legacy') else ','))
        elif file.suffix.lower() in ('.md', '.txt', '.json'):
            record['text'] = file.read_text(encoding='utf-8-sig')
        elif file.suffix.lower() in ('.jpg', '.png', '.jpeg'):
            record['needs_ocr'] = True
    except Exception as exc:
        record['error'] = str(exc)
    records[key] = record
(OUT / 'extracted.json').write_text(json.dumps(records, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
important = {k:v for k,v in records.items() if any(x in k for x in ('/notices/', '/contracts/', '/context/', 'inspection_report'))}
(OUT / 'priority-evidence.txt').write_text('\n\n'.join(k+'\n'+v.get('text', json.dumps(v.get('sheets', {}), ensure_ascii=False, default=str)) for k,v in important.items()),encoding='utf-8')
print(json.dumps({'files':len(records),'errors':[k for k,v in records.items() if 'error' in v],'ocr_needed':[k for k,v in records.items() if v.get('needs_ocr')],'output':str(OUT)},indent=2))
