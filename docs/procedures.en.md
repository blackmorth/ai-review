# ai-review — procedures

[Version française](procedures.fr.md)

Principle: **the agent clears the ground, the human decides.** `ai-review` makes a first pass on a pull/merge
request: description faithful to the diff, implicit assumptions, risk areas, reading callers beyond the diff,
findings classified 🔴 critical / 🟡 warning / 💡 suggestion. It is never an approval.

Whatever the case, you need `python3` and `git`, and the base commit must be present (no shallow clone).
Any language (PHP, Rust, Go, Python, Ruby…): the tool reads the repository through git, nothing is language-specific.

| # | Case | Where the AI runs | Does the code leave the network? |
|---|---|---|---|
| 1 | [By hand on your machine](#1-by-hand-on-your-machine-ai-review-command) | local Ollama | no |
| 2 | [Share your Ollama on the local network](#2-share-your-ollama-on-the-local-network) | another PC's Ollama | no |
| 3 | [By hand from another PC of the network](#3-by-hand-from-another-pc-of-the-network) | shared Ollama | no |
| 4 | [GitHub Actions + online AI](#4-github-actions--online-ai) | provider | yes, to the provider |
| 5 | [GitHub Actions + self-hosted runner + Ollama](#5-github-actions--self-hosted-runner--ollama) | local Ollama | no (except GitHub itself) |
| 6 | [GitLab CI + online AI](#6-gitlab-ci--online-ai) | provider | yes, to the provider |
| 7 | [Private GitLab + network runner + Ollama](#7-private-gitlab--network-runner--ollama-fully-in-house) | network Ollama | no |
| 8 | [Any other CI](#8-any-other-ci-jenkins-forgejo-woodpecker-bitbucket) | your choice | depends on the choice |

Common to every case: [project context](#project-context-ai-reviewmd),
[PR description](#pr-description), [troubleshooting](#troubleshooting). Variables and providers: [README](../README.md).

---

## Project context (`.ai-review.md`)

At the root of the reviewed repository, a few lines the AI reads before the diff: stack, architecture and layers,
the project's own sensitive areas (rights, money, personal data…). Without this file the AI only knows the code.
Example:

```markdown
Go 1.23 service, hexagonal architecture (domain → app → adapters). The billing package computes amounts
in cents (int64), never float. Sensitive areas: auth middleware, customer CSV export (GDPR).
```

## PR description

The AI checks that the description states what was verified **and** what was not. Template to copy:
[`templates/change-request.md`](../templates/change-request.md) to `.github/pull_request_template.md` (GitHub)
or `.gitlab/merge_request_templates/Default.md` (GitLab). Locally, write that text in `pr.md`.

---

## 1. By hand on your machine (`ai-review` command)

**When:** before opening the PR, to fix things before a human reads. Free, the code never leaves the machine.

**Prerequisites:** [Ollama](https://ollama.com) and a code model (`ollama pull qwen3-coder:30b`), a clone of this
repository.

**Install (once):**
```sh
git clone https://github.com/blackmorth/ai-review.git ~/ai-review
mkdir -p ~/bin && ln -sf ~/ai-review/bin/ai-review ~/bin/ai-review
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

**Use**, from the repository to review, work branch committed:
```sh
ai-review                          # compares with the remote's default branch (origin/HEAD, else origin/main)
ai-review HEAD~3                   # only the last 3 commits
ai-review main --output review.md  # keeps the report in a file
ai-review --json review.json       # structured version as well
ai-review --fail-on-critical       # exit code 2 on a 🔴 (for a script)
```
- `./pr.md` is used as the description when it exists.
- Only **commits** are reviewed, not uncommitted changes.
- When Ollama is not running, the command starts it with `OLLAMA_CONTEXT_LENGTH=32768`.
- Overridable defaults: `AI_REVIEW_BASE_URL`, `AI_REVIEW_MODEL` (`qwen3-coder:30b`), `AI_REVIEW_LANGUAGE`
  (`French`). Report in English: `AI_REVIEW_LANGUAGE=English ai-review`.

**Post the report on an open PR (optional):**
```sh
ai-review --output review.md
gh pr comment --body-file review.md          # GitHub
glab mr note -m "$(cat review.md)"           # GitLab
```

**Avoid:** running it as a `pre-commit` hook (too slow); as `pre-push`, possible but ~2 min on every push.

## 2. Share your Ollama on the local network

**When:** one machine with a GPU serves the model to the other PCs or runners of the network.

**On native Linux:**
```sh
OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_HOST=0.0.0.0:11434 ollama serve
```
(with systemd: `sudo systemctl edit ollama` and add
`Environment="OLLAMA_HOST=0.0.0.0:11434"` and `Environment="OLLAMA_CONTEXT_LENGTH=32768"`.)

**On WSL2 (Windows):** WSL sits behind a NAT, so the way in must also be opened on the Windows side.

1. Start Ollama in WSL as above (`OLLAMA_HOST=0.0.0.0:11434`).
2. Turn on mirrored networking: in `C:\Users\<you>\.wslconfig`
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   then in PowerShell: `wsl --shutdown`, and start WSL again.
   *Alternative without mirrored networking* (the WSL IP changes on each restart, redo it) — admin PowerShell:
   ```powershell
   netsh interface portproxy add v4tov4 listenport=11434 listenaddress=0.0.0.0 connectport=11434 connectaddress=<wsl-ip>
   ```
   (`<wsl-ip>`: `hostname -I` in WSL.)
3. Windows firewall, admin PowerShell, **restricted to the allowed IPs**:
   ```powershell
   New-NetFirewallRule -DisplayName "Ollama LAN" -Direction Inbound -Protocol TCP -LocalPort 11434 `
     -RemoteAddress 192.168.1.20,192.168.1.21 -Action Allow
   ```
   With mirrored networking, if it still blocks, also allow WSL's Hyper-V firewall:
   ```powershell
   Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
   ```

**Check**, from another PC: `curl http://<windows-ip>:11434/v1/models` lists the models.

**Security:** Ollama has **no authentication**: any machine reaching the port can use it. Never expose it to the
Internet; restrict the firewall to the intended machines.

## 3. By hand from another PC of the network

**When:** a colleague or another machine, without a GPU, uses the shared Ollama (case 2) on its own clone.

1. Install `ai-review` as in case 1 (no Ollama needed on that machine). If it has no Internet access, copy the
   folder or clone an internal mirror (see case 7).
2. Point at the machine serving the model:
   ```sh
   export AI_REVIEW_BASE_URL=http://<ollama-machine-ip>:11434/v1
   ai-review
   ```
   (put it in `~/.bashrc` to forget about it). The command only starts Ollama for `localhost`.

Only the diff and the files the agent reads travel, between that machine and the Ollama machine.

## 4. GitHub Actions + online AI

**When:** GitHub repository, an online AI acceptable for the code's confidentiality.

1. Copy [`examples/github-workflow.yml`](../examples/github-workflow.yml) to `.github/workflows/ai-review.yml`.
2. In GitHub → Settings → Secrets and variables → Actions:
   - **Variables**: `AI_REVIEW_BASE_URL` and `AI_REVIEW_MODEL`, for instance
     `https://api.anthropic.com/v1/` + `claude-sonnet-5-5`, or `https://api.mistral.ai/v1` + `mistral-large-latest`
     (other providers: [README](../README.md)), and for a thinking model (Gemini…)
     `AI_REVIEW_REASONING_EFFORT` = `low`, much faster;
   - **Secret**: `AI_REVIEW_API_KEY`, the provider's key.
3. To make a 🔴 block the merge: `fail-on-critical: 'true'` in the workflow, then make the job required
   (Settings → Branches → protection rule → *Require status checks*).

**Check:** open a PR (not a draft): an "AI first pass" comment appears, then is updated on each push instead of
piling up.

The diff, the description and the files read go to the chosen provider.

## 5. GitHub Actions + self-hosted runner + Ollama

**When:** GitHub repository, but the AI must stay on your machine. GitHub's runners cannot see your `localhost`:
the job must run at your place.

1. Ollama on the runner's machine (`OLLAMA_CONTEXT_LENGTH=32768 ollama serve`), or on the network (case 2).
2. Runner: GitHub → Settings → Actions → Runners → *New self-hosted runner* → Linux x64, copy the commands shown
   and add a label to the configuration:
   ```sh
   ./config.sh --url https://github.com/<owner>/<repo> --token <token> --labels ollama
   ./run.sh                                      # or as a service: sudo ./svc.sh install && sudo ./svc.sh start
   ```
   The machine needs `python3` and `git`.
3. Workflow: [`examples/github-workflow.yml`](../examples/github-workflow.yml) (its line
   `runs-on: ${{ vars.AI_REVIEW_RUNNER || 'ubuntu-24.04' }}` picks the runner).
4. Repository variables: `AI_REVIEW_RUNNER` = `ollama`, `AI_REVIEW_BASE_URL` = `http://localhost:11434/v1`
   (or `http://<ip>:11434/v1`), `AI_REVIEW_MODEL` = `qwen3-coder:30b`. No secret.
   Deleting `AI_REVIEW_RUNNER` goes back to GitHub's runners (case 4).

**Limits:**
- Machine and runner off: the job waits in the queue, it does not fail.
- **Public repository: not recommended.** A PR from a fork would run code on your machine. Keep it for private
  repositories.
- A local 30B model catches less than a large online model.

## 6. GitLab CI + online AI

**When:** gitlab.com or self-managed GitLab, online AI acceptable.

1. Add the job from [`examples/gitlab-ci.yml`](../examples/gitlab-ci.yml) to `.gitlab-ci.yml`.
2. Settings → CI/CD → Variables: `AI_REVIEW_BASE_URL`, `AI_REVIEW_MODEL`, `AI_REVIEW_API_KEY` (masked).
3. Token to write the comment (`CI_JOB_TOKEN` cannot): Settings → Access tokens → *project access token*, role
   Reporter, scope `api` → masked variable `AI_REVIEW_GITLAB_TOKEN`.
4. The job only runs in merge request pipelines (`merge_request_event`).

**Check:** open an MR: an "AI first pass" note appears and is updated on each push; the report is also in the
job's artifacts (`ai-review.md`).

## 7. Private GitLab + network runner + Ollama (fully in-house)

**When:** nothing may leave the network: self-managed GitLab, runner and Ollama on the local network.
(With a private repository on gitlab.com, the code is already at GitLab's; only the AI stays with you.)

1. Ollama shared on the network (case 2).
2. Internal mirror of the tool: create an `ai-review` project in your GitLab and push this repository to it
   (`git push --mirror`), tags included (`v1`).
3. GitLab Runner on a machine of the network ([install](https://docs.gitlab.com/runner/install/)), registered with
   a tag:
   ```sh
   sudo gitlab-runner register --url https://gitlab.internal --token <token> --executor docker \
     --docker-image python:3.13 --tag-list ollama
   ```
   With the `shell` executor instead, the machine needs `python3` and `git`.
4. Job: [`examples/gitlab-ci.yml`](../examples/gitlab-ci.yml), uncomment `tags: [ollama]`.
5. Project CI/CD variables:
   - `AI_REVIEW_BASE_URL` = `http://<ollama-machine-ip>:11434/v1` — **not `localhost`** with the docker executor:
     inside the container, `localhost` is the container itself;
   - `AI_REVIEW_MODEL` = `qwen3-coder:30b`; no key;
   - `AI_REVIEW_SOURCE` = `https://gitlab.internal/<group>/ai-review.git` (the mirror of step 2);
   - `AI_REVIEW_GITLAB_TOKEN` as in case 6.
6. When the runner has no Internet access, put the `python:3.13` image in your internal registry and change
   `image:`.

**Check:** open an MR, the note appears; on the Ollama machine, `ollama ps` shows the model loaded during the job.

## 8. Any other CI (Jenkins, Forgejo, Woodpecker, Bitbucket…)

The script depends on no forge: it writes a report, the rest is up to the CI.

```sh
export AI_REVIEW_BASE_URL=… AI_REVIEW_MODEL=… AI_REVIEW_API_KEY=…
python3 ai_review.py --base origin/main --description-file pr-description.md --post none \
    --output ai-review.md --json ai-review.json --fail-on-critical
```
- `--base`: the PR's starting commit, as your CI exposes it; fetch the full history.
- The description: write it to a file from your CI's variable, or use the `AI_REVIEW_DESCRIPTION` /
  `AI_REVIEW_TITLE` environment variables.
- Post `ai-review.md` through the forge's API, or keep it as an artifact. Exit code 2 = 🔴 with
  `--fail-on-critical`.

Template: [`examples/any-ci.sh`](../examples/any-ci.sh).

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The AI comments on code absent from the diff | Ollama context too small: the prompt is truncated. Start Ollama with `OLLAMA_CONTEXT_LENGTH=32768`. |
| "Description matches: yes" with no description at all | Small models are unreliable on that point: check it yourself or use a larger model. |
| `Empty diff: nothing to review.` | Nothing committed since the base, or wrong base (`--base`). |
| `unknown revision` / base not found | Shallow clone: `fetch-depth: 0` (GitHub), `GIT_DEPTH: 0` (GitLab), `git fetch origin` locally. |
| No comment on the PR/MR | GitHub: `pull-requests: write` permission. GitLab: `AI_REVIEW_GITLAB_TOKEN` missing or role too low. |
| `AI provider answered 401/404` | Key, URL (often the trailing `/v1`) or model id. |
| `Connection refused` from another PC | Ollama listens on `127.0.0.1` (`OLLAMA_HOST=0.0.0.0:11434`), firewall, or WSL without mirrored networking. |
| GitHub job stuck "Waiting for a runner" | Self-hosted runner off, or its label differs from `AI_REVIEW_RUNNER`. |
| Very slow review (over 5 min) | The job log gives each turn's duration. Thinking model: `AI_REVIEW_REASONING_EFFORT=low`. Fewer turns: `AI_REVIEW_MAX_TURNS`. Saturated free tier: the 429/503 retries show in the log. |
| Report in the wrong language | `AI_REVIEW_LANGUAGE`; some small models ignore it now and then. |
