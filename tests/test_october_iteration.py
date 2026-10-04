import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from fastapi.testclient import TestClient
from app import db,direct_links,search
from app.matching import title_matches,location_matches,recent,role_variants
from app.config import settings
from app.main import app
from app.resume import preflight_changes
from test_candidate_upgrade import employer_html
from test_workflow import sample_job
from conftest import csrf


def test_functional_title_identity():
    assert not title_matches('Head of R&D','Head of Sales')
    assert not title_matches('R&D Director','Sales Director')
    assert title_matches('Head of Quality','Head of Quality Management')
    assert title_matches('R&D Director','Director Research and Development')
    assert not title_matches('Head of R&D','Business Development Head')


def test_structured_title_cannot_override_wrong_visible_title():
    job={'title':'Research Director','company':'Example Engineering','company_url':'https://example.com','location':'Chennai'}
    html=employer_html().replace('<h1>Research Director</h1>','<h1>Head of Sales</h1>')
    assert not direct_links.verify_page('https://example.com/jobs/1',html,job)[0]


def test_vacancy_location_not_company_footer():
    job={'title':'Research Director','company':'Example Engineering','company_url':'https://example.com','location':'Chennai, Tamil Nadu'}
    html=employer_html().replace('"addressLocality": "Chennai"','"addressLocality": "Mumbai"')+'<footer>Company headquarters Chennai, Tamil Nadu, India</footer>'
    assert not direct_links.verify_page('https://example.com/jobs/1',html,job)[0]
    assert location_matches('Bangalore, Karnataka, India',['Bengaluru'])
    assert not location_matches('Mumbai, Maharashtra, India',['Chennai','Tamil Nadu'])
    assert location_matches('Remote India',['Remote India'],True)
    assert not location_matches('Remote US',['Remote India'],True)


def test_date_ranges_and_related_roles():
    date=(datetime.now(timezone.utc)-timedelta(days=45)).isoformat()
    assert not recent(date,30) and recent(date,60)
    assert not recent('',90)
    assert 'Quality Management Head' in role_variants('Head of Quality')
    queue=list(search.query_queue(['R&D Director','Head of Quality','Automation Consultant'],['Chennai'],['PLC']))
    assert 'R&D Director' in queue[0][0] and 'Head of Quality' in queue[1][0] and 'Automation Consultant' in queue[2][0]
    assert any('programmable logic controller' in x[0] for x in queue)


def test_login_only_password_and_hidden_owner_pages(test_env,monkeypatch):
    original=settings.app_password
    object.__setattr__(settings,'app_password','654321')
    with TestClient(app) as client:
        assert 'name="username"' not in client.get('/login').text
        assert client.post('/login',data={'password':'654321'}).status_code==200
        assert client.get('/activity').status_code==403
        assert '/activity' not in client.get('/').text
        assert 'Surendran’s sign-in' not in client.get('/profile').text
        assert '<option selected>10</option>' in client.get('/').text
        assert 'Last 2 months' in client.get('/').text and 'Last 3 months' in client.get('/').text
        assert 'id="create-alert" disabled' in client.get('/alerts').text
    object.__setattr__(settings,'app_password',original)


def test_pdf_too_long_preflight_retains_source(test_env):
    import pymupdf as fitz
    source=test_env/'resume.pdf';doc=fitz.open();page=doc.new_page()
    # Base14 fonts are intentionally unsupported for in-place edits: the fit check must be honest.
    page.insert_text((50,50),'Electrical leadership');doc.save(source)
    before=source.read_bytes()
    result=preflight_changes(source,[{'original':'Electrical leadership','replacement':'Electrical leadership '+('new details '*100)}])
    assert not result[0]['fits'] and source.read_bytes()==before


def test_progress_endpoint_has_counts(client):
    run=db.write('INSERT INTO search_runs(kind,started_at,parameters_json,progress_json) VALUES(?,?,?,?)',('manual',db.utcnow(),'{}',json.dumps({'queries':4,'found':12,'checked':5,'matched':2,'stage':'Checking company vacancies'})))
    html=client.get(f'/runs/{run}').text
    assert 'role="progressbar"' in html and 'Possible listings' in html and 'Suitable jobs' in html


def test_downloads_survive_unfittable_selected_edit(client,test_env):
    import pymupdf as fitz
    source=test_env/'resume.pdf';doc=fitz.open();doc.new_page().insert_text((50,50),'Electrical leadership');doc.save(source)
    rid=db.write('INSERT INTO resumes(filename,stored_path,file_type,resume_text,style_sample,uploaded_at) VALUES(?,?,?,?,?,?)',('resume.pdf',str(source),'.pdf','Electrical leadership','',db.utcnow()))
    job=search.save_job(sample_job('Research Director','Example Engineering','resume-fail'))
    vid=db.write('INSERT INTO resume_variants(job_id,base_resume_id,proposed_json,created_at) VALUES(?,?,?,?)',(job['id'],rid,json.dumps([{'original':'Electrical leadership','replacement':'Extra long '+('leadership '*100),'reason':'Test overflow'}]),db.utcnow()))
    result=client.post(f'/resume-variants/{vid}/approve',data={'_csrf':csrf(client),'change_0':'on'})
    assert result.status_code==200 and 'Some original wording was retained' in result.text
    assert client.get(f'/resume-variants/{vid}/pdf').content==source.read_bytes()
    assert client.get(f'/resume-variants/{vid}/docx').status_code==200


def test_pdf_edit_keeps_neighbouring_columns(test_env):
    import pymupdf as fitz
    from app.resume import _pdf_with_original_layout
    font=fitz.Font('cjk');doc=fitz.open();page=doc.new_page()
    page.insert_font(fontname='embedded',fontbuffer=font.buffer)
    for x,text in [(40,'Electrical design'),(220,'PLC integration'),(400,'Embedded systems')]:
        page.insert_text((x,60),text,fontname='embedded',fontsize=11)
    source=test_env/'columns.pdf';target=test_env/'changed.pdf';doc.save(source)
    original=fitz.open(source);before={w[4]:w[:4] for w in original[0].get_text('words')}
    _pdf_with_original_layout(source,target,[{'original':'PLC integration','replacement':'Robotic integration'}])
    changed=fitz.open(target);after={w[4]:w[:4] for w in changed[0].get_text('words')}
    assert after['Electrical']==before['Electrical'] and after['Embedded']==before['Embedded']
    assert 'Robotic' in after and 'PLC' not in after
