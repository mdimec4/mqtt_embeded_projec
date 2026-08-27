"""Run the subscriber and roll back a failed automatic update."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from updater import BACKUP_DIR, STATE_FILE, send_update_email

APP_DIR = Path(__file__).resolve().parent


def restore_backup():
    for path in BACKUP_DIR.iterdir():
        shutil.copy2(path, APP_DIR / path.name)
    shutil.rmtree(BACKUP_DIR)
    STATE_FILE.unlink(missing_ok=True)


def main():
    pending = STATE_FILE.exists() and BACKUP_DIR.exists()
    startup_timeout = int(os.environ.get("UPDATE_STARTUP_TIMEOUT_SECONDS", "20"))
    while True:
        process = subprocess.Popen([sys.executable, str(APP_DIR / "subscriber.py")])
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            result = process.poll()
            if result is not None:
                if result == 75 and STATE_FILE.exists() and BACKUP_DIR.exists():
                    pending = True
                    break
                if pending:
                    restore_backup()
                    send_update_email("Subscriber update rolled back", "The updated subscriber failed during startup.")
                    pending = False
                    break
                return result
            time.sleep(1)
        else:
            if pending:
                STATE_FILE.unlink(missing_ok=True)
                shutil.rmtree(BACKUP_DIR)
                send_update_email("Subscriber update completed", "The subscriber restarted successfully after its update.")
                pending = False
            result = process.wait()
            if result == 75:
                pending = STATE_FILE.exists() and BACKUP_DIR.exists()
                continue
            return result


if __name__ == "__main__":
    raise SystemExit(main())
