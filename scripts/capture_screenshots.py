import subprocess
import time
from pathlib import Path

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BASE_URL = "http://127.0.0.1:8000/app"
OUT_DIR = Path(r"C:\Users\avani_mittal\Desktop\b2b outreach sys\docs\screenshots")
OUT_DIR.mkdir(parents=True, exist_ok=True)

screens = [
    ("01_overview.png", f"{BASE_URL}#overview"),
    ("02_needs_review.png", f"{BASE_URL}#review"),
    ("03_contacts.png", f"{BASE_URL}#contacts"),
    ("04_queue.png", f"{BASE_URL}#queue"),
    ("05_suppression.png", f"{BASE_URL}#suppression"),
    ("06_audit_log.png", f"{BASE_URL}#audit"),
    ("07_system_config.png", f"{BASE_URL}#config"),
    ("08_draft_edit_modal.png", f"{BASE_URL}#review?draft=212"),
    ("09_unsuppress_modal.png", f"{BASE_URL}#suppression?modal=unsuppress"),
    ("10_csv_modal.png", f"{BASE_URL}#contacts?modal=csv"),
]

for filename, url in screens:
    out_file = str(OUT_DIR / filename)
    cmd = [
        EDGE_PATH,
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        "--window-size=1440,900",
        "--virtual-time-budget=4000",
        f"--screenshot={out_file}",
        url,
    ]
    print(f"Capturing {filename} from {url}...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(f"Done: {filename}, returncode={res.returncode}")
    time.sleep(1)

print("All screenshots captured successfully.")
