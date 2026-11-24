-- Count the candidate pool per stratum, before settling the quotas in pull_sample.sql.
--
--   Run with sampling/run_in_django.py (see readme.md).
--
-- `cand` and `labeled` must match pull_sample.sql. Change both files together.

WITH cand AS (
    SELECT
        ct.id                AS court_id,
        ct.jurisdiction,
        c.case_name,
        c.case_name_full,
        d.case_name          AS docket_case_name,
        d.case_name_full     AS docket_case_name_full,
        d.nature_of_suit,
        o.extracted_by_ocr,
        o.llm_input
    FROM search_opinioncluster c
    JOIN search_docket d  ON d.id = c.docket_id
    JOIN search_court  ct ON ct.id = d.court_id
    JOIN LATERAL (
        SELECT o.extracted_by_ocr, left(o.plain_text, 2000) AS llm_input
        FROM search_opinion o
        WHERE o.cluster_id = c.id
        ORDER BY o.id
        LIMIT 1
    ) o ON true
    WHERE c.source = 'G'
      AND c.date_created >= '2025-10-15'
      AND o.llm_input <> ''
),
labeled AS (
    SELECT cand.*,
        CASE
            WHEN jurisdiction = 'FB'                                 THEN 'bankruptcy'
            WHEN nature_of_suit ILIKE '%social security%'
              OR llm_input ILIKE '%commissioner of social security%' THEN 'social_security'
            WHEN docket_case_name ~* '^in re\M'
              OR llm_input ~* '\mconsolidated\M'                     THEN 'in_re_or_consolidated'
            WHEN length(case_name_full) >= 250                       THEN 'long_caption'
            WHEN case_name = docket_case_name
             AND case_name_full = docket_case_name_full              THEN 'name_unchanged'
            ELSE 'general'
        END AS stratum
    FROM cand
)
SELECT stratum,
       count(*)                                  AS clusters,
       count(DISTINCT court_id)                  AS courts,
       count(*) FILTER (WHERE extracted_by_ocr)  AS ocr_flagged,
       count(*) FILTER (WHERE jurisdiction = 'FB') AS bankruptcy_court,
       count(*) FILTER (WHERE case_name = docket_case_name
                          AND case_name_full = docket_case_name_full) AS names_equal_docket,
       count(*) FILTER (WHERE docket_case_name_full = '') AS docket_full_name_empty
FROM labeled
GROUP BY stratum
ORDER BY clusters DESC;
