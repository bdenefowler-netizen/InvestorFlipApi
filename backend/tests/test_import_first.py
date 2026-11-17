import asyncio
import copy
import re
from types import SimpleNamespace
from intake_v3 import upsert_import_records
from bulk_import import _prepare_row, _upload_categories
from import_matching import legal_query


def matches(doc, query):
    for key, value in query.items():
        if key == '$or':
            if not any(matches(doc, item) for item in value): return False
        elif isinstance(value, dict) and '$regex' in value:
            if not re.search(value['$regex'], str(doc.get(key, '')), re.I): return False
        elif doc.get(key) != value: return False
    return True


class Collection:
    def __init__(self, rows=()): self.rows = list(copy.deepcopy(rows))
    def find(self, query, projection=None):
        rows = [x for x in self.rows if matches(x, query)]
        class Cursor:
            def limit(self, count): return self
            async def to_list(self, length=None): return copy.deepcopy(rows[:length])
        return Cursor()
    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if matches(row, query): row.update(update['$set']); return
        if upsert: self.rows.append(copy.deepcopy(update['$set']))


def run(rows, category, county=(), properties=()):
    db = SimpleNamespace(county_records=Collection(county), properties=Collection(properties))
    prepared = [_prepare_row(row, [category]) for row in rows]
    report = asyncio.run(upsert_import_records(db, prepared, f'User upload [{category}]: test.csv'))
    return report, db.properties.rows


PARCEL = dict(id='county-1', account_id='00123', normalized_account_id='123', legal_description='LOT 1 BLOCK 2 TEST', situs_address='100 Main St', city='Fort Worth', owner_name='County Owner')


def test_legal_only_foreclosure_gets_county_identity_and_survives_reupload():
    raw = {'Legal_Description':'LOT 1, BLOCK 2 TEST', 'Current_Owner':'Old Owner', 'Sell_Date':'12/01/2026'}
    report, props = run([raw], 'pre_foreclosure', [PARCEL])
    assert report['accepted'] == 1
    assert props[0]['account_id'] == '00123'
    assert props[0]['situs_address'] == '100 Main St'
    assert props[0]['owner_name'] == 'County Owner'
    assert props[0]['sale_date'] == '12/01/2026'
    report, props = run([raw], 'pre_foreclosure', [PARCEL], props)
    assert report['updated'] == 1 and len(props) == 1


def test_tad_wins_over_conflicting_legal_and_address():
    _, props = run([{'TAD Account #':'123','Legal Description':'WRONG','Property Address':'999 Wrong St'}], 'pre_foreclosure', [PARCEL])
    assert props[0]['legal_description'] == PARCEL['legal_description']
    assert props[0]['owner_name'] == PARCEL['owner_name']


def test_ambiguous_legal_never_selects_first_parcel():
    _, props = run([{'Legal Description':PARCEL['legal_description']}], 'pre_foreclosure', [PARCEL,{**PARCEL,'id':'county-2','account_id':'999'}])
    assert not props[0].get('account_id')
    assert props[0]['import_review_required']


def test_legal_mismatch_does_not_fall_back_to_wrong_address():
    _, props = run([{'Legal Description':'DIFFERENT LOT','Property Address':'100 Main St','City':'Fort Worth'}], 'repair_notice', [PARCEL])
    assert not props[0].get('account_id')
    assert props[0]['has_repair_notice']


def test_only_recognized_open_code_cases_are_imported():
    rows=[{'Violation_Address':f'{n} Main St','City':'Fort Worth','Violation_Current_Status':status} for n,status in enumerate(['Open','Closed','Unknown',''],100)]
    report, props = run(rows, 'code_violations')
    assert report['accepted']==1 and report['rejected']==3
    assert props[0]['open_code_violation_count']==1


def test_recorded_lien_is_not_invented_tax_auction():
    _, props = run([{'Legal Description':'LOT 4 TEST','Grantor':'Person','Doc_Type':'FEDERAL TAX LIEN'}], 'tax_lien')
    assert props[0]['has_uploaded_tax_lien']
    assert not props[0].get('tax_lien_active')
    assert props[0]['document_type']=='FEDERAL TAX LIEN'


