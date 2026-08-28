#!/usr/bin/env bash
set -euo pipefail
unset OMNILANE_DEPTH

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omnilane-inbox-tests.XXXXXX")"

cleanup() {
  /bin/rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT

fail() {
  printf 'not ok - foreman completion inbox: %s\n' "$1" >&2
  exit 1
}

wait_for_file() {
  local path="$1" tries=0
  while [[ ! -f "$path" && "$tries" -lt 100 ]]; do
    sleep 0.1
    tries=$((tries + 1))
  done
  [[ -f "$path" ]]
}

make_gate() {
  local path="$1"
  cat > "$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
output_file="$5"
printf 'prompt must stay private: ' > "$output_file"
perl -e 'print "x" x 2200' >> "$output_file"
printf '\nTHE-END\n' >> "$output_file"
exit "${GATE_EXIT:-0}"
EOF
  chmod +x "$path"
}

record_json() {
  local path="$1" job_id="$2" workdir="$3" exit_code="${4:-0}"
  JOB_ID="$job_id" WORKDIR_VALUE="$workdir" EXIT_VALUE="$exit_code" \
    perl -MJSON::PP -e '
      my $record = {
        job_id => $ENV{JOB_ID}, lane => "triage", vendor => "exec",
        model => "/tmp/gate", mode => "advise",
        workdir => $ENV{WORKDIR_VALUE}, exit => 0 + $ENV{EXIT_VALUE},
        finished => "2026-08-28T00:00:00Z", tail => "tail-$ENV{JOB_ID}"
      };
      open my $fh, ">", $ARGV[0] or die $!;
      print {$fh} JSON::PP->new->canonical->encode($record), "\n";
      close $fh or die $!;
    ' "$path"
  chmod 600 "$path"
}

[[ -x "$ROOT/hooks/report-completions.sh" ]] || fail "consumer is missing or not executable"
grep -q '"UserPromptSubmit"' "$ROOT/hooks/hooks.json" || fail "UserPromptSubmit hook is not registered"

home="$TEST_ROOT/home"
workdir="$TEST_ROOT/project"$'\n'"quote-tab"$'\t'
gate="$TEST_ROOT/gate.sh"
mkdir -p "$home" "$workdir"
make_gate "$gate"
printf 'triage: exec "%s" -\n' "$gate" > "$home/routing.local.yaml"

job_id="$(OMNILANE_HOME="$home" GATE_EXIT=7 \
  bash "$ROOT/scripts/dispatch.sh" --background --workdir "$workdir" \
  triage 'TOP-SECRET-PROMPT')"
[[ "$job_id" =~ ^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9]+$ ]] || fail "background stdout was not only a job id: $job_id"
wait_for_file "$home/jobs/$job_id/exit" || fail "background job never wrote exit"
[[ "$(cat "$home/jobs/$job_id/exit")" == "7" ]] || fail "job exit was not 7"

record="$home/inbox/$job_id.json"
wait_for_file "$record" || fail "producer did not finish the atomic record write"
[[ -f "$record" && ! -L "$record" ]] || fail "producer did not create one real record"
[[ "$(find "$home/inbox" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')" == "1" ]] || fail "producer created more than one record"
[[ "$(stat -f '%Lp' "$home/inbox" 2>/dev/null || stat -c '%a' "$home/inbox")" == "700" ]] || fail "inbox mode is not 700"
[[ "$(stat -f '%Lp' "$record" 2>/dev/null || stat -c '%a' "$record")" == "600" ]] || fail "record mode is not 600"

