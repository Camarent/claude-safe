"""cs-qrun `--postgres`: the flag name and the pipeline.json keys that tell qrun how to start its own Postgres instance."""

QRUN_PG_FLAG = "--postgres"  # cs-qrun flag: start a scratch Postgres for this command and set its URL in the child env
QRUN_PG_VAR_KEY = "qrun_pg_var"  # pipeline.json key: env var name the command reads the instance URL from (e.g. DATABASE_URL)
QRUN_PG_START_KEY = "qrun_pg_start"  # pipeline.json key: start command; it runs in a scratch dir, prints the URL as its last stdout line
QRUN_PG_DIR_ENV = "QRUN_PG_DIR"  # env var the start command gets: the instance dir qrun removes when the command ends
