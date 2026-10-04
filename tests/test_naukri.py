from app.naukri import query_choice, to_raw


def test_naukri_query_rotates_role_and_city_without_adding_every_skill():
    roles=['Head of Quality','Hardware Engineering Head']
    places=['Chennai','Bengaluru','Remote India']
    assert query_choice(roles,places,1)==('quality head manufacturing','Chennai')
    assert query_choice(roles,places,2)==('quality head manufacturing','Bangalore')
    assert query_choice(roles,places,3)==('hardware engineering lead','Chennai')


def test_naukri_leads_are_industrial_and_portal_is_only_discovery():
    row={'jobId':'123','title':'Head of Quality - Manufacturing','companyName':'Device Maker',
         'location':'Chennai','portalUrl':'https://www.naukri.com/job-listings-quality-123',
         'companyApplyUrl':'https://www.naukri.com/job-listings-quality-123',
         'description':'Lead electrical product and supplier quality.',
         'createdDate':'2026-10-04T00:00:00Z'}
    raw=to_raw(row)
    assert raw['source']=='Apify Naukri'
    assert raw['job_apply_link']==row['portalUrl']
    assert raw['apply_options']==[]
    assert to_raw({**row,'title':'Software Test Automation Engineer'}) is None
    assert to_raw({**row,'companyApplyUrl':'https://company.example/careers/123'})['apply_options']==[
        {'apply_link':'https://company.example/careers/123','is_direct':True}]