RECORD="$record" EXPECTED_ID="$job_id" EXPECTED_WORKDIR="$workdir" perl -MJSON::PP -e '
  use bytes;
  open my $fh, "<", $ENV{RECORD} or die $!;
  local $/; my $raw = <$fh>;
  die "record is not one line\n" unless $raw =~ /\A[^\n]*\n?\z/;
  my $r = decode_json($raw);
  my @want = qw(job_id lane vendor model mode workdir exit finished tail);
  die "wrong keys\n" unless join(",", sort keys %$r) eq join(",", sort @want);
  die "wrong id\n" unless $r->{job_id} eq $ENV{EXPECTED_ID};
  die "wrong workdir\n" unless $r->{workdir} eq $ENV{EXPECTED_WORKDIR};
  die "wrong exit\n" unless $r->{exit} == 7;
  die "prompt leaked\n" if $raw =~ /TOP-SECRET-PROMPT/;
  die "tail over byte cap\n" if length($r->{tail}) > 2000;
  die "missing truncation note\n" unless $r->{tail} =~ /truncat/i;
  die "tail lost suffix\n" unless $r->{tail} =~ /THE-END\n\z/;
' || fail "record JSON, privacy, or byte cap is wrong"

first="$(OMNILANE_HOME="$home" CLAUDE_PROJECT_DIR="$workdir" "$ROOT/hooks/report-completions.sh")"
[[ "$first" == *"$job_id"* && "$first" == *"triage"* && "$first" == *"exec"* && "$first" == *"FAILED"* && "$first" == *"exit=7"* && "$first" == *"THE-END"* ]] || fail "first delivery is incomplete: $first"
second="$(OMNILANE_HOME="$home" CLAUDE_PROJECT_DIR="$workdir" "$ROOT/hooks/report-completions.sh")"
[[ -z "$second" ]] || fail "record was delivered twice: $second"
[[ -f "$home/inbox/consumed/$job_id.json" ]] || fail "claimed record was not moved to consumed"

unrelated="$TEST_ROOT/unrelated"
mkdir -p "$unrelated"
unrelated_id="20260828-000001-1-1"
record_json "$home/inbox/$unrelated_id.json" "$unrelated_id" "$unrelated"
targeted="$(OMNILANE_HOME="$home" CLAUDE_PROJECT_DIR="$workdir" "$ROOT/hooks/report-completions.sh")"
[[ -z "$targeted" && -f "$home/inbox/$unrelated_id.json" ]] || fail "unrelated record was claimed"

prefix="$TEST_ROOT/project-other"
mkdir -p "$prefix"
prefix_id="20260828-000002-1-1"
record_json "$home/inbox/$prefix_id.json" "$prefix_id" "$prefix"
prefix_out="$(OMNILANE_HOME="$home" CLAUDE_PROJECT_DIR="$workdir" "$ROOT/hooks/report-completions.sh")"
[[ -z "$prefix_out" && -f "$home/inbox/$prefix_id.json" ]] || fail "path-prefix collision was claimed"

subdir="$workdir/subdir"
mkdir -p "$subdir"
subdir_id="20260828-000003-1-1"
record_json "$home/inbox/$subdir_id.json" "$subdir_id" "$subdir"
subdir_out="$(OMNILANE_HOME="$home" CLAUDE_PROJECT_DIR="$workdir" "$ROOT/hooks/report-completions.sh")"
[[ "$subdir_out" == *"$subdir_id"* ]] || fail "subdirectory record was not delivered"

bounded_home="$TEST_ROOT/bounded"
mkdir -p "$bounded_home/inbox/consumed"
chmod 700 "$bounded_home/inbox" "$bounded_home/inbox/consumed"
for n in $(seq 1 12); do
  id="$(printf '20260828-0100%02d-1-1' "$n")"
  record_json "$bounded_home/inbox/$id.json" "$id" "$workdir"
done
bounded="$(OMNILANE_HOME="$bounded_home" CLAUDE_PROJECT_DIR="$workdir" "$ROOT/hooks/report-completions.sh")"
blocks="$(printf '%s\n' "$bounded" | grep -c '^Omnilane completion:')"
[[ "$blocks" == "10" ]] || fail "consumer printed $blocks records instead of 10"
[[ "$bounded" == *"2 matching completion records withheld"* ]] || fail "withheld count missing: $bounded"
last_header="$(printf '%s\n' "$bounded" | grep '^Omnilane completion:' | tail -n 1)"
[[ "$last_header" == *"20260828-010010-1-1"* ]] || fail "records were not printed oldest-to-newest: $last_header"
[[ "$(find "$bounded_home/inbox" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')" == "2" ]] || fail "withheld records were claimed"

