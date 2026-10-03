#!/usr/bin/env perl
use strict;
use warnings;
use Errno qw(EINTR);
use POSIX qw(WNOHANG);
use Time::HiRes qw(clock_gettime CLOCK_MONOTONIC sleep);

# A per-call deadline owned by a separate process, not by the invoked program.
# Keep the outer supervisor's process group: it owns existing whole-job process-group cleanup.
@ARGV >= 2 or die "usage: call-timeout.pl SECONDS COMMAND [ARG...]\n";
my $seconds = shift @ARGV;
$seconds =~ /\A[1-9][0-9]*\z/ or die "invalid per-call timeout\n";
my $deadline = clock_gettime(CLOCK_MONOTONIC) + $seconds;
my $forwarded;
# Establish sole wait ownership even if our caller ignored SIGCHLD.
$SIG{CHLD} = 'DEFAULT';
$SIG{HUP} = sub { $forwarded = 129 unless defined $forwarded; };
$SIG{INT} = sub { $forwarded = 130 unless defined $forwarded; };
$SIG{TERM} = sub { $forwarded = 143 unless defined $forwarded; };
my $pid = fork();
if (!defined $pid) {
    print STDERR "omnilane: per-call watchdog could not fork: $!\n";
    exit 125;
}
if ($pid == 0) {
    $SIG{HUP} = $SIG{INT} = $SIG{TERM} = 'DEFAULT';
    exec { $ARGV[0] } @ARGV or do {
        print STDERR "omnilane: per-call watchdog could not start command: $!\n";
        exit 127;
    };
}

sub reap {
    while (1) {
        my $waited = waitpid($pid, WNOHANG);
        next if $waited == -1 && $! == EINTR;
        return (1, $?) if $waited == $pid;
        return (1, undef) if $waited == -1;
        return (0, undef);
    }
}

sub stop_child {
    # This process is the sole reaper. Until waitpid succeeds, this numeric PID
    # cannot refer to a new unrelated process. Never signal after reaping it.
    my ($done, $status) = reap();
    return defined($status) if $done;
    kill 'TERM', $pid;
    my $grace = clock_gettime(CLOCK_MONOTONIC) + 1.0;
    while (clock_gettime(CLOCK_MONOTONIC) < $grace) {
        ($done, $status) = reap();
        return defined($status) if $done;
        sleep 0.02;
    }
    if (!kill('KILL', $pid)) {
        ($done, $status) = reap();
        return $done && defined($status);
    }
    while (1) {
        my $waited = waitpid($pid, 0);
        next if $waited == -1 && $! == EINTR;
        return $waited == $pid;
    }
}

sub require_stopped_child {
    return if stop_child();
    print STDERR "omnilane: per-call watchdog could not confirm child cleanup\n";
    exit 125;
}

while (1) {
    if (defined $forwarded) {
        require_stopped_child();
        exit $forwarded;
    }
    my ($done, $status) = reap();
    if ($done) {
        defined $status or exit 125;
        exit(($status & 127) ? 128 + ($status & 127) : ($status >> 8));
    }
    if (clock_gettime(CLOCK_MONOTONIC) >= $deadline) {
        require_stopped_child();
        # Preserve the supervised per-call SIGALRM status. In particular this
        # must not masquerade as the outer whole-job deadline's exit 124.
        exit 142;
    }
    my $remaining = $deadline - clock_gettime(CLOCK_MONOTONIC);
    sleep($remaining > 0.02 ? 0.02 : $remaining) if $remaining > 0;
}
