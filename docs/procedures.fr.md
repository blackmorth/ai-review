# ai-review — procédures

[English version](procedures.en.md)

Principe : **l'agent défriche, l'humain décide.** `ai-review` fait une première passe sur une pull/merge request :
description fidèle au diff, hypothèses implicites, zones à risque, lecture des appelants hors du diff,
retours classés 🔴 critique / 🟡 warning / 💡 suggestion. Ce n'est jamais une validation.

Quel que soit le cas, il faut `python3` et `git`, et le commit de base doit être présent (pas de clone superficiel).
N'importe quel langage (PHP, Rust, Go, Python, Ruby…) : l'outil lit le dépôt avec git, rien n'est propre à un langage.

| # | Cas | Où tourne l'IA | Le code sort-il du réseau ? |
|---|---|---|---|
| 1 | [À la main sur son poste](#1-à-la-main-sur-son-poste-commande-ai-review) | Ollama local | non |
| 2 | [Partager son Ollama sur le réseau local](#2-partager-son-ollama-sur-le-réseau-local) | Ollama d'un autre PC | non |
| 3 | [À la main depuis un autre PC du réseau](#3-à-la-main-depuis-un-autre-pc-du-réseau) | Ollama partagé | non |
| 4 | [GitHub Actions + IA en ligne](#4-github-actions--ia-en-ligne) | fournisseur | oui, chez le fournisseur |
| 5 | [GitHub Actions + runner local + Ollama](#5-github-actions--runner-auto-hébergé--ollama) | Ollama local | non (sauf GitHub lui-même) |
| 6 | [GitLab CI + IA en ligne](#6-gitlab-ci--ia-en-ligne) | fournisseur | oui, chez le fournisseur |
| 7 | [GitLab privé + runner du réseau + Ollama](#7-gitlab-privé--runner-du-réseau--ollama-tout-en-interne) | Ollama du réseau | non |
| 8 | [Toute autre CI](#8-toute-autre-ci-jenkins-forgejo-woodpecker-bitbucket) | au choix | selon le choix |

Communs à tous les cas : [contexte du projet](#contexte-du-projet-ai-reviewmd),
[description de la PR](#description-de-la-pr), [dépannage](#dépannage). Variables et fournisseurs : [README](../README.md).

---

## Contexte du projet (`.ai-review.md`)

À la racine du dépôt revu, quelques lignes que l'IA lit avant le diff : stack, architecture et couches,
zones sensibles propres au projet (droits, argent, données personnelles…). Sans ce fichier, l'IA ne connaît que
le code. Exemple :

```markdown
Service Go 1.23, architecture hexagonale (domain → app → adapters). Le paquet billing calcule des montants
en centimes (int64), jamais en float. Zones sensibles : middleware d'auth, export CSV des clients (RGPD).
```

## Description de la PR

L'IA vérifie que la description dit ce qui a été vérifié **et** ce qui ne l'a pas été. Modèle à copier :
[`templates/change-request.md`](../templates/change-request.md) vers `.github/pull_request_template.md` (GitHub)
ou `.gitlab/merge_request_templates/Default.md` (GitLab). En local, écrire ce texte dans `pr.md`.

---

## 1. À la main sur son poste (commande `ai-review`)

**Quand :** avant d'ouvrir la PR, pour corriger avant qu'un humain relise. Gratuit, le code ne quitte pas la machine.

**Prérequis :** [Ollama](https://ollama.com) et un modèle de code (`ollama pull qwen3-coder:30b`), un clone de
ce dépôt.

**Installation (une fois) :**
```sh
git clone https://github.com/blackmorth/ai-review.git ~/ai-review
mkdir -p ~/bin && ln -sf ~/ai-review/bin/ai-review ~/bin/ai-review
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

**Utilisation**, depuis le dépôt à revoir, branche de travail commitée :
```sh
ai-review                          # compare à la branche par défaut du remote (origin/HEAD, sinon origin/main)
ai-review HEAD~3                   # seulement les 3 derniers commits
ai-review main --output review.md  # garde le rapport dans un fichier
ai-review --json review.json       # version structurée en plus
ai-review --fail-on-critical       # code de sortie 2 s'il y a un 🔴 (pour un script)
```
- `./pr.md` est pris comme description s'il existe.
- Seuls les **commits** sont revus, pas les modifications non commitées.
- Si Ollama ne tourne pas, la commande le démarre avec `OLLAMA_CONTEXT_LENGTH=32768`.
- Valeurs par défaut modifiables : `AI_REVIEW_BASE_URL`, `AI_REVIEW_MODEL` (`qwen3-coder:30b`),
  `AI_REVIEW_LANGUAGE` (`French`). Exemple en anglais : `AI_REVIEW_LANGUAGE=English ai-review`.

**Publier le rapport sur une PR déjà ouverte (facultatif) :**
```sh
ai-review --output review.md
gh pr comment --body-file review.md          # GitHub
glab mr note -m "$(cat review.md)"           # GitLab
```

**À éviter :** le lancer en hook `pre-commit` (trop lent) ; en `pre-push`, possible mais ~2 min à chaque push.

## 2. Partager son Ollama sur le réseau local

**Quand :** une machine avec GPU sert le modèle aux autres PC ou runners du réseau.

**Sous Linux natif :**
```sh
OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_HOST=0.0.0.0:11434 ollama serve
```
(avec systemd : `sudo systemctl edit ollama` et ajouter
`Environment="OLLAMA_HOST=0.0.0.0:11434"` et `Environment="OLLAMA_CONTEXT_LENGTH=32768"`.)

**Sous WSL2 (Windows) :** WSL est derrière une NAT, il faut en plus ouvrir le passage côté Windows.

1. Lancer Ollama dans WSL comme ci-dessus (`OLLAMA_HOST=0.0.0.0:11434`).
2. Activer le réseau miroir : dans `C:\Users\<vous>\.wslconfig`
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   puis dans PowerShell : `wsl --shutdown`, et relancer WSL.
   *Alternative sans réseau miroir* (l'IP de WSL change à chaque redémarrage, à refaire) — PowerShell admin :
   ```powershell
   netsh interface portproxy add v4tov4 listenport=11434 listenaddress=0.0.0.0 connectport=11434 connectaddress=<ip-wsl>
   ```
   (`<ip-wsl>` : `hostname -I` dans WSL.)
3. Pare-feu Windows, PowerShell admin, **limité aux IP autorisées** :
   ```powershell
   New-NetFirewallRule -DisplayName "Ollama LAN" -Direction Inbound -Protocol TCP -LocalPort 11434 `
     -RemoteAddress 192.168.1.20,192.168.1.21 -Action Allow
   ```
   En réseau miroir, si ça bloque encore, autoriser aussi le pare-feu Hyper-V de WSL :
   ```powershell
   Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
   ```

**Vérifier**, depuis un autre PC : `curl http://<ip-windows>:11434/v1/models` liste les modèles.

**Sécurité :** Ollama n'a **aucune authentification** : toute machine qui atteint le port peut l'utiliser.
Ne jamais l'exposer sur Internet ; restreindre le pare-feu aux machines voulues.

## 3. À la main depuis un autre PC du réseau

**Quand :** un collègue ou un autre poste, sans GPU, utilise l'Ollama partagé (cas 2) sur son propre clone.

1. Installer `ai-review` comme au cas 1 (Ollama inutile sur ce poste). Si le poste n'a pas accès à Internet,
   copier le dossier ou cloner un miroir interne (voir cas 7).
2. Pointer vers la machine qui sert le modèle :
   ```sh
   export AI_REVIEW_BASE_URL=http://<ip-machine-ollama>:11434/v1
   ai-review
   ```
   (à mettre dans `~/.bashrc` pour ne plus y penser). La commande ne démarre Ollama que pour `localhost`.

Seul le diff et les fichiers lus par l'agent voyagent, entre ce poste et la machine Ollama.

## 4. GitHub Actions + IA en ligne

**Quand :** dépôt GitHub, une IA en ligne acceptable pour la confidentialité du code.

1. Copier [`examples/github-workflow.yml`](../examples/github-workflow.yml) dans `.github/workflows/ai-review.yml`.
2. Dans GitHub → Settings → Secrets and variables → Actions :
   - **Variables** : `AI_REVIEW_BASE_URL` et `AI_REVIEW_MODEL`, par exemple
     `https://api.anthropic.com/v1/` + `claude-sonnet-5-5`, ou `https://api.mistral.ai/v1` + `mistral-large-latest`
     (autres fournisseurs : [README](../README.md)), et pour un modèle qui raisonne (Gemini…)
     `AI_REVIEW_REASONING_EFFORT` = `low`, bien plus rapide ;
   - **Secret** : `AI_REVIEW_API_KEY`, la clé du fournisseur.
3. Pour qu'un 🔴 bloque la fusion : `fail-on-critical: 'true'` dans le workflow, puis rendre le job obligatoire
   (Settings → Branches → règle de protection → *Require status checks*).

**Vérifier :** ouvrir une PR (pas en brouillon) : un commentaire « Défrichage IA » apparaît, puis se met à jour à
chaque push au lieu de s'empiler.

Le diff, la description et les fichiers lus partent chez le fournisseur choisi.

## 5. GitHub Actions + runner auto-hébergé + Ollama

**Quand :** dépôt GitHub, mais l'IA doit rester sur sa machine. Les runners de GitHub ne voient pas votre
`localhost` : le job doit tourner chez vous.

1. Ollama sur la machine du runner (`OLLAMA_CONTEXT_LENGTH=32768 ollama serve`), ou sur le réseau (cas 2).
2. Runner : GitHub → Settings → Actions → Runners → *New self-hosted runner* → Linux x64, copier les commandes
   affichées et ajouter un label à la configuration :
   ```sh
   ./config.sh --url https://github.com/<owner>/<repo> --token <token> --labels ollama
   ./run.sh                                      # ou, en service : sudo ./svc.sh install && sudo ./svc.sh start
   ```
   La machine doit avoir `python3` et `git`.
3. Workflow : [`examples/github-workflow.yml`](../examples/github-workflow.yml) (sa ligne
   `runs-on: ${{ vars.AI_REVIEW_RUNNER || 'ubuntu-24.04' }}` choisit le runner).
4. Variables du dépôt : `AI_REVIEW_RUNNER` = `ollama`, `AI_REVIEW_BASE_URL` = `http://localhost:11434/v1`
   (ou `http://<ip>:11434/v1`), `AI_REVIEW_MODEL` = `qwen3-coder:30b`. Pas de secret.
   Supprimer `AI_REVIEW_RUNNER` ramène sur les runners de GitHub (cas 4).

**Limites :**
- Machine et runner éteints : le job attend dans la file, il n'échoue pas.
- **Dépôt public : déconseillé.** Une PR venue d'un fork exécuterait du code sur votre machine. Réserver aux dépôts
  privés.
- Un modèle local de 30B relève moins de choses qu'un grand modèle en ligne.

## 6. GitLab CI + IA en ligne

**Quand :** gitlab.com ou GitLab auto-hébergé, IA en ligne acceptable.

1. Ajouter le job de [`examples/gitlab-ci.yml`](../examples/gitlab-ci.yml) au `.gitlab-ci.yml`.
2. Settings → CI/CD → Variables : `AI_REVIEW_BASE_URL`, `AI_REVIEW_MODEL`, `AI_REVIEW_API_KEY` (masquée).
3. Jeton pour écrire le commentaire (le `CI_JOB_TOKEN` ne peut pas) : Settings → Access tokens → *project access
   token*, rôle Reporter, scope `api` → variable masquée `AI_REVIEW_GITLAB_TOKEN`.
4. Le job ne tourne que dans les pipelines de merge request (`merge_request_event`).

**Vérifier :** ouvrir une MR : une note « Défrichage IA » apparaît et se met à jour à chaque push ; le rapport est
aussi dans les artefacts du job (`ai-review.md`).

## 7. GitLab privé + runner du réseau + Ollama (tout en interne)

**Quand :** rien ne doit sortir du réseau : GitLab auto-hébergé, runner et Ollama sur le réseau local.
(Avec un dépôt privé sur gitlab.com, le code est déjà chez GitLab ; seule l'IA reste chez vous.)

1. Ollama partagé sur le réseau (cas 2).
2. Miroir interne de l'outil : créer un projet `ai-review` dans votre GitLab et y pousser ce dépôt
   (`git push --mirror`), tags compris (`v1`).
3. GitLab Runner sur une machine du réseau
   ([installation](https://docs.gitlab.com/runner/install/)), enregistré avec un tag :
   ```sh
   sudo gitlab-runner register --url https://gitlab.interne --token <token> --executor docker \
     --docker-image python:3.13 --tag-list ollama
   ```
   Avec l'exécuteur `shell` à la place, la machine doit avoir `python3` et `git`.
4. Job : [`examples/gitlab-ci.yml`](../examples/gitlab-ci.yml), décommenter `tags: [ollama]`.
5. Variables CI/CD du projet :
   - `AI_REVIEW_BASE_URL` = `http://<ip-machine-ollama>:11434/v1` — **pas `localhost`** avec l'exécuteur docker :
     dans le conteneur, `localhost` est le conteneur lui-même ;
   - `AI_REVIEW_MODEL` = `qwen3-coder:30b` ; pas de clé ;
   - `AI_REVIEW_SOURCE` = `https://gitlab.interne/<groupe>/ai-review.git` (le miroir de l'étape 2) ;
   - `AI_REVIEW_GITLAB_TOKEN` comme au cas 6.
6. Si le runner n'a pas d'accès Internet, mettre l'image `python:3.13` dans votre registre interne et changer
   `image:`.

**Vérifier :** ouvrir une MR, la note apparaît ; côté machine Ollama, `ollama ps` montre le modèle chargé pendant
le job.

## 8. Toute autre CI (Jenkins, Forgejo, Woodpecker, Bitbucket…)

Le script ne dépend d'aucune forge : il écrit un rapport, le reste est à la CI.

```sh
export AI_REVIEW_BASE_URL=… AI_REVIEW_MODEL=… AI_REVIEW_API_KEY=…
python3 ai_review.py --base origin/main --description-file pr-description.md --post none \
    --output ai-review.md --json ai-review.json --fail-on-critical
```
- `--base` : le commit de départ de la PR, tel que votre CI l'expose ; récupérer l'historique complet.
- La description : l'écrire dans un fichier depuis la variable de votre CI, ou `AI_REVIEW_DESCRIPTION` /
  `AI_REVIEW_TITLE` en variables d'environnement.
- Poster `ai-review.md` via l'API de la forge, ou l'archiver comme artefact. Code de sortie 2 = 🔴 avec
  `--fail-on-critical`.

Modèle : [`examples/any-ci.sh`](../examples/any-ci.sh).

---

## Dépannage

| Symptôme | Cause et remède |
|---|---|
| L'IA commente du code absent du diff | Contexte Ollama trop petit : le prompt est tronqué. Lancer Ollama avec `OLLAMA_CONTEXT_LENGTH=32768`. |
| « Description fidèle : oui » alors qu'il n'y a pas de description | Les petits modèles sont peu fiables sur ce point : relire soi-même ou prendre un plus grand modèle. |
| `Empty diff: nothing to review.` | Rien de commité depuis la base, ou mauvaise base (`--base`). |
| `unknown revision` / base introuvable | Clone superficiel : `fetch-depth: 0` (GitHub), `GIT_DEPTH: 0` (GitLab), `git fetch origin` en local. |
| Pas de commentaire sur la PR/MR | GitHub : permission `pull-requests: write`. GitLab : `AI_REVIEW_GITLAB_TOKEN` manquant ou rôle insuffisant. |
| `AI provider answered 401/404` | Clé, URL (souvent le `/v1` final) ou identifiant du modèle. |
| `Connection refused` depuis un autre PC | Ollama écoute sur `127.0.0.1` (`OLLAMA_HOST=0.0.0.0:11434`), pare-feu, ou WSL sans réseau miroir. |
| Job GitHub bloqué « Waiting for a runner » | Runner auto-hébergé éteint ou label différent de `AI_REVIEW_RUNNER`. |
| Review très lente (plus de 5 min) | Le journal du job donne la durée de chaque tour. Modèle qui raisonne : `AI_REVIEW_REASONING_EFFORT=low`. Moins de tours : `AI_REVIEW_MAX_TURNS`. Offre gratuite saturée : les relances 429/503 apparaissent dans le journal. |
| Rapport dans la mauvaise langue | `AI_REVIEW_LANGUAGE` ; certains petits modèles l'ignorent parfois. |
