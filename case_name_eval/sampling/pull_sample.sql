-- Pull the stratified sample for the case-name evaluation set (courtlistener#8325).
--
-- Source: CourtListener replica. Output: CSV on stdout, one row per cluster.
--
--   Run with sampling/run_in_django.py (see readme.md).
--
-- The candidate pool and stratum rules (CTEs `cand` and `labeled`) are repeated
-- in stratum_counts.sql. Change both files together.

COPY (
WITH params AS (
    SELECT 'casename-eval-v1'::text AS seed,   -- change to draw a different sample
           2000                     AS n_total
),

-- Every cluster created by recap_document_into_opinions after the LLM step shipped.
cand AS (
    SELECT
        c.id                 AS cluster_id,
        o.id                 AS opinion_id,
        d.id                 AS docket_id,
        ct.id                AS court_id,
        ct.jurisdiction,
        c.date_created,
        c.case_name,
        c.case_name_full,
        d.case_name          AS docket_case_name,
        d.case_name_full     AS docket_case_name_full,
        d.docket_number,
        d.nature_of_suit,
        o.extracted_by_ocr,
        o.sha1,
        o.llm_input,
        md5(c.id::text || p.seed) AS h          -- stable pseudo-random order
    FROM search_opinioncluster c
    JOIN search_docket d  ON d.id = c.docket_id
    JOIN search_court  ct ON ct.id = d.court_id
    JOIN LATERAL (
        SELECT o.id, o.extracted_by_ocr, o.sha1,
               left(o.plain_text, 2000) AS llm_input
        FROM search_opinion o
        WHERE o.cluster_id = c.id
        ORDER BY o.id
        LIMIT 1                                 -- the task reads the first sub-opinion
    ) o ON true
    CROSS JOIN params p
    WHERE c.source = 'G'                        -- ClusterSources.RECAP
      AND c.date_created >= '2025-10-15'        -- first day of the current task code
      AND o.llm_input <> ''
),

-- One stratum per cluster. Order = priority: the first matching rule wins.
-- OCR is not a stratum: the flag is set on about two thirds of the pool.
-- It stays as an output column (extracted_by_ocr) for slicing results.
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
),

quota (stratum, n) AS (
    VALUES ('bankruptcy', 300), ('social_security', 300),
           ('in_re_or_consolidated', 250), ('long_caption', 250),
           ('name_unchanged', 250), ('general', 650)
),

-- Within a stratum, take one case per court in turn, so no single court dominates.
by_court AS (
    SELECT labeled.*,
           row_number() OVER (PARTITION BY stratum, court_id ORDER BY h) AS court_rn
    FROM labeled
),
ranked AS (
    SELECT by_court.*,
           row_number() OVER (PARTITION BY stratum ORDER BY court_rn, h) AS rn
    FROM by_court
),

-- Fill each quota first. A stratum that runs short is topped up at random from the rest.
picked AS (
    SELECT r.*, (r.rn <= q.n) AS in_quota
    FROM ranked r
    JOIN quota q USING (stratum)
    ORDER BY (r.rn <= q.n) DESC, r.h
    LIMIT (SELECT n_total FROM params)
)

SELECT
    s.cluster_id,
    s.opinion_id,
    s.docket_id,
    rd.recap_document_id,
    s.court_id,
    s.jurisdiction,
    s.docket_number,
    s.nature_of_suit,
    s.date_created,
    s.extracted_by_ocr,
    s.stratum,
    s.in_quota,
    s.case_name                AS current_case_name,
    s.case_name_full           AS current_case_name_full,
    s.docket_case_name,
    s.docket_case_name_full,
    ev.case_name               AS pre_update_case_name,
    ev.case_name_full          AS pre_update_case_name_full,
    ev.pgh_created_at          AS first_update_at,
    s.llm_input,
    length(o.plain_text)       AS plain_text_length,
    o.plain_text
FROM picked s
JOIN search_opinion o ON o.id = s.opinion_id
-- Cluster row as it was before its first change (pghistory stores the old row).
LEFT JOIN LATERAL (
    SELECT e.case_name, e.case_name_full, e.pgh_created_at
    FROM search_opinionclusterevent e
    WHERE e.pgh_obj_id = s.cluster_id
    ORDER BY e.pgh_created_at
    LIMIT 1
) ev ON true
-- The RECAP document the opinion was imported from.
LEFT JOIN LATERAL (
    SELECT r.id AS recap_document_id
    FROM search_docketentry de
    JOIN search_recapdocument r ON r.docket_entry_id = de.id
    WHERE de.docket_id = s.docket_id
      AND r.sha1 = s.sha1
    LIMIT 1
) rd ON true
ORDER BY s.stratum, s.h
) TO STDOUT WITH (FORMAT csv, HEADER true);