def test_explicit_new_categories():
    for category in ['repair_notice','tax_auction','tax_lien','pre_foreclosure']:
        prefix='preforeclosure' if category=='pre_foreclosure' else category
        assert _upload_categories(prefix+'__mixed.csv',[{'Violation_Address':'LOT 4 TEST'}])==[category]


def test_chunk_transport_reaches_direct_import_without_admin(monkeypatch, tmp_path):
    import bulk_import
    import upload_runtime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    db = SimpleNamespace(county_records=Collection(), properties=Collection())
    async def connect(): return None
    db.connect = connect
    db.close = connect
    monkeypatch.setattr(bulk_import, 'PostgresDatabase', lambda: db)
    monkeypatch.setattr(upload_runtime, '_CHUNK_ROOT', tmp_path)
    app = FastAPI()
    app.include_router(bulk_import.router, prefix='/api')
    client = TestClient(app)
    payload = b'Legal_Description,Current_Owner\nLOT 8 BLOCK 1 TEST,Person\n'
    parts = [payload[:20], payload[20:]]
    common = dict(upload_id='regression',total_chunks='2',filename='preforeclosure__test.csv',total_size=str(len(payload)))
    for i, part in enumerate(parts):
        response = client.post('/api/import/bulk/upload-workbook/chunk',data={**common,'chunk_index':str(i)},files={'chunk':('part',part)})
        assert response.status_code == 200
    response = client.post('/api/import/bulk/upload-workbook/complete',data=common)
    assert response.status_code == 200, response.text
    assert response.json()['inserted'] == 1
    assert db.properties.rows[0]['has_pre_foreclosure']
    assert not (tmp_path/'regression').exists()


def test_zip_keeps_selected_source(monkeypatch):
    import bulk_import
    import io, zipfile
    from fastapi import UploadFile
    db = SimpleNamespace(county_records=Collection(), properties=Collection())
    async def connect(): return None
    db.connect = connect
    db.close = connect
    monkeypatch.setattr(bulk_import, 'PostgresDatabase', lambda: db)
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('unhelpful.csv','Legal Description,Owner\nLOT 9 BLOCK 1 TEST,Person\n')
    archive.seek(0)
    report=asyncio.run(bulk_import.public_county_workbook_upload(UploadFile(filename='repair_notice__cases.zip',file=archive)))
    assert report['ok'] and report['categories']==['repair_notice']
    assert db.properties.rows[0]['has_repair_notice']


def test_copied_street_is_not_a_legal_match_key():
    from intake_v3 import normalize_import_row
    row=normalize_import_row({'Property_Address':'100 Main St, Fort Worth, TX 76102','Legal_Description':'100 Main St, Fort Worth, TX 76102'},'User upload [pre_foreclosure]: test.csv',2)
    assert not row.get('legal_description') and not row.get('legal_key')
    assert row['address_key']


def test_code_header_aliases_reach_county_workspace():
    from import_worker import _code_records
    prepared=_prepare_row({'Property Address':'100 Main St','Case Current Status':'Open','Case ID':'C-1'},['code_violations'])
    assert _code_records([prepared])[0]['open_code_violation_count']==1


def test_runtime_lead_route_does_not_stage(monkeypatch):
    import bulk_import, upload_runtime, io
    from fastapi import UploadFile
    called=[]
    async def direct(file):
        called.append(file.filename)
        return {'ok':True,'inserted':1}
    monkeypatch.setattr(bulk_import,'public_county_workbook_upload',direct)
    report=asyncio.run(upload_runtime._stage_uploaded_file(UploadFile(filename='preforeclosure__test.csv',file=io.BytesIO(b'legal\nLOT 1 TEST\n'))))
    assert report['inserted']==1 and called==['preforeclosure__test.csv']


def test_combining_property_rows_preserves_each_case():
    report, props=run([{'Legal Description':'LOT 1 TEST','Case ID':'A'},{'Legal Description':'LOT 1 TEST','Case ID':'B'}],'repair_notice')
    assert report['duplicates_merged']==1
    assert len(props[0]['import_evidence'])==2
