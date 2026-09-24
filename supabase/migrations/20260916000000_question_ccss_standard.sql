-- The CCSS standard a question is scored against, from ccss_standards.ccss_for().
-- No default: NULL means "not resolved", never "no standard applies".

ALTER TABLE "public"."questions"
    ADD COLUMN IF NOT EXISTS "ccss_standard" "text";

COMMENT ON COLUMN "public"."questions"."ccss_standard" IS
    'CCSS code the question is scored against, resolved by ccss_standards.py '
    'from topic, scenario and grade. NULL means not resolved, not "none".';
