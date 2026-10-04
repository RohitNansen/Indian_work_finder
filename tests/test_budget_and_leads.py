import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import db, quota
from app.budget import BudgetExceeded, month_spend, monthly_limit, reserve_call, set_monthly_limit
from app.company_memory import remember_source_lead
from app.config import settings
from app.main import app
from conftest import csrf


def test_paid_request_stops_before_limit_and_emails_owner_once(test_env, monkeypatch):
    original_limit=settings.monthly_spend_limit_usd
    original_email=settings.quota_alert_email
    object.__setattr__(settings,'monthly_spend_limit_usd',30)
    object.__setattr__(settings,'quota_alert_email','owner@example.com')
    sent=[]
    monkeypatch.setattr(quota,'send_email',lambda *args: sent.append(args) or 'sent-id')
    try:
        set_monthly_limit(30)
        db.write('INSERT INTO api_calls(provider,operation,request_json,estimated_cost_usd,started_at) '
                 'VALUES(?,?,?,?,?)',('JSearch','prior','{}',29.7,db.utcnow()))
        with pytest.raises(BudgetExceeded):
            reserve_call(None,'OpenRouter','rank_jobs','{}',0.5)
        assert month_spend()==pytest.approx(29.7)
        assert len(sent)==1 and 'Paid API requests have been paused' in sent[0][2]
        with pytest.raises(BudgetExceeded):
            reserve_call(None,'OpenRouter','rank_jobs','{}',0.5)
        assert len(sent)==1
        call_id=reserve_call(None,'JSearch','search','{}',0.005)
        assert db.one('SELECT estimated_cost_usd FROM api_calls WHERE id=?',(call_id,))['estimated_cost_usd']==0.005
    finally:
        object.__setattr__(settings,'monthly_spend_limit_usd',original_limit)
        object.__setattr__(settings,'quota_alert_email',original_email)


def test_owner_can_lower_stop_amount_but_candidate_cannot(test_env):
    original_password=settings.owner_password
    original_limit=settings.monthly_spend_limit_usd
    object.__setattr__(settings,'owner_password','distinct-owner-password')
    object.__setattr__(settings,'monthly_spend_limit_usd',30)
    try:
        with TestClient(app) as candidate:
            candidate.post('/login',data={'password':settings.app_password})
            assert candidate.post('/api-use/limit',data={'_csrf':csrf(candidate),'monthly_limit_usd':'12'}).status_code==403
        with TestClient(app) as owner:
            owner.post('/login',data={'password':'distinct-owner-password'})
            response=owner.post('/api-use/limit',data={'_csrf':csrf(owner),'monthly_limit_usd':'20'},follow_redirects=False)
            assert response.status_code==303 and monthly_limit()==20
            assert 'Searches started this month' in owner.get('/api-use').text
            assert owner.post('/api-use/limit',data={'_csrf':csrf(owner),'monthly_limit_usd':'31'}).status_code==400
    finally:
        object.__setattr__(settings,'owner_password',original_password)
        object.__setattr__(settings,'monthly_spend_limit_usd',original_limit)


def test_simultaneous_paid_requests_share_one_remaining_budget(test_env,monkeypatch):
    original_limit=settings.monthly_spend_limit_usd
    monkeypatch.setattr(quota,'send_email',lambda *args:'sent-id')
    object.__setattr__(settings,'monthly_spend_limit_usd',30)
    try:
        db.write('INSERT INTO api_calls(provider,operation,request_json,estimated_cost_usd,started_at) '
                 'VALUES(?,?,?,?,?)',('JSearch','prior','{}',29.7,db.utcnow()))
        def request():
            try:
                reserve_call(None,'OpenRouter','rank_jobs','{}',0.2)
                return 'reserved'
            except BudgetExceeded:
                return 'stopped'
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(lambda _:request(),range(2)))==['reserved','stopped']
        assert month_spend()==pytest.approx(29.9)
    finally:
        object.__setattr__(settings,'monthly_spend_limit_usd',original_limit)


def test_every_naukri_listing_kept_in_private_inventory(test_env):
    item={'jobId':'123','title':'Quality Engineer','companyName':'Device Maker',
          'location':'Chennai','portalUrl':'https://www.naukri.com/job-listings-123'}
    remember_source_lead('Apify Naukri',item)
    remember_source_lead('Apify Naukri',{**item,'title':'Senior Quality Engineer'})
    rows=db.all_rows('SELECT * FROM source_leads')
    assert len(rows)==1 and rows[0]['title']=='Senior Quality Engineer'
    assert json.loads(rows[0]['raw_json'])['companyName']=='Device Maker'
