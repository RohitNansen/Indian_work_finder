# Job source evaluation — 4 October 2026

## Decision for this candidate

Keep JSearch as the current broad discovery feed, use the employer vacancy page as the source of truth, and add a second, independent feed only after a measured pilot. Paying OpenWebNinja for a higher tier buys more calls, not a different job catalogue: its pricing page says all plans use the same endpoints and data. The observed bottleneck is verified, direct, relevant vacancies, not the number of indexed rows.

Two capped tests with the actual profile (Chennai/Tamil Nadu/Bengaluru/Remote India, 30 days, 10 requested) retrieved 90 and 79 candidate listings respectively. Forty employer vacancy checks in each produced one curated role per run. The selected employer vacancies were Hitachi Energy's R&D Team Manager in Chennai and Cisco's Leader, Hardware Engineering in Bangalore. The tests cost about US$0.11 and US$0.12 respectively in indexed searches and model calls. Both used the same JSearch catalogue plus a small number of employer web searches, so these are diagnosis samples, not a head-to-head vendor benchmark. Results change daily, and a requested count is a ceiling rather than a guaranteed number.

## Options

| Provider | Current public price | What it adds | Limitation for this use |
|---|---|---|---|
| [OpenWebNinja JSearch](https://www.openwebninja.com/api/jsearch) | Free 200 requests/month; pay as you go US$0.005/request; Pro US$25/month for 10,000 requests | Existing integration, India search, public Google for Jobs listings, dates and apply options | Higher tier increases volume only. Many apply options are intermediaries; original employer vacancy still needs independent verification. |
| [Serper](https://serper.dev/) | 2,500 free queries; US$50 top-up for 50,000 queries, valid six months | Low-cost Google web search for finding original employer careers pages, without a monthly fee | A web search API, not a job feed. It cannot itself certify that a vacancy is current, at the requested city, or directly applyable. |
| [TheirStack](https://theirstack.com/en/pricing?tab=api) | US$49/month for 1,500 API credits; [one credit per job returned](https://theirstack.com/en/docs/api-reference/jobs/search_jobs_v1) | Different structured feed drawn from career sites, ATSs and boards, with posted date and closed-job filters | Charge is per returned job rather than per request. India coverage and direct-link yield for these niche senior roles have not yet been measured. |
| [SerpApi Google Jobs](https://serpapi.com/google-jobs-api) | [Free 250 searches/month; Starter US$25/month for 1,000](https://serpapi.com/pricing) | Another Google Jobs extraction route | Considerable source overlap with JSearch; no reason to expect a better India role catalogue solely from a paid plan. |
| [JobDataAPI](https://jobdataapi.com/) | Access Lite US$345/month | ATS-focused feed | Too expensive for a single candidate until cheaper sources have been evaluated. |

## Pilot before a subscription

Try Serper's free queries first as a separate employer-site discovery aid; it may help locate direct application pages. Measure 50 to 100 returned candidates against the same profile and verify original employer title, vacancy city, posting date, open status, relevance, and cost. If coverage remains poor, test TheirStack with a small, capped batch before selecting a US$49/month plan. A paid JSearch tier is appropriate only when request quota, rather than verified yield, becomes the bottleneck. No new subscription has been purchased.

The app currently accepts only verified employer pages with machine-readable JobPosting details that identify one vacancy, its title, location and description. This deliberately rejects many potentially good pages that lack structured data. Expanding trusted employer or ATS adapters can raise coverage while retaining title and location safeguards.
