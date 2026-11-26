-- 0049: the categorizer asks the model directly. The shipped seed rules and the run table of the
-- old batch categorizer go; user and llm rules stay.
DELETE FROM category_rules WHERE source = 'seed';
ALTER TABLE category_rules DROP CONSTRAINT category_rules_source_check;
ALTER TABLE category_rules ADD CONSTRAINT category_rules_source_check
    CHECK (source IN ('user', 'llm'));
DROP TABLE categorize_runs;
