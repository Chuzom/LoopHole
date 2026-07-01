#!/usr/bin/env bash
# Watch loophole work LIVE on a real local LLM — THE FORGE animates in your
# terminal while qwen3-coder writes code, then the real verifier decides "done".
# No API key, no cost (local Ollama). Just run:  bash try-live.sh
set -euo pipefail

LH="$HOME/projects/loophole/.venv/bin/loophole"
MODEL="ollama:qwen3-coder:30b"          # emits real tool calls; devstral:24b also works
export OLLAMA_BASE_URL="http://localhost:11434"

# 1) a throwaway git repo for the agents to work in
WS="$(mktemp -d /tmp/loophole-live.XXXX)"
git -C "$WS" init -q
git -C "$WS" config user.email you@local
git -C "$WS" config user.name you
git -C "$WS" commit -q --allow-empty -m init

echo "▶ workspace: $WS"
echo "▶ model:     $MODEL  (local, \$0)"
echo "▶ watch the swarm below — one clean milestone line per real event…"
sleep 1

# 2) the live run. The default 'stream' view prints an append-only milestone line
#    per real event (add --view forge for the animated dashboard instead).
"$LH" run \
  "Create a file named palindrome.py containing a function is_palindrome(s) that returns True if s is a palindrome ignoring case and non-alphanumeric characters, else False. Write the file directly with the write_file tool; do NOT run any shell commands." \
  --verify '/usr/bin/python3 -c "from palindrome import is_palindrome; assert is_palindrome(\"A man, a plan, a canal: Panama\"); assert not is_palindrome(\"hello\"); print(\"VERIFIED\")"' \
  --planner-model  "$MODEL" \
  --executor-model "$MODEL" \
  --workspace "$WS/work" \
  --db "$WS/state.db" \
  --max-parallel 1 --max-rounds 5 --skip-critique

# 3) show what the model actually wrote and the real git history
echo; echo "════ the code the LLM wrote (merged only because the verifier passed) ════"
cat "$WS/work/palindrome.py" 2>/dev/null || echo "(no file — the verifier refused to certify)"
echo "════ real git history of the run ════"
git -C "$WS/work" log --oneline
