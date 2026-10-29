from app.db.session import get_db_session
from app.workers.a2_personalization.service import run_a2_for_contact

def inspect():
    with get_db_session() as db:
        for cid in [1, 3]:
            s = run_a2_for_contact(cid, db)
            print(f"Contact {cid}: status={s.get('status')}")
            print(f"  rulebook: {s.get('rulebook_errors')}")
            print(f"  grounding: {s.get('grounding_errors')}")
            print(f"  sameness: {s.get('sameness_errors')}")
            print(f"  word count: {len(s.get('draft_body', '').split())}")
            print(f"  body: {s.get('draft_body')}\n")

if __name__ == "__main__":
    inspect()
