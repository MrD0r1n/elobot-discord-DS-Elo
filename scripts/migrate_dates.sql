BEGIN;

-- Append ' 00:00:00' only to YYYY-MM-DD strings
UPDATE match_data
SET "date" = "date" || ' 00:00:00'
WHERE "date" IS NOT NULL
  AND typeof("date") = 'text'
  AND length("date") = 10
  AND "date" GLOB '____-__-__';

COMMIT;
