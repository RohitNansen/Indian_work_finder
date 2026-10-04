from app import ats_feeds, direct_links


def test_ats_feed_normalizes_original_publish_date_and_company_url():
    lever=ats_feeds._raw_job('Lever','dozee','Dozee','https://www.dozee.health',{
        'id':'role-1','text':'Hardware Lead','createdAt':1790921020721,
        'hostedUrl':'https://jobs.lever.co/dozee/role-1',
        'categories':{'location':'Bangalore','commitment':'Full Time'},
        'descriptionPlain':'Lead medical hardware development and verification.'})
    assert lever['job_posted_at_datetime_utc'].startswith('2026-10-02')
    assert lever['job_apply_link'].startswith('https://jobs.lever.co/dozee/')
    greenhouse=ats_feeds._raw_job('Greenhouse','instawork','Instawork','https://www.instawork.com',{
        'id':123,'title':'Head of Quality Assurance - Robotics','first_published':'2026-08-28T00:45:03-04:00',
        'absolute_url':'https://job-boards.greenhouse.io/instawork/jobs/123',
        'location':{'name':'Bengaluru, India'},'content':'<p>Own quality strategy.</p>'})
    assert greenhouse['job_posted_at_datetime_utc']=='2026-08-28T00:45:03-04:00'
    assert greenhouse['job_description']=='Own quality strategy.'
    assert ats_feeds._raw_job('Greenhouse','instawork','Instawork','https://www.instawork.com',{
        'id':124,'title':'Office Coordinator','absolute_url':'https://job-boards.greenhouse.io/instawork/jobs/124'}) is None


def test_greenhouse_api_requires_matching_live_vacancy(monkeypatch):
    class Response:
        status_code=200
        def raise_for_status(self):pass
        def json(self):
            return {'title':'Head of Quality Assurance - Robotics','company_name':'Instawork',
                    'location':{'name':'Bengaluru, Karnataka, India'},
                    'content':'<p>'+('Own manufacturing quality, robotics systems, and root cause analysis. '*3)+'</p>',
                    'first_published':'2026-08-28T00:45:03-04:00','application_deadline':None}
    class Client:
        def __init__(self,*a,**k):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url,**kwargs):
            assert url=='https://boards-api.greenhouse.io/v1/boards/instawork/jobs/123'
            return Response()
    monkeypatch.setattr(direct_links.httpx,'Client',Client)
    job={'title':'Head of Quality Assurance - Robotics','company':'Instawork',
         'location':'Bengaluru, Karnataka, India','company_url':'https://www.instawork.com'}
    url='https://job-boards.greenhouse.io/instawork/jobs/123'
    valid,_,details=direct_links.greenhouse_vacancy_details(url,'<h1>Head of Quality Assurance - Robotics</h1>',job)
    assert valid and details['posted_at']=='2026-08-28T00:45:03-04:00'
    wrong=dict(job,title='Head of Sales')
    assert not direct_links.greenhouse_vacancy_details(url,'<h1>Head of Quality Assurance - Robotics</h1>',wrong)[0]


def test_apify_pilot_does_not_count_naukri_link_as_company_application(test_env):
    from deploy.pilot_apify import compare
    summary=compare([{'title':'Hardware Lead','companyName':'Dozee','location':'Bangalore',
                      'createdDate':'2026-10-02T00:00:00Z',
                      'companyApplyUrl':'https://www.naukri.com/job-listings-example'}])
    assert summary['records']==1 and summary['topic_titles']==1
    assert summary['company_links_supplied']==0 and summary['company_links_verified']==0
