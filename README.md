# ai-review

First-pass AI review of a pull/merge request — **the agent clears the ground, the human decides.**
Any repository, any language, any CI, any model.

- **Any model**: speaks the OpenAI-compatible chat completions protocol (with tool calling).
- **Any language**: the agent explores the repo with `git ls-files` / `git grep` / file reads; nothing is
  language-specific. Project specifics go in a `.ai-review.md` at the repository root (stack, layers,
  sensitive areas).
- **Any CI**: one Python 3 file, standard library only, needs `python3` and `git` with the base commit fetched.
  Keeps one comment up to date on GitHub and GitLab; elsewhere, writes Markdown (`--output`) and JSON (`--json`).

What the agent does (see `prompt.md`): checks the description against the diff (what was verified, what was not),
names implicit assumptions, audits risk areas (auth, access rights, money, injections, data leaks, migrations),
reads callers beyond the diff, and classifies findings 🔴 critical / 🟡 warning / 💡 suggestion. It stays silent
on what linters enforce. The size of the change is checked without AI (target 200 lines, alarm past 400 or 10 files).

## Configuration

| Variable | |
|---|---|
| `AI_REVIEW_BASE_URL` | endpoint, see below (required) |
| `AI_REVIEW_MODEL` | model id (required) |
| `AI_REVIEW_API_KEY` | secret; empty for a local server |
| `AI_REVIEW_LANGUAGE` | language of the report, default `English` |
| `AI_REVIEW_CONTEXT_FILE` | project context, default `.ai-review.md` |
| `AI_REVIEW_MAX_TURNS` | exploration budget, default `20` |
| `AI_REVIEW_FAIL_ON_CRITICAL` | `true`: exit code 2 on a critical finding |
| `AI_REVIEW_GITLAB_TOKEN` | GitLab only: token allowed to write MR notes |

| Provider | `AI_REVIEW_BASE_URL` |
|---|---|
| Anthropic | `https://api.anthropic.com/v1/` |
| OpenAI | `https://api.openai.com/v1` |
| Mistral | `https://api.mistral.ai/v1` |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Ollama (self-hosted runner or laptop) | `http://localhost:11434/v1` — start it with `OLLAMA_CONTEXT_LENGTH=32768`, the default window truncates the diff and the model then reviews code that is not in it |

The diff, description and files are sent to that provider: choose it according to the code's confidentiality.

## Plugging it in

- GitHub: `examples/github-workflow.yml` (uses `action.yml`).
- GitLab: `examples/gitlab-ci.yml`.
- Anything else or locally: `examples/any-ci.sh`, or
  `python3 ai_review.py --base origin/main --description-file pr.md --post none`.
- Description template for authors: `templates/change-request.md`.
