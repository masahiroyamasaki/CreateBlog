"""fix_stuck.py — processing/creating 状態で止まったネタ・記事を一括リセットする"""
from app import app
from models import db, TopicQueue, Post

with app.app_context():
    stuck_topics = TopicQueue.query.filter_by(status="processing").all()
    print(f"止まっているネタ: {len(stuck_topics)} 件")
    for t in stuck_topics:
        print(f"  [topic {t.id}] {t.title[:40]}")
        if t.generated_post_id:
            p = Post.query.get(t.generated_post_id)
            if p and p.status == "creating":
                db.session.delete(p)
                print(f"    → 作成中の記事 (post {p.id}) を削除")
        t.status = "pending"
        t.generated_post_id = None

    orphan_posts = Post.query.filter_by(status="creating").all()
    print(f"孤立した作成中記事: {len(orphan_posts)} 件")
    for p in orphan_posts:
        print(f"  [post {p.id}] {p.title[:40]} → failed に変更")
        p.status = "failed"

    db.session.commit()
    print("\n完了。すべてリセットされました。")
