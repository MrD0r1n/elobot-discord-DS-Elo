-- set_all_players_active.sql

BEGIN TRANSACTION;

UPDATE elo_data
SET inactive = 0;

COMMIT;
