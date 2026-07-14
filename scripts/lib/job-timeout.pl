#!/usr/bin/env perl
use strict;
use warnings;
use POSIX qw(setsid WNOHANG);
use Time::HiRes qw(clock_gettime CLOCK_MONOTONIC sleep);

my $POLL_SECONDS = 0.05;
my $TERM_GRACE_SECONDS = 1.0;

sub usage_error {
    print STDERR "usage: job-timeout.pl SECONDS COMMAND [ARG...]\n";
    exit 2;
}

@ARGV >= 2 or usage_error();
my $seconds = shift @ARGV;
$seconds =~ /\A[1-9][0-9]{0,8}\z/ or usage_error();

sub monotonic_now {
    return clock_gettime(CLOCK_MONOTONIC);
}

sub group_exists {
    my ($pgid) = @_;
    return kill 0, -$pgid;
}

sub reap_nonblocking {
    my ($pid) = @_;
    my $waited = waitpid($pid, WNOHANG);
    return (1, $?) if $waited == $pid;
    return (1, undef) if $waited == -1;
    return (0, undef);
}

sub terminate_group {
    my ($pgid, $pid, $already_reaped) = @_;
    # A signal can arrive in the tiny fork-to-setsid window. Signal the child
    # PID as well as the future group until it is reaped; after reaping, avoid
    # the positive PID because the kernel may reuse it for an unrelated process.
    kill 'TERM', $pid unless $already_reaped;
    kill 'TERM', -$pgid;

    my $grace_deadline = monotonic_now() + $TERM_GRACE_SECONDS;
    my $reaped = $already_reaped;
    while (monotonic_now() < $grace_deadline) {
        my ($done) = reap_nonblocking($pid);
        $reaped = 1 if $done;
        last unless group_exists($pgid);
        sleep($POLL_SECONDS);
    }

    kill 'KILL', -$pgid if group_exists($pgid);
    kill 'KILL', $pid unless $reaped;
    waitpid($pid, 0) unless $reaped;
}

my $forwarded_exit;
$SIG{HUP}  = sub { $forwarded_exit = 129; };
$SIG{INT}  = sub { $forwarded_exit = 130; };
$SIG{TERM} = sub { $forwarded_exit = 143; };

my $deadline = monotonic_now() + $seconds;
my $pid = fork();
if (!defined $pid) {
    print STDERR "omnilane: job supervisor could not fork: $!\n";
    exit 125;
}

if ($pid == 0) {
    $SIG{HUP} = $SIG{INT} = $SIG{TERM} = 'DEFAULT';
    setsid() or do {
        print STDERR "omnilane: job supervisor could not create process group: $!\n";
        exit 125;
    };
    exec { $ARGV[0] } @ARGV or do {
        print STDERR "omnilane: job supervisor could not start command: $!\n";
        exit 127;
    };
}

my $worker_status;
while (1) {
    if (defined $forwarded_exit) {
        terminate_group($pid, $pid, 0);
        exit $forwarded_exit;
    }

    my ($reaped, $status) = reap_nonblocking($pid);
    if ($reaped) {
        $worker_status = $status;
        last;
    }

    my $remaining = $deadline - monotonic_now();
    if ($remaining <= 0) {
        terminate_group($pid, $pid, 0);
        exit 124;
    }
    sleep($remaining < $POLL_SECONDS ? $remaining : $POLL_SECONDS);
}

# A worker can exit while a child it spawned is still alive. The isolated
# process group belongs only to this job, so clean any such remainder before
# reporting completion.
terminate_group($pid, $pid, 1) if group_exists($pid);

defined $worker_status or exit 125;
exit(($worker_status & 127) ? 128 + ($worker_status & 127) : ($worker_status >> 8));
