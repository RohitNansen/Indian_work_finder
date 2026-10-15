# Expanded employer discovery

The search keeps the candidate's editable roles, skills, work types and recent-posting choice. Its default places now include South India, resolved into individual cities for source queries and checked against the actual vacancy location. Medical and life-science roles remain the first preference; suitable textile and other engineering roles remain eligible.

Each run now checks the public Lever and Greenhouse employer feeds, a rotating set of saved career-site hints and verified company sites, targeted employer-site web search, two small Naukri searches, and up to 36 JSearch requests. Every returned company can enrich the persistent company inventory. Unverified site hints are used only to discover possible openings; they do not become trusted application links. Individual vacancies must still pass employer title, hiring company, location, description, availability and posting-date checks before ranking.

The search caps are 90 employer vacancy checks, 16 employer web lookups and 15 minutes per run. The requested number of jobs remains the output cap. The app's atomic $30 monthly paid-API stop and owner notification remain in force. The admin API use page reports recorded provider spending; provider invoices can differ from those estimates.

The official career-site seed list is in `app/company_memory.py`. These are discovery starting points, not verified job claims. They rotate across runs, and the list of companies from searches grows independently of the seed list.
