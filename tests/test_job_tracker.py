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
