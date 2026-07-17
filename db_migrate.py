"""db_migrate.py — 起動時にSQLAlchemyモデルとMySQLを自動同期する"""
import logging
from sqlalchemy import inspect, text
from sqlalchemy.types import Enum as SAEnum

logger = logging.getLogger(__name__)


def auto_migrate(app, db):
    """
    モデルに存在するがDBに存在しないカラムを自動でADD COLUMN。
    ENUMカラムは値セットが不足していれば自動でMODIFY COLUMN。
    """
    with app.app_context():
        inspector = inspect(db.engine)

        for table_name, table in db.metadata.tables.items():
            if not inspector.has_table(table_name):
                continue

            existing_cols = {c["name"]: c for c in inspector.get_columns(table_name)}

            for col in table.columns:
                col_name = col.name

                # ── 新規カラムの追加 ──────────────────────────────────────────
                if col_name not in existing_cols:
                    try:
                        col_type = col.type.compile(db.engine.dialect)
                        nullable = "" if col.nullable else " NOT NULL"
                        # デフォルト値（callableは除外）
                        if (
                            col.default is not None
                            and col.default.arg is not None
                            and not callable(col.default.arg)
                        ):
                            default = f" DEFAULT '{col.default.arg}'"
                        elif col.nullable:
                            default = " DEFAULT NULL"
                        else:
                            default = ""
                        sql = f"ALTER TABLE `{table_name}` ADD COLUMN `{col_name}` {col_type}{nullable}{default}"
                        db.session.execute(text(sql))
                        db.session.commit()
                        logger.warning(f"[db_migrate] Added   {table_name}.{col_name}")
                    except Exception as e:
                        db.session.rollback()
                        logger.error(f"[db_migrate] Failed to add {table_name}.{col_name}: {e}")

                # ── ENUMカラムの値セット拡張 ──────────────────────────────────
                elif isinstance(col.type, SAEnum):
                    try:
                        existing_type = existing_cols[col_name]["type"]
                        existing_enums = set(getattr(existing_type, "enums", []))
                        expected_enums = set(col.type.enums)

                        if not expected_enums.issubset(existing_enums):
                            enum_values = ", ".join(f"'{e}'" for e in col.type.enums)
                            nullable = "" if col.nullable else " NOT NULL"
                            if (
                                col.default is not None
                                and col.default.arg is not None
                                and not callable(col.default.arg)
                            ):
                                default = f" DEFAULT '{col.default.arg}'"
                            else:
                                default = ""
                            sql = f"ALTER TABLE `{table_name}` MODIFY COLUMN `{col_name}` ENUM({enum_values}){nullable}{default}"
                            db.session.execute(text(sql))
                            db.session.commit()
                            logger.warning(f"[db_migrate] Updated ENUM {table_name}.{col_name} → {col.type.enums}")
                    except Exception as e:
                        db.session.rollback()
                        logger.error(f"[db_migrate] Failed to update ENUM {table_name}.{col_name}: {e}")
