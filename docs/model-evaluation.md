# Model choice and cost assumptions

## Comparison process

The first version defaults to `google/gemini-3.8-flash`. Before settling on the production model:

1. Upload the real resume and correct the extracted role labels.
2. Collect at least 20 varied, current jobs: plausible R&D/quality/consulting roles, adjacent industry roles, and clearly unsuitable jobs.
3. Give each job a human label from 0 (irrelevant) to 3 (strong fit), without showing those labels to the model.
4. Run Gemini 3.8 Flash, GPT-5.4 Mini and GPT-5.4 on the same profile, descriptions, prompt and job set.
5. Inspect the top five, the factual basis of each explanation, whether useful adjacent roles survive, and whether proposed resume wording stays in the writer's voice. A model that invents experience fails even if its ranking is high.
6. Choose the least expensive model that meets the quality bar. GPT-5.4 can be selected if it materially improves the shortlist or wording.

`python -m app.evaluate --input evaluation.json --output comparison.json` runs the ranking comparison. The input file must have `profile` (string), `jobs` (list with `id`, `title`, `company`, `location`, `work_type`, `description`) and optional `human_labels` (map from job ID to 0–3). The script reports NDCG@5, rankings, explanations and recorded API cost.

## Approximate OpenRouter credit usage

Prices checked 24 September 2026: [Gemini 3.8 Flash](https://openrouter.ai/google/gemini-3.8-flash), [GPT-5.4 Mini](https://openrouter.ai/openai/gpt-5.4-mini), [GPT-5.4](https://openrouter.ai/openai/gpt-5.4). The ECB reference rate on 22 September 2026 was about USD 1.1463 per EUR, so EUR 8 is approximately USD 9.17 before any conversion or platform costs. [ECB rate notice](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX%3AC%2F2026%2F04613)

For a **representative run that analyzes up to 40 candidate jobs**, assume 100,000 input tokens and 5,000 output tokens across the batches. The real count varies with resume and job-description length. Search API requests, resume extraction, question generation, resume edits and email delivery are excluded from this LLM-only estimate.

| Model | USD per million input/output tokens | Approx. USD per run | Runs from EUR 8 |
|---|---:|---:|---:|
| Gemini 3.8 Flash | 0.75 / 3.75 | 0.094 | about 97 |
| GPT-5.4 Mini | 0.75 / 4.50 | 0.098 | about 94 |
| GPT-5.4 | 2.50 / 15.00 | 0.325 | about 28 |

The app logs actual input/output tokens and provider cost after every call. JSearch is separately billed by request; the configured maximum is 20 requests per run, which would be at most USD 0.10 at its current pay-as-you-go price if all 20 are charged. [JSearch pricing](https://www.openwebninja.com/api/jsearch)
