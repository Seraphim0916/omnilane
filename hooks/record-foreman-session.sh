#!/usr/bin/env bash
# Fail-open SessionStart hook: bind Claude's session_id to its process identity.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)" || exit 0
# shellcheck source=../scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh" >/dev/null 2>&1 || exit 0

record_foreman_session() {
  local session_id parent_pid start_time sessions entry tmp old_umask rc=0
  session_id="$(perl -MJSON::PP -0777 -e '
    use strict;
    use warnings;
    my $payload = eval { decode_json(<STDIN>) };
    exit 1 unless ref($payload) eq "HASH";
    my $id = $payload->{session_id};
    exit 1 if ref($id) || !defined($id) ||
      $id !~ /\A[A-Za-z0-9._:-]{1,256}\z/;
    print $id;
  ' 2>/dev/null)" || return 0

  parent_pid="$PPID"
  [[ "$parent_pid" =~ ^[1-9][0-9]{0,9}$ ]] || return 0
  start_time="$(process_start_time "$parent_pid")" || return 0
  sessions="$OMNILANE_HOME/sessions"
  prepare_private_store "$sessions" "session identity store" || return 0
  entry="$sessions/$parent_pid.json"
  tmp="$sessions/.$parent_pid.tmp.$$-$RANDOM"

  old_umask="$(umask)"
  umask 077
  PID_VALUE="$parent_pid" START_VALUE="$start_time" SESSION_VALUE="$session_id" \
    perl -MJSON::PP -e '
      use strict;
      use warnings;
      my $entry = {
        pid => 0 + $ENV{PID_VALUE},
        start_time => $ENV{START_VALUE},
        session_id => $ENV{SESSION_VALUE},
      };
      print JSON::PP->new->canonical->encode($entry), "\n";
    ' > "$tmp" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    chmod 600 "$tmp" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    mv "$tmp" "$entry" || rc=$?
  fi
  umask "$old_umask"
  if [[ "$rc" -ne 0 ]]; then
    rm "$tmp" 2>/dev/null || true
  fi
  return 0
}

record_foreman_session || true
exit 0
