# jev-gate

PreToolUse hook that decides every Claude Code tool call: **allow**, **ask** (permission prompt), or **deny** (blocked; Claude sees the reason).
Default allow: it only stops calls that go against your instructions or are clearly harmful. It replaces the Anthropic auto-mode classifier so sessions can run in bypass mode.

1. Hard rules in `rules.toml` (regex), severity order deny > ask > check > allow. Anything that must never depend on a model goes here. `check` rules (e.g. motor scripts) always go to Jev and run only if Jev is sure the user authorized that run (P(fine) ≥ 0.8, `JEV_GATE_CHECK_P`); otherwise ask.
2. Everything else defaults to **allow**. Jev (TypeSafe `/v1/systemone`) answers two `choice` questions in one request: does the call go against one of `instructions` or the user's messages (fine / conflicts / harmful), and which instruction. Jev sees the tool call (secrets redacted), the cwd and the conversation: all of the user's messages in the session (newest first, up to 12k chars), the agent reply before each of the last 3 user messages, and, in a subagent, the brief it was launched with. The agent text is there so short replies ("sure", "work on all three") have a referent and subagent work counts as in scope. It never counts as authorization. Tool results are never sent, because they can carry injected instructions. `JEV_GATE_AGENT_TEXT=0` sends the user's messages only.
   The user's messages outrank the standing `instructions`, and later messages outrank earlier ones: "run the full suite here" makes a compute-heavy job fine. Physical motion still needs the user to authorize that specific run.
3. deny if P(harmful) ≥ 0.7 (`JEV_GATE_DENY_P`); ask if P(conflicts)+P(harmful) ≥ 0.6 (`JEV_GATE_ASK_P`); else allow. The prompt reason names the instruction.

Rule hits take ~15 ms, Jev decisions ~150–300 ms.

Fails closed: an exception → ask; a crash, timeout or bad output → blocked (`onFailure: "block"` in settings). No network → every call that no rule covers prompts. In bypass mode a hook `ask` is still enforced (headless `-p` sessions block the call).

## Setup

```sh
mkdir -p ~/.config/jev-gate
cp rules.example.toml ~/.config/jev-gate/rules.toml     # or point JEV_GATE_RULES at a private repo
cp cases.example.toml ~/.config/jev-gate/cases.toml
ln -s "$PWD/jev_gate.py" ~/.local/bin/jev-gate
jev-gate eval
```

Needs `uv` and a TypeSafe key: `TYPESAFE_API_KEY`, or Jevgrep's saved key (`jg auth --provider typesafe`).

`~/.claude/settings.json` → `hooks.PreToolUse` (and `"permissions": {"defaultMode": "bypassPermissions"}` to make it the only gate):

```json
{"matcher": "*", "hooks": [{"type": "command", "timeout": 20, "onFailure": "block",
  "command": "uv run -q --script /path/to/personal-tools/jev-gate/jev_gate.py"}]}
```

## Use

```sh
jev-gate test 'git push origin main' --say 'push it'   # try a call without running it
jev-gate test 'git commit -am fix' --say 'sure' --replied 'Fix the three bugs?'   # --brief 'subagent brief' too
jev-gate log -n 50 --source jev               # recent Jev decisions with P(fine) and latency
jev-gate eval                                 # cases.toml regression set
JEV_GATE_SHADOW=1 claude                      # log verdicts only; normal permission mode decides
```

Log: `~/.local/state/jev-gate/log.db` (sqlite, table `log`).

## Growing the rules

When the gate gets a call wrong: add it to `cases.toml`, then add or reword an entry in `instructions` (judgement calls) or add a `[[rule]]` (must never depend on a model), and rerun `eval` until it passes.
Keep instructions narrow and say what does *not* count — Jev over-applies broad wording (an unqualified "compute-heavy" rule flagged `sed -i`).
Edits to the rules, `jev_gate.py` and `~/.claude/settings*.json` always ask, so Claude cannot loosen the gate on its own.

## Privacy

Every tool call that no hard rule covers is sent to `api.typesafe.ai`: tool name and input (commands, paths, edit contents, truncated to 12k chars, common secret formats redacted), the cwd, your messages from the session, and recent agent replies and subagent briefs (secrets redacted).
