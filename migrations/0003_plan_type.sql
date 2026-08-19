-- A plan's kind, kept apart from its lifecycle. `status` answers whether a
-- plan is still to be executed; `type` answers what the record is. They were
-- one column until now, which is why 'template' was a status: a plan that is
-- never completed because it is not a plan.
--
-- Left unconstrained, like `status`. The vocabulary is written by agents, and
-- a CHECK would turn a new kind of record into a schema migration.

-- +goose Up

ALTER TABLE plans
ADD COLUMN IF NOT EXISTS type VARCHAR(50) NOT NULL DEFAULT 'plan';

-- The one status that was never a lifecycle. It becomes a type, and the row
-- gets a lifecycle back: a template is available until it is archived.
UPDATE plans SET type = 'template', status = 'active'
WHERE status = 'template';

-- The dashboard lists plans across every project, narrowed by kind and
-- lifecycle; idx_plans_project_status only leads with the project.
CREATE INDEX IF NOT EXISTS idx_plans_type_status ON plans (type, status);

-- +goose Down
DROP INDEX IF EXISTS idx_plans_type_status;

-- Put the templates back where they were, or the rollback loses the
-- distinction the column carried.
UPDATE plans SET status = 'template'
WHERE type = 'template';

ALTER TABLE plans
DROP COLUMN IF EXISTS type;
