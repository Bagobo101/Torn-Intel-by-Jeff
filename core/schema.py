from utils.logger import Logger


class SchemaBuilder:

    def __init__(self, database, logger):

        self.db = database
        self.logger = logger

    def create(self, model):

        columns = []

        for name, field in model.fields().items():

            columns.append(
                field.build(name)
            )

        sql = f"""
        CREATE TABLE IF NOT EXISTS
        {model.table_name}
        (
            {",".join(columns)}
        )
        """

        self.logger.info(
            f"Ensuring table {model.table_name}"
        )

        self.db.execute(sql)
        self.db.commit()

        self.ensure_columns(model)

    def ensure_columns(self, model):
        """Add any missing columns to an existing table."""
        try:
            existing_cols = {
                str(row["name"]).lower()
                for row in self.db.select(f"PRAGMA table_info({model.table_name})")
            }
        except Exception:
            return

        for name, field in model.fields().items():
            if name.lower() not in existing_cols:
                col_def = field.build(name)
                # In SQLite ALTER TABLE ADD COLUMN cannot contain PRIMARY KEY
                col_sql = col_def.replace("PRIMARY KEY", "").strip()
                try:
                    self.db.execute(f"ALTER TABLE {model.table_name} ADD COLUMN {col_sql}")
                    self.db.commit()
                    self.logger.info(f"Added column '{name}' to {model.table_name}")
                except Exception as exc:
                    self.logger.warning(f"Could not add column '{name}' to {model.table_name}: {exc}")