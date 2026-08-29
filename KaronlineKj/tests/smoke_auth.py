"""Vérification de fumée : comptes + sessions authentifiées (serveur local).

Usage : python _smoke_auth.py
Lance lan_server.py sur un port de test avec dossier données/dossier musique
temporaires, déroule le scénario complet puis nettoie.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"
HERE = Path(__file__).resolve().parent.parent
EMAIL = "Smoke@Test.Example"
PASSWORD = "motdepasse-test-1"

# Affichage robuste quand la sortie est redirigée par PowerShell (cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

results = []


def check(name, condition, extra=""):
    results.append((name, bool(condition), extra))
    print(("PASS " if condition else "FAIL ") + name +
          ((" — " + str(extra)) if extra else ""))


def call(path, payload=None, token=None):
    body = None
    headers = {}
    method = "GET"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except Exception:
            data = {}
        return exc.code, data


def wait_port():
    import socket
    for _ in range(40):
        probe = socket.socket()
        probe.settimeout(0.3)
        try:
            probe.connect(("127.0.0.1", PORT))
            probe.close()
            return True
        except OSError:
            time.sleep(0.25)
        finally:
            probe.close()
    return False


def main():
    data_dir = tempfile.mkdtemp(prefix="kl_data_")
    lib_dir = Path(tempfile.mkdtemp(prefix="kl_lib_"))
    for name in ("Queen - Don't Stop Me Now.mp4",
                 "Adele - Rolling In The Deep.mp4"):
        (lib_dir / name).write_bytes(b"")

    env = dict(os.environ, KL_DATA_DIR=data_dir,
               KL_DEBUG_VERIFY_CODE="1",
               PYTHONIOENCODING="utf-8")
    server = subprocess.Popen(
        [sys.executable, str(HERE / "lan_server.py"),
         "--port", str(PORT), "--library", str(lib_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=env,
    )
    try:
        if not wait_port():
            print("FAIL serveur non démarré")
            sys.exit(2)

        status, payload = call("/auth/register", {
            "email": EMAIL, "password": PASSWORD,
            "card_brand": "Visa", "card_last4": "4242"})
        code = payload.get("code")
        check("register → envoi code + verification required",
              status == 202 and payload.get("verification_required") is True and
              isinstance(code, str) and len(code) == 6,
              payload)

        status, payload = call("/auth/login",
                               {"email": EMAIL, "password": PASSWORD})
        check("login avant vérification → 403", status == 403,
              payload.get("error"))

        status, payload = call("/auth/verify", {
            "email": EMAIL, "code": code})
        token = payload.get("token", "")
        check("verification OK → jeton reçu", status == 200 and token,
              payload)

        status, payload = call("/auth/register", {
            "email": EMAIL.lower(), "password": "xxxxxxxxx"})
        check("register doublon → 409", status == 409,
              payload.get("error"))

        status, payload = call(
            "/auth/login",
            {"email": EMAIL, "password": "mauvais-code"})
        check("login erroné → 401", status == 401,
              payload.get("error"))

        status, payload = call(
            "/auth/login",
            {"email": EMAIL.lower(), "password": PASSWORD})
        login_token = payload.get("token", "")
        check("login OK → email normalisé", status == 200 and
              payload.get("email") == EMAIL.lower() and login_token,
              status)

        status, payload = call("/auth/me")
        check("me sans jeton → 401", status == 401,
              payload.get("error"))
        status, payload = call("/auth/me", token=token)
        check("me avec jeton → carte masquée", status == 200 and
              payload.get("card_label") == "Visa ••••4242",
              payload.get("card_label"))

        status, payload = call(
            "/session/register",
            {"name": "soiree-smoke",
             "host_url": "https://abc.trycloudflare.com"})
        check("session sans compte → 401 AUTH REQUIRED", status == 401,
              payload.get("error"))

        status, payload = call(
            "/session/register",
            {"name": "Soiree-Smoke",
             "host_url": "https://abc.trycloudflare.com"},
            token=token)
        check("session avec jeton → owner rattache", status == 200 and
              payload.get("owner") == EMAIL.lower(), payload)

        status, payload = call("/session/soiree-smoke")
        check("résolution publique de session", status == 200 and
              payload.get("host_url") == "https://abc.trycloudflare.com",
              payload.get("host_url"))

        status, payload = call("/catalogue")
        check("catalogue public → 2 titres",
              status == 200 and len(payload) == 2, len(payload))

        status, _ignored = call("/auth/logout", {}, token=token)
        status2, payload2 = call("/auth/me", token=token)
        check("logout puis me → 401",
              status == 200 and status2 == 401, payload2.get("error"))

        failures = [name for name, ok, _x in results if not ok]
        print(f"\n{len(results) - len(failures)}/{len(results)} tests passés")
        if failures:
            print("ÉCHECS :", ", ".join(failures))
            sys.exit(1)
    finally:
        server.terminate()
        try:
            out = server.communicate(timeout=4)[0]
            tail = out.decode("utf-8", errors="ignore")[-300:]
            print("--- log serveur ---\n" + tail)
        except Exception:
            server.kill()


if __name__ == "__main__":
    main()
