#!/usr/bin/env python3
"""Codex subscription proof only. Never contacts Buffer or writes to an outbox."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata
import urllib.error
import urllib.request

REPO = "joserobertoaraujofilho-art/robox"
SECRET = "CODEX_AUTH_JSON"
CONFIG = '''forced_login_method = "chatgpt"
cli_auth_credentials_store = "file"
sandbox_mode = "read-only"
approval_policy = "never"
web_search = "disabled"
[features]
shell_tool = false
[history]
persistence = "none"
'''
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
        "category": {"type": "string", "enum": ["atemporal", "requires_review"]},
        "requires_approval": {"type": "boolean"},
    },
    "required": ["text", "category", "requires_approval"],
}


def restricted_env() -> dict[str, str]:
    """Do not pass CI tokens, paid API keys or workflow commands to the model."""
    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "CODEX_HOME") if k in os.environ}


def github_guard() -> dict:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_EVENT_NAME") not in ("schedule", "workflow_dispatch")
            or event.get("repository", {}).get("private") is not True):
        raise RuntimeError("Requires the authorized PRIVATE repository, main branch and a trusted scheduled/manual run.")
    return event


def github_json(path: str, token: str, payload: dict | None = None, missing_ok: bool = False):
    if not token:
        raise RuntimeError("Required GitHub credential is not configured.")
    req = urllib.request.Request(
        "https://api.github.com/repos/" + REPO + "/" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method="PUT" if payload is not None else "GET",
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "robox-codex-proof"})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and missing_ok:
            return None
        raise RuntimeError("GitHub HTTP " + str(exc.code) + "; no credential values were logged.") from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("GitHub connection failed; inspect before retrying.") from None


def auth_ok(raw: str) -> dict:
    if len(raw.encode()) > 45000:
        raise ValueError("Auth file is too large for this storage path.")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid auth structure.")
    tokens = data.get("tokens")
    if (data.get("auth_mode") != "chatgpt" or data.get("OPENAI_API_KEY")
            or not isinstance(tokens, dict)
            or not all(isinstance(tokens.get(k), str) and tokens[k] for k in ("access_token", "refresh_token"))):
        raise ValueError("A ChatGPT-managed session with refresh token is required. Paid API auth is not accepted.")
    return data


def auth_home() -> Path:
    home = Path(os.environ["CODEX_HOME"])
    temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    if not home.resolve().is_relative_to(temp) or home.resolve() == temp:
        raise RuntimeError("CODEX_HOME must be a private directory inside RUNNER_TEMP.")
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.chmod(0o700)
    config = home / "config.toml"
    config.write_text(CONFIG)
    config.chmod(0o600)
    return home


def auth_prepare(restore: bool) -> None:
    github_guard()
    writer = os.environ.get("CODEX_SECRET_WRITER", "")
    github_json("actions/secrets/public-key", writer)
    home = auth_home()
    if restore:
        # Repository secrets are snapshotted at queue time. Do not reuse a snapshot
        # that another serialized run refreshed while this job waited.
        meta = github_json("actions/secrets/" + SECRET, writer)
        run_id = os.environ["GITHUB_RUN_ID"]
        if not run_id.isdigit():
            raise RuntimeError("Invalid workflow identifier.")
        run = github_json("actions/runs/" + run_id, os.environ.get("GITHUB_TOKEN", ""))
        parse = lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))
        if parse(meta["updated_at"]) > parse(run["created_at"]):
            raise RuntimeError("Queued run has stale auth; stop and use a newly queued run.")
        raw = os.environ.get(SECRET, "")
        auth_ok(raw)
        path = home / "auth.json"
        with path.open("x", encoding="utf-8") as stream:
            stream.write(raw)
        path.chmod(0o600)
    print("AUTH_PREPARED; no session or token content printed.")


def auth_save() -> None:
    github_guard()
    raw = (auth_home() / "auth.json").read_text()
    auth_ok(raw)
    token = os.environ.get("CODEX_SECRET_WRITER", "")
    if not token:
        raise RuntimeError("CODEX_SECRET_WRITER missing; refreshed auth was NOT persisted.")
    env = restricted_env()
    env["GH_TOKEN"] = token
    result = subprocess.run(["gh", "secret", "set", SECRET, "--repo", REPO],
                            input=raw, text=True, env=env, capture_output=True, timeout=40)
    if result.returncode:
        raise RuntimeError("Secret write-back failed. Do not reuse the original seed; a new login may be needed.")
    print("AUTH_SAVED_TO_ACTIONS_SECRET; credentials were not committed or uploaded as artifacts.")


def normalized(text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFC", text).casefold()))


def validate_post(post: dict, previous: list[str]) -> str:
    if not isinstance(post, dict) or set(post) != set(SCHEMA["required"]):
        raise ValueError("Unexpected response structure.")
    if post["category"] != "atemporal" or post["requires_approval"] is not False:
        raise ValueError("Content requires human review; not an eligible atemporal.")
    text = post["text"]
    if not isinstance(text, str) or text != text.strip() or not 1 <= len(text) <= 280:
        raise ValueError("Expected 1-280 characters, without outer whitespace.")
    if any(unicodedata.category(c).startswith("C") or ord(c) > 0xFFFF or 0x2600 <= ord(c) <= 0x27FF for c in text):
        raise ValueError("Control characters or emoji not allowed.")
    if re.search(r"[@#?!]|https?://|www\.|\d", text, re.I):
        raise ValueError("Mentions, hashtags, questions, links and numeric claims are not accepted in this test.")
    if not 1 <= len([s for s in re.split(r"[.;]+", text) if s.strip()]) <= 4:
        raise ValueError("Use one to four short sentences.")
    if re.search(r"\b(honestamente|francamente|sinceramente|galera|porra|caralho|merda)\b", text, re.I):
        raise ValueError("Vocabulary outside the persona.")
    if any(SequenceMatcher(None, normalized(text), normalized(old)).ratio() >= 0.86 for old in previous):
        raise ValueError("Repeated or overly similar text.")
    return text


def read_history(root: Path) -> tuple[list[str], int]:
    texts, scheduled = [], 0
    for folder in ("codex-trials", "outbox/atemporais", "outbox/aprovados"):
        for path in sorted((root / folder).glob("*.json"))[-80:]:
            data = json.loads(path.read_text())
            if isinstance(data.get("text"), str):
                texts.append(data["text"])
            if folder == "codex-trials" and data.get("event") == "schedule" and data.get("mode") == "dry_run":
                scheduled += 1
    return texts[-100:], scheduled


def generate(root: Path) -> None:
    github_guard()
    auth_ok((auth_home() / "auth.json").read_text())
    previous, scheduled = read_history(root)
    if os.environ["GITHUB_EVENT_NAME"] == "schedule" and scheduled >= 2:
        print("TWO_SCHEDULED_PROOFS_COMPLETE; no further model call or publication.")
        return
    persona = (root / "persona-seu-ze.md").read_text().split("## Operação")[0]
    prompt = ("Gere um único post ORIGINAL do Seu Zé, em português brasileiro.\n"
              "Somente atemporal, 1-4 frases, até 280 caracteres. Humor seco, elegante, não militante.\n"
              "Sem notícias, pessoas reais, alegações médicas, dados, números, preços ou citações.\n"
              "Sem palavrões, emojis, hashtags, menções, perguntas, exclamações ou pedido de engajamento.\n"
              "Não ataque grupos protegidos, não incite violência, não tenha crianças como alvo.\n"
              "Na dúvida, use category=requires_review e requires_approval=true.\n"
              "Não execute ferramentas, não leia arquivos, não publique, não mude configurações.\n"
              "Apenas devolva o JSON definido pelo schema. Faça revisão de estilo e segurança antes da resposta.\n"
              "PERSONA EDITORIAL:\n" + persona + "\nTEXTOS JÁ USADOS (não copiar):\n" + json.dumps(previous, ensure_ascii=False)
              + "\nIdentificador desta execução: " + os.environ["GITHUB_RUN_ID"])
    with tempfile.TemporaryDirectory(prefix="robox-text-") as work:
        schema = Path(work) / "schema.json"
        output = Path(work) / "post.json"
        schema.write_text(json.dumps(SCHEMA))
        result = subprocess.run(["codex", "exec", "--ephemeral", "--sandbox", "read-only",
                                 "--skip-git-repo-check", "--output-schema", str(schema),
                                 "--output-last-message", str(output), "-"],
                                input=prompt, text=True, cwd=work, env=restricted_env(),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        if result.returncode or not output.is_file():
            raise RuntimeError("Codex did not return a usable response. Check login, plan limits and CLI; no paid fallback or retry.")
        post = json.loads(output.read_text())
        text = validate_post(post, previous)
    report = {"mode": "dry_run", "generator": "codex_chatgpt_subscription",
              "run_id": os.environ["GITHUB_RUN_ID"], "event": os.environ["GITHUB_EVENT_NAME"],
              "source_commit": os.environ["GITHUB_SHA"], "generated_at": datetime.now(timezone.utc).isoformat(),
              "text": text, "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
              "publication_attempted": False}
    target = root / "codex-result.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("TEXT_GENERATED_AND_VALIDATED; not sent to X or Buffer.")


def record(root: Path) -> None:
    github_guard()
    path = root / "codex-result.json"
    data = json.loads(path.read_text())
    run_id = os.environ["GITHUB_RUN_ID"]
    if not run_id.isdigit() or data.get("run_id") != run_id or data.get("mode") != "dry_run" or data.get("publication_attempted") is not False:
        raise ValueError("Invalid proof artifact.")
    text = validate_post({"text": data.get("text"), "category": "atemporal", "requires_approval": False}, [])
    if data.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest():
        raise ValueError("Proof checksum mismatch.")
    target = "contents/codex-trials/" + run_id + ".json"
    token = os.environ.get("GITHUB_TOKEN", "")
    if github_json(target, token, missing_ok=True):
        print("PROOF_ALREADY_RECORDED; not duplicated.")
        return
    response = github_json(target, token, {"message": "Codex: registrar teste sem publicação " + run_id,
                                          "branch": "main", "content": base64.b64encode(path.read_bytes()).decode()})
    print("PROOF_COMMIT", response["commit"]["sha"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["auth-init", "auth-restore", "auth-save", "generate", "record"])
    args = parser.parse_args()
    try:
        if args.action.startswith("auth-"):
            if args.action == "auth-save":
                auth_save()
            else:
                auth_prepare(args.action == "auth-restore")
        elif args.action == "generate":
            generate(Path.cwd())
        else:
            record(Path.cwd())
    except RuntimeError as exc:
        print("FAILED:", str(exc))
        raise SystemExit(1)
    except Exception as exc:
        # Do not echo raw responses, auth JSON or model logs.
        print("FAILED", type(exc).__name__, ": action could not complete safely; no publication was attempted.")
        raise SystemExit(1)
