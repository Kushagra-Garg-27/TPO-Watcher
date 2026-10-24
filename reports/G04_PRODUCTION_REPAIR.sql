-- G04_PRODUCTION_REPAIR.sql
-- Production SQLite Data Quality Repair for Mastercard ID 5610
--
-- Background: Initial baseline ingestion left is_active as NULL.
-- The deactivate_missing_companies guard only matches WHERE is_active='True'.
-- As a result, ID 5610 remains an unnormalized permanent orphan.
--
-- Safety: This script affects exactly one historical baseline record.
-- It does not modify active subscribers, deliveries, or current detections.

BEGIN TRANSACTION;

-- 1. Check existing record
SELECT id, company, is_active, first_seen_at 
FROM companies 
WHERE id = '5610';

-- 2. Update is_active to string 'False' (inactive baseline company)
UPDATE companies 
SET is_active = 'False',
    updated_at = datetime('now')
WHERE id = '5610' 
  AND (is_active IS NULL OR is_active = 'None');

-- 3. Verify update
SELECT id, company, is_active, updated_at 
FROM companies 
WHERE id = '5610';

COMMIT;
