-- A teacher reading signal_consent through PostgREST saw `updated_by`/`*_revoked_by`, a parent's id,
-- where the rule is "a role, never an identity". The backend serves teachers the role and nothing
-- in the frontend reads this table. The student's and the parent's own-row reads stay.

DROP POLICY IF EXISTS "consent: teacher read" ON "public"."signal_consent";

NOTIFY pgrst, 'reload schema';
