#!/bin/sh
# Any other CI (Jenkins, Woodpecker, Forgejo, Bitbucket…) or a laptop: python3 + git, then do what you want with
# the report (post it through your forge's API, archive it). Exit code 2 = critical finding with --fail-on-critical.
export AI_REVIEW_BASE_URL=https://api.mistral.ai/v1 AI_REVIEW_MODEL=mistral-large-latest AI_REVIEW_API_KEY="$KEY"
python3 ai_review.py --base origin/main --description-file pr-description.md --post none \
    --output ai-review.md --json ai-review.json --fail-on-critical
