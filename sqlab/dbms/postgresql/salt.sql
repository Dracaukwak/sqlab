CREATE FUNCTION salt_{i:03d}(x NUMERIC) RETURNS BIGINT AS 'SELECT $1::BIGINT # {y};' LANGUAGE sql;
