"""Normalize actual archive tables and reviewed evidence; do not infer dock times."""
import json
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'customer-data'
records=json.loads((OUT/'extracted.json').read_text(encoding='utf-8'))
review=json.loads((OUT/'reviewed-evidence.json').read_text(encoding='utf-8'))
current_checksums={key:record['sha256'] for key,record in records.items()}
if review.get('sourceChecksums') != current_checksums:
    raise ValueError('Reviewed evidence is not pinned to the current archive checksums. Review changed evidence and update its sourceChecksums before normalizing.')
rows=lambda key: records[key]['rows']
stock=rows('soony/erp/stock_snapshot.csv')
receipts={r['gr_number']:r for r in rows('soony/erp/goods_receipts.csv')}
parts={r['component_id']:r for r in rows('soony/master/components.csv')}
plan=records['soony/context/launch_plan_GH6.xlsx']['sheets']
erp=defaultdict(int)
for r in stock:
    erp[r['material_id']]+=int(r['quantity'])
usable=dict(erp)
for correction in review['stockAdjustments']:
    batch=next(r for r in stock if r['batch']==correction['batch'])
    assert batch['material_id']==correction['component']
    usable[correction['component']]-=int(batch['quantity'])-correction['usable']
for adjustment in review['conservativeHolds']:
    usable[adjustment['component']]-=adjustment['quantity']
components=[]
for material in parts:
    counts=[r['last_count'] for r in stock if r['material_id']==material]
    if not counts:
        continue
    components.append({'id':material,'name':parts[material]['description'],'erp':erp[material],'usable':usable[material],
      'observedAt':min(counts)+'T00:00:00+01:00','source':'soony/erp/stock_snapshot.csv; reviewed-evidence.json',
      'basis':'Stock snapshot less documented corrections and conservative holds. Physical recount outstanding.'})
products=[]
for row in plan['Editions'][3:]:
    if not row[1]:continue
    bom={r['child_id']:int(r['qty_per']) for r in rows('soony/master/bom.csv') if r['parent_id']==row[1] and r['valid_from']<='2026-11-22'<=r['valid_to']}
    for embedded in review['embeddedComponents']:
        if embedded['parentComponent'] in bom:
            bom.pop(embedded['childComponent'],None)
    products.append({'id':row[1],'name':row[0],'target':int(row[2]),'packed':int(row[4]),'rate':row[6],
      'priority':0 if row[0]=="Collector's" else 1,'bom':bom,'source':'soony/context/launch_plan_GH6.xlsx Editions; soony/master/bom.csv; reviewed embedded-component evidence'})
active_components={component for product in products for component in product['bom']}
components=[component for component in components if component['id'] in active_components]
snapshot={'sample':False,'dataset':'Soony customer archive / kickoff replay','now':review['evaluationTime'],'deadline':'2026-11-22T20:00:00+01:00','staleHours':12,
 'shifts':[{'start':'2026-11-21T06:00:00+01:00','end':'2026-11-21T22:00:00+01:00'},{'start':'2026-11-22T06:00:00+01:00','end':'2026-11-22T20:00:00+01:00'}],
 'products':products,'components':components,'lots':[], 'findings':review['findings'],
 'assumptions':review['assumptions'], 'archiveFiles':len(records),
 'inboundUnscheduled':plan['Inbound'][1:],'sourceChecksums':current_checksums}
(OUT/'snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
normalized_pos=[]
for r in rows('soony/erp/purchase_orders_legacy_2024-2025H1.csv'):
    normalized_pos.append({'po_number':r['EBELN'],'po_line':str(int(r['EBELP'])//10),'supplier_id':'SUP-'+r['LIFNR'].zfill(4),'material_id':r['MATNR'],'quantity':int(r['MENGE']),'unit_price':float(r['NETPR'].replace(',','.')),'source':'legacy','raw':r})
for r in rows('soony/erp/purchase_orders_2025H2-2026.csv'):
    normalized_pos.append({**r,'quantity':int(r['quantity']),'unit_price':float(r['unit_price']),'source':'current'})
(OUT/'purchase-orders-normalized.json').write_text(json.dumps(normalized_pos,ensure_ascii=False,indent=2),encoding='utf-8')
needed=defaultdict(int)
for p in products:
    for component,quantity in p['bom'].items():needed[component]+=(p['target']-p['packed'])*quantity
audit=[{'component':k,'description':parts[k]['description'],'needed':v,'erp':erp[k],'reviewedUsable':usable[k],'stockOnlyGap':max(0,v-usable[k])}for k,v in sorted(needed.items())]
(OUT/'material-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'products':products,'materialAudit':audit,'purchaseOrderLines':len(normalized_pos),'findings':len(snapshot['findings'])},ensure_ascii=False,indent=2))
