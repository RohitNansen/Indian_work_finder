from app import db
from app.search import save_job
from test_workflow import sample_job


def test_tracker_keeps_opened_default_and_surfaces_unopened_alert_match(client):
    missed = save_job(sample_job('Head of Quality', 'Device Maker', 'missed'))
    opened = save_job(sample_job('R&D Director', 'Textile Lab', 'opened'))
    for job in (missed, opened):
        db.write("UPDATE jobs SET direct_status='verified',verification_version=3,direct_url=apply_url WHERE id=?", (job['id'],))
    run = db.write("INSERT INTO search_runs(kind,started_at,parameters_json,status) VALUES('alert',?,'{}','complete')", (db.utcnow(),))
    for job in (missed, opened):
        db.write("INSERT INTO search_results(run_id,job_id,rank,internal_score,why) VALUES(?,?,?,?,?)",
                 (run,job['id'],1,85,'Relevant quality and product leadership'))
    db.write("INSERT INTO job_activity(job_id,action,occurred_at) VALUES(?,'listing_opened',?)", (opened['id'],db.utcnow()))

    default = client.get('/my-jobs').text
    assert 'Job tracker' in default and 'Opened jobs and status' in default
    assert 'Textile Lab' in default and 'Device Maker' not in default

    missed_page = client.get('/my-jobs?view=recommended').text
    assert 'Recommended jobs you may have missed' in missed_page
    assert 'Device Maker' in missed_page and 'Textile Lab' not in missed_page
    assert 'Relevant quality and product leadership' in missed_page
    assert 'Email alert' in missed_page

    db.write("INSERT INTO job_activity(job_id,action,occurred_at) VALUES(?,'listing_opened',?)", (missed['id'],db.utcnow()))
    assert 'Device Maker' not in client.get('/my-jobs?view=recommended').text
    assert 'Device Maker' in client.get('/my-jobs').text


def test_tracker_status_priority_and_date_sort_keep_closed_at_bottom(client):
    rows=[]
    for index,(company,status) in enumerate([
        ('Opened Lab',None),('Rejected Lab','Rejected'),
        ('Applied Lab','Applied'),('Offer Lab','Offer')]):
        job=save_job(sample_job('R&D Head',company,f'sort-{index}'))
        added_day={'Opened Lab':2,'Rejected Lab':4,'Applied Lab':3,'Offer Lab':1}[company]
        db.write('UPDATE jobs SET first_seen_at=? WHERE id=?',
                 (f'2026-10-0{added_day}T10:00:00+00:00',job['id']))
        db.write("INSERT INTO job_activity(job_id,action,occurred_at) VALUES(?,'listing_opened',?)",
                 (job['id'],f'2026-10-0{4-index}T12:00:00+00:00'))
        if status:
            db.write('INSERT INTO job_status(job_id,status,updated_at) VALUES(?,?,?)',
                     (job['id'],status,db.utcnow()))
        rows.append(company)
    default=client.get('/my-jobs').text
    assert default.index('Offer Lab') < default.index('Applied Lab') < default.index('Opened Lab') < default.index('Rejected Lab')
    assert 'First added' in default and 'Last opened' in default
    by_added=client.get('/my-jobs?sort=added').text
    assert by_added.index('Applied Lab') < by_added.index('Opened Lab') < by_added.index('Offer Lab') < by_added.index('Rejected Lab')
    by_opened=client.get('/my-jobs?sort=opened').text
    assert by_opened.index('Opened Lab') < by_opened.index('Applied Lab') < by_opened.index('Offer Lab') < by_opened.index('Rejected Lab')
