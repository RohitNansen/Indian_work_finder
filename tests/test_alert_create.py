from app import db
from conftest import csrf


def test_create_alert_is_always_available_and_starts_paused(client):
    page=client.get('/alerts').text
    assert 'id="create-alert"' in page and 'id="create-alert" disabled' not in page
    assert 'value="s_nansen@yahoo.co.in, anitha.nansen@gmail.com"' in page
    response=client.post('/alerts',data={
        '_csrf':csrf(client),'name':'Weekly research roles',
        'email':'s_nansen@yahoo.co.in, anitha.nansen@gmail.com',
        'roles':'Head of Quality','keywords':'manufacturing quality',
        'locations':'Chennai','work_types':'Consulting',
        'time_ist':'08:00','count':'5','days_recent':'7',
    },follow_redirects=False)
    assert response.status_code==303
    row=db.one('SELECT name,email,enabled FROM alerts ORDER BY id DESC LIMIT 1')
    assert row['name']=='Weekly research roles'
    assert row['enabled']==0
