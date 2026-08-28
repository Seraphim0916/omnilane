# Foreman auto-report inbox

## What was built

Every completed dispatch now writes one private, atomic JSON record to `$OMNILANE_HOME/inbox/` from `finish_job()`, the common completion point for foreground jobs, background jobs, and HUP/TERM traps. Records contain routing metadata, final exit status, finish time, and a byte-bounded tail of `out.txt`; they never contain the task prompt. `OMNILANE_INBOX=0` disables producer writes.

A Claude Code `UserPromptSubmit` hook runs `hooks/report-completions.sh`. It claims a pending record with a same-filesystem rename before printing a short completion block, so concurrent consumers deliver a record at most once. Each invocation prints at most ten records, leaves excess records pending, and retains only the 200 newest consumed records. Missing, empty, unsafe, or malformed inbox state fails open without affecting the user's prompt.

## Session targeting

The consumer resolves both paths physically and accepts a record only when its `workdir` equals the consuming session directory (`$CLAUDE_PROJECT_DIR`, otherwise `$PWD`) or is below it at a slash boundary. Thus `/a/b/task` matches `/a/b`, while `/a/bc` does not.

`workdir` was chosen because dispatch already records it, every supported harness has a meaningful current project directory, and no Claude-specific session identifier is available to the dispatch producer. This prevents an unrelated project sharing the same `$OMNILANE_HOME` from draining another project's results. Sessions in the same project intentionally share that project's completion stream; the first hook to claim a record receives it.

## Honest limitations

1. `UserPromptSubmit` fires only when the user types. An idle foreman learns nothing until its next prompt.
2. In Claude Code, a foreground dispatch launched as a background Bash call already receives a harness notification when that Bash call exits. The inbox is primarily valuable for `dispatch.sh --background`, cross-session delivery, and harnesses without Claude Code's foreground-call notification.
