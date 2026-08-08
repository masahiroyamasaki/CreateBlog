"""migrate_bank_fields.py — bank_account を5フィールドに分割するDBマイグレーション"""
from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        # 新カラム追加（既存なら無視）
        cols = [
            ("bank_name",           "VARCHAR(100) NOT NULL DEFAULT ''"),
            ("bank_branch",         "VARCHAR(100) NOT NULL DEFAULT ''"),
            ("bank_account_type",   "VARCHAR(20)  NOT NULL DEFAULT ''"),
            ("bank_account_number", "VARCHAR(30)  NOT NULL DEFAULT ''"),
            ("bank_account_holder", "VARCHAR(100) NOT NULL DEFAULT ''"),
        ]
        for col_name, col_def in cols:
            try:
                conn.execute(db.text(f"ALTER TABLE designers ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"追加完了: {col_name}")
            except Exception as e:
                if "Duplicate column name" in str(e) or "already exists" in str(e):
                    print(f"スキップ（既存）: {col_name}")
                else:
                    print(f"エラー: {col_name} — {e}")

        # 旧 bank_account カラムを削除
        try:
            conn.execute(db.text("ALTER TABLE designers DROP COLUMN bank_account"))
            conn.commit()
            print("削除完了: bank_account")
        except Exception as e:
            if "Can't DROP" in str(e) or "check that column" in str(e):
                print("スキップ（既に削除済み）: bank_account")
            else:
                print(f"エラー: bank_account 削除 — {e}")

    print("マイグレーション完了")
