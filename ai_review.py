#!/usr/bin/env python3
"""First-pass AI review of a pull/merge request, for any repository, any CI, any model.

The model is reached through the OpenAI-compatible chat completions protocol, which OpenAI, Anthropic, Mistral,
Gemini, OpenRouter, Ollama and vLLM all speak: switching AI is switching AI_REVIEW_BASE_URL and AI_REVIEW_MODEL.
The agent explores the repository through read-only tools (read a file, search, list files) so it can read callers
beyond the diff. It writes a Markdown report and, on GitHub or GitLab, keeps one comment up to date on the request.
Python standard library only, so it runs on any CI image that has python3 and git.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

PROMPT_FILE = Path(__file__).with_name("prompt.md")
COMMENT_MARKER = "<!-- ai-review -->"
SEVERITIES = ("critical", "warning", "suggestion")
# Report labels; the model writes its own text in AI_REVIEW_LANGUAGE, any language it knows.
LABELS = {
    "en": {"critical": "🔴 Critical", "warning": "🟡 Warning", "suggestion": "💡 Suggestion", "heading": "AI first pass",
           "model": "first filter, not an approval: the decision stays human.", "size": "Size", "files": "files",
           "lines": "lines", "too_big": "beyond {lines} lines or {files} files, human review skims: split it?",
           "above_target": "above the {lines}-line target", "matches": "Description matches the diff",
           "yes": "yes", "no": "**no**", "none": "Nothing to report."},
    "fr": {"critical": "🔴 Critique", "warning": "🟡 Warning", "suggestion": "💡 Suggestion", "heading": "Défrichage IA",
           "model": "premier filtre, pas une validation : la décision reste humaine.", "size": "Taille",
           "files": "fichiers", "lines": "lignes",
           "too_big": "au-delà de {lines} lignes ou {files} fichiers, la review humaine survole : découper ?",
           "above_target": "au-dessus de la cible de {lines} lignes", "matches": "Description fidèle au diff",
           "yes": "oui", "no": "**non**", "none": "Aucun point relevé."},
}
# Cisco, "Best Kept Secrets of Peer Code Review": detection collapses past 400 changed lines or 10 files.
TARGET_LINES, MAX_LINES, MAX_FILES = 200, 400, 10
MAX_DIFF_CHARS, MAX_TOOL_CHARS = 150_000, 20_000


@dataclass(frozen=True)
class Settings:
    base_url: str
    model: str
    api_key: str
    base_ref: str
    language: str
    context_file: str
    max_turns: int
    post: str
    fail_on_critical: bool
    output: str | None
    json_output: str | None


@dataclass(frozen=True)
class ChangeRequest:
    title: str
    description: str
    diff: str
    changed_files: list[str]
    added_lines: int
    deleted_lines: int


@dataclass(frozen=True)
class Finding:
    severity: str
    file: str
    line: int | None
    title: str
    detail: str


@dataclass
class Review:
    summary: str
    description_matches_diff: bool | None
    findings: list[Finding] = field(default_factory=list)

    def has_critical(self) -> bool:
        return any(finding.severity == "critical" for finding in self.findings)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def detect_base_ref() -> str:
    """The commit the request branches from, as each CI exposes it."""
    if sha := os.environ.get("CI_MERGE_REQUEST_DIFF_BASE_SHA"):  # GitLab
        return sha
    if event := github_event():
        return event["pull_request"]["base"]["sha"]
    if branch := os.environ.get("GITHUB_BASE_REF"):
        return f"origin/{branch}"
    return "origin/main"


def github_event() -> dict | None:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not Path(path).is_file():
        return None
    event = json.loads(Path(path).read_text())
    return event if "pull_request" in event else None


def read_description(args: argparse.Namespace) -> tuple[str, str]:
    if args.description_file:
        return "", Path(args.description_file).read_text()
    if "AI_REVIEW_DESCRIPTION" in os.environ:
        return os.environ.get("AI_REVIEW_TITLE", ""), os.environ["AI_REVIEW_DESCRIPTION"]
    if "CI_MERGE_REQUEST_IID" in os.environ:
        return os.environ.get("CI_MERGE_REQUEST_TITLE", ""), os.environ.get("CI_MERGE_REQUEST_DESCRIPTION", "")
    if event := github_event():
        return event["pull_request"]["title"] or "", event["pull_request"]["body"] or ""
    return "", ""


def load_change_request(base_ref: str, title: str, description: str) -> ChangeRequest:
    revision_range = f"{base_ref}...HEAD"
    added = deleted = 0
    for row in git("diff", "--numstat", revision_range).splitlines():
        plus, minus, _ = row.split("\t", 2)
        if plus != "-":  # binary files count no lines
            added, deleted = added + int(plus), deleted + int(minus)
    return ChangeRequest(
        title=title,
        description=description,
        diff=git("diff", "--unified=5", revision_range),
        changed_files=git("diff", "--name-only", revision_range).split(),
        added_lines=added,
        deleted_lines=deleted,
    )


class RepositoryTools:
    """Read-only window on the tracked files: the agent never writes, never runs code, never leaves the repo."""

    SPECS = [
        {"type": "function", "function": {
            "name": "read_file",
            "description": "Read a tracked file, optionally a line range (1-based, inclusive).",
            "parameters": {"type": "object", "required": ["path"], "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            }},
        }},
        {"type": "function", "function": {
            "name": "search",
            "description": "Search tracked files for a fixed string (callers, usages). Returns path:line:text.",
            "parameters": {"type": "object", "required": ["text"], "properties": {
                "text": {"type": "string"},
                "path_glob": {"type": "string", "description": "Optional, e.g. 'src/**/*.php'."},
            }},
        }},
        {"type": "function", "function": {
            "name": "list_files",
            "description": "List tracked files matching a glob, e.g. 'src/Billing/**'.",
            "parameters": {"type": "object", "required": ["path_glob"], "properties": {
                "path_glob": {"type": "string"},
            }},
        }},
    ]

    def __init__(self) -> None:
        self.tracked = set(git("ls-files").splitlines())

    def call(self, name: str, arguments: dict) -> str:
        try:
            match name:
                case "read_file":
                    return self.read_file(arguments["path"], arguments.get("start_line"), arguments.get("end_line"))
                case "search":
                    return self.search(arguments["text"], arguments.get("path_glob"))
                case "list_files":
                    return self.list_files(arguments["path_glob"])
            return f"Unknown tool {name}."
        except (KeyError, TypeError, ValueError, subprocess.CalledProcessError) as error:
            return f"Tool error: {error}"

    def read_file(self, path: str, start: int | None, end: int | None) -> str:
        if path not in self.tracked:
            return f"{path} is not a tracked file."
        lines = Path(path).read_text(errors="replace").splitlines()
        first = max(1, start or 1)
        last = min(len(lines), end or len(lines))
        numbered = "\n".join(f"{number}: {lines[number - 1]}" for number in range(first, last + 1))
        return truncate(numbered)

    def search(self, text: str, path_glob: str | None) -> str:
        command = ["git", "grep", "-n", "-I", "-F", "-e", text, "--"] + ([f":(glob){path_glob}"] if path_glob else [])
        result = subprocess.run(command, capture_output=True, text=True)
        return truncate(result.stdout) or "No match."

    def list_files(self, path_glob: str) -> str:
        return truncate(git("ls-files", "--", f":(glob){path_glob}")) or "No file."


def truncate(text: str) -> str:
    return text if len(text) <= MAX_TOOL_CHARS else text[:MAX_TOOL_CHARS] + "\n… [truncated]"


class ChatClient:
    def __init__(self, settings: Settings) -> None:
        self.url = settings.base_url.rstrip("/") + "/chat/completions"
        self.model = settings.model
        self.api_key = settings.api_key

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        body = {"model": self.model, "messages": messages, "tools": tools, "temperature": 0.1}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.load(response)["choices"][0]["message"]
        except urllib.error.HTTPError as error:
            raise SystemExit(f"AI provider answered {error.code}: {error.read().decode(errors='replace')[:500]}")


def build_brief(change: ChangeRequest, settings: Settings) -> str:
    project_context = Path(settings.context_file)
    context = project_context.read_text() if project_context.is_file() else "(none)"
    diff = change.diff if len(change.diff) <= MAX_DIFF_CHARS else change.diff[:MAX_DIFF_CHARS] + "\n… [diff truncated]"
    return (
        f"Write the summary, titles and details in {settings.language}.\n\n"
        f"## Project context ({settings.context_file})\n{context}\n\n"
        f"## Request title\n{change.title or '(none)'}\n\n"
        f"## Request description\n{change.description or '(empty: the author wrote nothing)'}\n\n"
        f"## Changed files ({len(change.changed_files)}, +{change.added_lines} -{change.deleted_lines})\n"
        + "\n".join(change.changed_files)
        + f"\n\n## Diff\n```diff\n{diff}\n```"
    )


def run_agent(change: ChangeRequest, settings: Settings) -> Review:
    tools = RepositoryTools()
    client = ChatClient(settings)
    messages: list[dict] = [
        {"role": "system", "content": PROMPT_FILE.read_text()},
        {"role": "user", "content": build_brief(change, settings)},
    ]
    for turn in range(settings.max_turns):
        last_turn = turn == settings.max_turns - 1
        message = client.complete(messages, [] if last_turn else RepositoryTools.SPECS)
        messages.append({key: value for key, value in message.items() if value is not None})
        calls = message.get("tool_calls") or []
        if not calls:
            return parse_review(message.get("content") or "")
        for call in calls:
            raw_arguments = call["function"].get("arguments") or "{}"
            arguments = raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments)
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": tools.call(call["function"]["name"], arguments),
            })
        if turn == settings.max_turns - 2:
            messages.append({"role": "user", "content": "Exploration budget spent: answer now with the JSON."})
    raise SystemExit("The model never produced its review.")


def parse_review(content: str) -> Review:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return Review(summary=f"The model did not answer in JSON:\n\n{content}", description_matches_diff=None)
    data = json.loads(match.group(0))
    findings = [
        Finding(
            severity=item.get("severity", "suggestion") if item.get("severity") in SEVERITIES else "suggestion",
            file=item.get("file", ""),
            line=item.get("line") if isinstance(item.get("line"), int) else None,
            title=item.get("title", ""),
            detail=item.get("detail", ""),
        )
        for item in data.get("findings", [])
    ]
    findings.sort(key=lambda finding: SEVERITIES.index(finding.severity))
    return Review(data.get("summary", ""), data.get("description_matches_diff"), findings)


def render_markdown(review: Review, change: ChangeRequest, settings: Settings) -> str:
    label = LABELS["fr" if settings.language.lower().startswith("fr") else "en"]
    changed_lines = change.added_lines + change.deleted_lines
    size = (f"{len(change.changed_files)} {label['files']}, {changed_lines} {label['lines']}"
            f" (+{change.added_lines} -{change.deleted_lines})")
    if changed_lines > MAX_LINES or len(change.changed_files) > MAX_FILES:
        size += " — ⚠️ " + label["too_big"].format(lines=MAX_LINES, files=MAX_FILES)
    elif changed_lines > TARGET_LINES:
        size += " — " + label["above_target"].format(lines=TARGET_LINES)
    matches = {True: label["yes"], False: label["no"], None: "?"}[review.description_matches_diff]
    lines = [
        COMMENT_MARKER,
        f"## {label['heading']}",
        f"_`{settings.model}` — {label['model']}_",
        "",
        f"**{label['size']} :** {size}  ",
        f"**{label['matches']} :** {matches}",
        "",
        review.summary,
        "",
    ]
    if not review.findings:
        lines.append(label["none"])
    for finding in review.findings:
        where = f"`{finding.file}:{finding.line}`" if finding.line else f"`{finding.file}`"
        lines += [f"- {label[finding.severity]} — {where} **{finding.title}**", f"  {finding.detail}"]
    return "\n".join(lines) + "\n"


def post_comment(body: str, target: str) -> None:
    """Create the review comment, or update the one this tool left on a previous run, so pushes do not pile comments
    up. Only its own: a human may paste a report carrying the same marker, and editing it would be refused (403)."""
    if target == "github":
        event = github_event()
        token = os.environ.get("GITHUB_TOKEN")
        if not event or not token:
            print("No pull_request event or GITHUB_TOKEN: comment not posted.", file=sys.stderr)
            return
        api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        comments_url = f"{api}/repos/{os.environ['GITHUB_REPOSITORY']}/issues/{event['pull_request']['number']}/comments"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        # GITHUB_TOKEN cannot call /user: its identity is the Actions bot, unless a PAT author is configured.
        author = os.environ.get("AI_REVIEW_COMMENT_AUTHOR", "github-actions[bot]")
        existing = next((c for c in http("GET", comments_url + "?per_page=100", headers)
                         if COMMENT_MARKER in c["body"] and c["user"]["login"] == author), None)
        url = f"{api}/repos/{os.environ['GITHUB_REPOSITORY']}/issues/comments/{existing['id']}" if existing else comments_url
        http("PATCH" if existing else "POST", url, headers, {"body": body})
    elif target == "gitlab":
        token = os.environ.get("AI_REVIEW_GITLAB_TOKEN")  # CI_JOB_TOKEN cannot write notes
        if not token or "CI_MERGE_REQUEST_IID" not in os.environ:
            print("No merge request pipeline or AI_REVIEW_GITLAB_TOKEN: comment not posted.", file=sys.stderr)
            return
        notes_url = (f"{os.environ['CI_API_V4_URL']}/projects/{os.environ['CI_PROJECT_ID']}"
                     f"/merge_requests/{os.environ['CI_MERGE_REQUEST_IID']}/notes")
        headers = {"PRIVATE-TOKEN": token}
        author_id = http("GET", f"{os.environ['CI_API_V4_URL']}/user", headers)["id"]
        existing = next((n for n in http("GET", notes_url + "?per_page=100", headers)
                         if COMMENT_MARKER in n["body"] and n["author"]["id"] == author_id), None)
        url = f"{notes_url}/{existing['id']}" if existing else notes_url
        http("PUT" if existing else "POST", url, headers, {"body": body})


def http(method: str, url: str, headers: dict, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data, {**headers, "Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def detect_platform() -> str:
    if os.environ.get("GITHUB_ACTIONS"):
        return "github"
    if os.environ.get("GITLAB_CI"):
        return "gitlab"
    return "none"


def parse_settings() -> tuple[Settings, argparse.Namespace]:
    env = os.environ.get
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", help="Commit or branch the request branches from (auto-detected in CI).")
    parser.add_argument("--description-file", help="File holding the request description (auto-detected in CI).")
    parser.add_argument("--output", help="Write the Markdown report there (stdout when omitted).")
    parser.add_argument("--json", dest="json_output", help="Also write the review as JSON there.")
    parser.add_argument("--post", choices=["auto", "github", "gitlab", "none"], default=env("AI_REVIEW_POST", "auto"))
    parser.add_argument("--fail-on-critical", action="store_true", default=env("AI_REVIEW_FAIL_ON_CRITICAL") == "true")
    args = parser.parse_args()
    if not env("AI_REVIEW_BASE_URL") or not env("AI_REVIEW_MODEL"):
        parser.error("AI_REVIEW_BASE_URL and AI_REVIEW_MODEL are required (see README).")
    settings = Settings(
        base_url=env("AI_REVIEW_BASE_URL"),
        model=env("AI_REVIEW_MODEL"),
        api_key=env("AI_REVIEW_API_KEY", ""),
        base_ref=args.base or detect_base_ref(),
        language=env("AI_REVIEW_LANGUAGE", "English"),
        context_file=env("AI_REVIEW_CONTEXT_FILE", ".ai-review.md"),
        max_turns=int(env("AI_REVIEW_MAX_TURNS", "20")),
        post=detect_platform() if args.post == "auto" else args.post,
        fail_on_critical=args.fail_on_critical,
        output=args.output,
        json_output=args.json_output,
    )
    return settings, args


def main() -> int:
    settings, args = parse_settings()
    title, description = read_description(args)
    os.chdir(git("rev-parse", "--show-toplevel").strip())  # tool paths are relative to the repository root
    change = load_change_request(settings.base_ref, title, description)
    if not change.changed_files:
        print("Empty diff: nothing to review.", file=sys.stderr)
        return 0
    review = run_agent(change, settings)
    report = render_markdown(review, change, settings)
    if settings.output:
        Path(settings.output).write_text(report)
    else:
        print(report)
    if settings.json_output:
        Path(settings.json_output).write_text(json.dumps(review, default=lambda o: o.__dict__, ensure_ascii=False, indent=2))
    if settings.post != "none":
        try:
            post_comment(report, settings.post)
        except urllib.error.HTTPError as error:  # the report exists anyway: a posting failure must not hide it
            print(f"Comment not posted, forge answered {error.code}: {error.read().decode(errors='replace')[:300]}",
                  file=sys.stderr)
    return 2 if settings.fail_on_critical and review.has_critical() else 0


if __name__ == "__main__":
    sys.exit(main())