for n in $(seq 1 205); do
  : > "$bounded_home/inbox/consumed/$(printf '20260827-0000%03d-1-1.json' "$n")"
done
retention_id="20260828-020000-1-1"
record_json "$bounded_home/inbox/$retention_id.json" "$retention_id" "$workdir"
OMNILANE_HOME="$bounded_home" CLAUDE_PROJECT_DIR="$workdir" "$ROOT/hooks/report-completions.sh" >/dev/null
[[ "$(find "$bounded_home/inbox/consumed" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')" == "200" ]] || fail "consumed retention is not 200"
[[ ! -e "$bounded_home/inbox/consumed/20260827-0000001-1-1.json" ]] || fail "oldest consumed record was retained"
[[ -e "$bounded_home/inbox/consumed/20260827-0000205-1-1.json" && -e "$bounded_home/inbox/consumed/$retention_id.json" ]] || fail "recent consumed records were pruned"

optout="$TEST_ROOT/optout"
mkdir -p "$optout"
printf 'triage: exec "%s" -\n' "$gate" > "$optout/routing.local.yaml"
optout_id="$(OMNILANE_HOME="$optout" OMNILANE_INBOX=0 \
  bash "$ROOT/scripts/dispatch.sh" --background --workdir "$workdir" \
  triage 'opt out')"
wait_for_file "$optout/jobs/$optout_id/exit" || fail "opt-out job never finished"
[[ ! -e "$optout/inbox" ]] || fail "OMNILANE_INBOX=0 still created inbox"

unsafe="$TEST_ROOT/unsafe"
foreign="$TEST_ROOT/foreign"
mkdir -p "$unsafe" "$foreign"
printf 'triage: exec "%s" -\n' "$gate" > "$unsafe/routing.local.yaml"
ln -s "$foreign" "$unsafe/inbox"
unsafe_id="$(OMNILANE_HOME="$unsafe" \
  bash "$ROOT/scripts/dispatch.sh" --background --workdir "$workdir" \
  triage 'unsafe inbox')"
wait_for_file "$unsafe/jobs/$unsafe_id/exit" || fail "unsafe-store job never finished"
[[ "$(cat "$unsafe/jobs/$unsafe_id/exit")" == "0" ]] || fail "inbox failure changed dispatch exit"
[[ -z "$(find "$foreign" -mindepth 1 -print -quit)" ]] || fail "producer wrote through inbox symlink"

nondir="$TEST_ROOT/non-directory"
mkdir -p "$nondir"
printf 'triage: exec "%s" -\n' "$gate" > "$nondir/routing.local.yaml"
printf 'not a directory\n' > "$nondir/inbox"
nondir_id="$(OMNILANE_HOME="$nondir" \
  bash "$ROOT/scripts/dispatch.sh" --background --workdir "$workdir" \
  triage 'non-directory inbox')"
wait_for_file "$nondir/jobs/$nondir_id/exit" || fail "non-directory-store job never finished"
[[ "$(cat "$nondir/jobs/$nondir_id/exit")" == "0" && -f "$nondir/inbox" ]] || fail "non-directory inbox changed dispatch behavior"

empty_home="$TEST_ROOT/empty"
mkdir -p "$empty_home/inbox"
chmod 700 "$empty_home/inbox"
empty="$(OMNILANE_HOME="$empty_home" CLAUDE_PROJECT_DIR="$workdir" "$ROOT/hooks/report-completions.sh")"
[[ -z "$empty" ]] || fail "empty inbox printed output"

missing="$(OMNILANE_HOME="$TEST_ROOT/missing" CLAUDE_PROJECT_DIR="$workdir" "$ROOT/hooks/report-completions.sh")"
[[ -z "$missing" ]] || fail "missing inbox printed output"

printf 'ok - foreman completion inbox\n'
