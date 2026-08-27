"""Poll GitHub Releases and stage verified subscriber updates."""

import base64
import hashlib
import hmac
import json
import logging
import os
import shutil
import smtplib
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from urllib.error import HTTPError, URLError
from email.message import EmailMessage
from pathlib import Path
from threading import Thread
from time import sleep

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

LOGGER = logging.getLogger(__name__)
APP_DIR = Path(__file__).resolve().parent
STATE_FILE = APP_DIR / ".update-state.json"
BACKUP_DIR = APP_DIR / ".update-backup"
VERSION_FILE = APP_DIR / "version.txt"
RESTART_FOR_UPDATE = 75


def configured(name, default=""):
    return os.environ.get(name, default).strip()


def send_update_email(subject, body):
    host = configured("UPDATE_SMTP_HOST")
    recipient = configured("UPDATE_EMAIL_TO")
    if not host or not recipient:
        return
    message = EmailMessage()
    message["From"] = configured("UPDATE_EMAIL_FROM", recipient)
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    port = int(configured("UPDATE_SMTP_PORT", "587"))
    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls(context=context)
        username = configured("UPDATE_SMTP_USERNAME")
        password = os.environ.get("UPDATE_SMTP_PASSWORD")
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)


def current_version():
    return VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "0.0.0"


def github_json(url):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "mqtt-embedded-subscriber"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def check_github(repository):
    try:
        return github_json(f"https://api.github.com/repos/{repository}/releases/latest")
    except HTTPError as error:
        if error.code == 404:
            LOGGER.info("No GitHub release is available for %s", repository)
            return None
        raise


def download(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": "mqtt-embedded-subscriber"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def verify_archive(archive, checksum, signature):
    expected = checksum.read_text(encoding="utf-8").split()[0].lower()
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise ValueError("subscriber archive checksum does not match")
    public_key = configured("UPDATE_PUBLIC_KEY")
    if not public_key:
        raise ValueError("UPDATE_PUBLIC_KEY is not configured")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
    key.verify(base64.b64decode(signature.read_text(encoding="utf-8")), archive.read_bytes())


def extract_safely(archive, destination):
    with tarfile.open(archive, "r:gz") as package:
        for member in package.getmembers():
            target = (destination / member.name).resolve()
            if not str(target).startswith(str(destination.resolve()) + os.sep):
                raise ValueError("update archive contains an unsafe path")
        package.extractall(destination)


def stage_update(version, archive):
    with tempfile.TemporaryDirectory(dir=APP_DIR) as temporary:
        staging = Path(temporary)
        extract_safely(archive, staging)
        source = staging / "subscriber"
        if not (source / "subscriber.py").exists() or not (source / "version.txt").exists():
            raise ValueError("update archive must contain subscriber/subscriber.py and subscriber/version.txt")
        subprocess.run([sys.executable, "-m", "py_compile", str(source / "subscriber.py")], check=True)
        if BACKUP_DIR.exists():
            shutil.rmtree(BACKUP_DIR)
        BACKUP_DIR.mkdir()
        for name in ("subscriber.py", "updater.py", "version.txt"):
            path = APP_DIR / name
            if path.exists():
                shutil.copy2(path, BACKUP_DIR / name)
        for name in ("subscriber.py", "updater.py", "version.txt"):
            shutil.copy2(source / name, APP_DIR / name)
        STATE_FILE.write_text(json.dumps({"version": version}), encoding="utf-8")


def check_once():
    repository = configured("UPDATE_GITHUB_REPOSITORY", "mdimec4/mqtt_embeded_projec")
    release = check_github(repository)
    if release is None:
        return
    version = release["tag_name"].removeprefix("v")
    if version == current_version():
        return
    assets = {asset["name"]: asset["browser_download_url"] for asset in release["assets"]}
    required = ("subscriber.tar.gz", "subscriber.tar.gz.sha256", "subscriber.tar.gz.sig")
    if any(name not in assets for name in required):
        raise ValueError("release is missing subscriber archive, checksum, or signature")
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        archive = directory / required[0]
        checksum = directory / required[1]
        signature = directory / required[2]
        for name, path in zip(required, (archive, checksum, signature)):
            download(assets[name], path)
        verify_archive(archive, checksum, signature)
        send_update_email("Subscriber update starting", f"Updating subscriber from {current_version()} to {version}.")
        stage_update(version, archive)
    os._exit(RESTART_FOR_UPDATE)


def run():
    if configured("UPDATE_ENABLED", "false").lower() != "true":
        return
    interval = int(configured("UPDATE_INTERVAL_SECONDS", "3600"))
    while True:
        try:
            check_once()
        except (TimeoutError, URLError, OSError) as error:
            LOGGER.warning("Automatic update check unavailable: %s", error)
        except Exception:
            LOGGER.exception("Automatic update check failed")
        sleep(interval)


def start():
    Thread(target=run, name="subscriber-updater", daemon=True).start()
