# Search quality and usability — 3 October 2026

| ID | Requested fix | Acceptance / verification |
|---|---|---|
| Q01 | Correct employer vacancy | R&D can never match Sales through generic leadership words; title, employer, structured vacancy location, description and freshness checked. Conflicting headings and expired pages rejected. |
| Q02 | Broader relevant discovery | Related role titles and skill synonyms; cover all requested roles before repeating; bounded employer web discovery and provider requests; rank actual employer description. No invented/filler jobs. |
| Q03 | Location and recency | Actual vacancy city/state; Remote India eligibility. 3/7/30/60/90 day choices, date postfilter for API ranges over 30 days. Default count 10. |
| Q04 | Search progress | Live counts for searches, retrieved, checked and matched; current stage and bounded stopping reason. |
| U01 | Password-only login | Requested shared password 654321 grants candidate access only; no username field or profile account card; Activity remains protected. |
| U02 | Experience memory | Editable table with per-row save/delete and source; separate from latest resume extraction; replacement resume retains answers. |
| U03 | Alerts | Create disabled until the form changes and is valid. Existing alerts stay paused. |
| U04 | Questions | Three clear individual question cards with independent saves; grounded in verified employer description. |
| D01 | Reliable resume downloads | Preflight edits against source layout before approval; fit-supported edits only, original content retained with explicit notice for incompatible changes; both formats downloadable. Inspect full supplied resume and regression samples visually. |
| R01 | API recommendation | Official pricing, India coverage, direct employer URLs, freshness, request vs record charging; candid limits and sample-based buying recommendation. |

Status: implementation and two capped live tests complete; final deployment and smoke check pending. The previous verifier could drop the R&D token, overmatch generic leadership words, and accept a company-location mention. The new verifier checks the actual employer vacancy. The two live tests found one suitable verified role each from 90 and 79 retrieved candidates, so availability remains below the requested 10. See job-api-options-2026-10-04.md for sourcing options and measured limits. The original editable Word resume was supplied after initial PDF testing and its two former failure cases now render as three-page Word and PDF files with all tested edits.
