"""Shared role semantics and vacancy-location checks (never company HQ text)."""
import re
from datetime import datetime,timezone,timedelta

ROLE_GROUPS = [
 ('research development', ['R&D','Research and Development','Research Development']),
 ('quality', ['Head of Quality','Quality Management Head','Quality Director','Quality Assurance Manager']),
 ('hardware', ['Hardware Engineering Head','Hardware Engineering Manager','Hardware Design Lead']),
 ('automation', ['Automation Consultant','Industrial Automation Lead','Controls Engineering Manager']),
 ('product development', ['Product Development Director','Product Development Manager','Engineering Director']),
 ('consultant', ['Engineering Consultant','Technical Advisor','Technical Consultant']),
]
SYNONYMS={'plc':'programmable logic controller','scada':'supervisory control data acquisition',
 'qa':'quality assurance','qc':'quality control','rd':'research development','rnd':'research development',
 'electricals':'electrical','electronics':'electronic','robotics':'robotic',
 'bangalore':'bengaluru','madras':'chennai','kovai':'coimbatore','tn':'tamil nadu'}
GENERIC={'head','director','manager','senior','lead','leader','chief','of','and','the','for','in','job','jobs','team','management','india','engineering','engineer'}

def normalize(value):
    value=re.sub(r'r\s*&\s*d\b','research development',str(value).lower())
    value=re.sub(r'[^a-z0-9]+',' ',value)
    return ' '.join(SYNONYMS.get(w,w) for w in value.split())

def words(value):return set(normalize(value).split())

def title_matches(expected,actual,location=''):
    ignored=GENERIC | words(location)
    wanted=words(expected)-ignored;got=words(actual)-ignored
    if not wanted or not got:return False
    # Generic seniority can never compensate for an unrelated function.
    for group in [{'research','development'},{'quality'},{'sales'},{'finance'},{'human','resources'}]:
        if group <= wanted and not group <= got:return False
    return len(wanted&got)/len(wanted)>=0.75 and len(wanted&got)/len(got)>=0.5

def role_variants(role):
    value=normalize(role);result=[role]
    for key,variants in ROLE_GROUPS:
        if key in value or any(normalize(v)==value for v in variants):
            result.extend(variants)
    if 'research development' in value:
        result.extend(['Research and Development Manager','R&D Head','Research and Development Director'])
    return list(dict.fromkeys(result))

def location_matches(actual,requested,remote=False):
    actual=normalize(actual); actual_words=set(actual.split())
    for place in requested:
        p=normalize(place); pw=set(p.split())
        if 'remote' in pw:
            if remote and ('india' in actual_words or 'worldwide' in actual_words):return True
            continue
        pw-= {'india'}
        if not pw:
            if 'india' in actual_words:return True
        elif pw <= actual_words:return True
    return False

def recent(value,days):
    if not value:return False
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        dt=dt.replace(tzinfo=dt.tzinfo or timezone.utc)
        now=datetime.now(timezone.utc)
        return now-timedelta(days=days)<=dt<=now+timedelta(days=1)
    except (ValueError,TypeError):return False
