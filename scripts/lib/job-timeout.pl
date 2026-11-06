#!/usr/bin/env perl
use strict;
use warnings;
use POSIX qw(setsid WNOHANG);
use Time::HiRes qw(clock_gettime CLOCK_MONOTONIC sleep);

my $POLL_SECONDS = 0.05;
my $TERM_GRACE_SECONDS = 1.0;

sub usage_error {
    print STDERR "usage: job-timeout.pl SECONDS|--no-deadline COMMAND [ARG...]\n";
    exit 2;
}

@ARGV >= 2 or usage_error();
my $seconds = shift @ARGV;
my $no_deadline = $seconds eq '--no-deadline';
$no_deadline || $seconds =~ /\A[1-9][0-9]{0,8}\z/ or usage_error();

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

# Processes that call setsid leave the job's group, so group signals miss them.
# The supervisor gives its worker a random OMNILANE_JOB_TOKEN that only this
# job's processes inherit. After the group is stopped, job_marker.py lists the
# processes whose environment holds exactly that entry and that started after
# this supervisor; they are stopped and the outcome is kept for `jobs status`.
my $LEFTOVERS_FILE = $ENV{OMNILANE_JOB_LEFTOVERS_FILE} // '';
my $MARKER_HELPER = $ENV{OMNILANE_JOB_MARKER_HELPER} // '';
delete @ENV{qw(OMNILANE_JOB_LEFTOVERS_FILE OMNILANE_JOB_MARKER_HELPER OMNILANE_JOB_TOKEN)};
my $JOB_TOKEN = '';
my $WORKER_NOT_BEFORE_US;  # wall clock, in microseconds, read just before the worker is forked
if ($MARKER_HELPER ne '' && open(my $random, '<:raw', '/dev/urandom')) {
    my $bytes = '';
    $JOB_TOKEN = unpack('H*', $bytes) if read($random, $bytes, 16) == 16;
    close($random);
}

# Returns undef when processes cannot be listed, else {pid => start time}.
sub marked_processes {
    return {} if $JOB_TOKEN eq '';
    my $helper_pid = open(my $scan, '-|', 'python3', '-I', $MARKER_HELPER, 'scan',
                          '--token', $JOB_TOKEN, '--supervisor', $$, '--after', $WORKER_NOT_BEFORE_US);
    defined $helper_pid or return undef;
    my %found;
    while (my $line = <$scan>) {
        my ($pid, $start) = $line =~ /\A(\d+) (\d+)\n?\z/ or next;
        $found{$pid} = $start;
    }
    close($scan) or return undef;
    return \%found;
}

sub record_leftovers {
    my ($reaped, $remaining) = @_;
    return if $LEFTOVERS_FILE eq '';
    my $tmp = "$LEFTOVERS_FILE.tmp.$$";
    open(my $out, '>', $tmp) or return;
    print {$out} "reaped=$reaped remaining=$remaining\n";
    close($out) or return;
    rename($tmp, $LEFTOVERS_FILE);
}

sub same_process {  # pid => start time, as listed now and as listed before
    my ($now, $before) = @_;
    return grep { defined $now->{$_} && $now->{$_} eq $before->{$_} } keys %$before;
}

sub reap_marked {
    my ($stop) = @_;
    return if $JOB_TOKEN eq '';
    my $found = marked_processes();
    if (!defined $found) {
        record_leftovers('unknown', 'unknown');
        return;
    }
    my $count = scalar keys %$found;
    if (!$stop) {
        record_leftovers(0, $count);
        return;
    }
    kill 'TERM', keys %$found if $count;
    my $grace_deadline = monotonic_now() + $TERM_GRACE_SECONDS;
    my %pending = %$found;
    while (%pending && monotonic_now() < $grace_deadline) {
        delete @pending{ grep { !kill(0, $_) } keys %pending };
        sleep($POLL_SECONDS) if %pending;
    }
    if (%pending) {
        # SIGKILL only a PID listed again with the same start time; a reused PID is left alone.
        my $again = marked_processes();
        my @kill = defined $again ? same_process($again, \%pending) : ();
        kill 'KILL', @kill if @kill;
        sleep($POLL_SECONDS * 4) if @kill;
    }
    # Count everything the last listing still finds, including processes started
    # after the first listing (a helper can answer TERM by starting a replacement).
    my $after = marked_processes();
    my $remaining = defined $after ? scalar(keys %$after) : 'unknown';
    record_leftovers($count, $remaining);
}
my $forwarded_exit;
$SIG{HUP}  = sub { $forwarded_exit = 129; };
$SIG{INT}  = sub { $forwarded_exit = 130; };
$SIG{TERM} = sub { $forwarded_exit = 143; };

# Background jobs still need group cleanup when no whole-job budget was set.
my $deadline = $no_deadline ? undef : monotonic_now() + $seconds;
$WORKER_NOT_BEFORE_US = int(Time::HiRes::time() * 1_000_000);
my $pid = fork();
if (!defined $pid) {
    print STDERR "omnilane: job supervisor could not fork: $!\n";
    exit 125;
}

if ($pid == 0) {
    $SIG{HUP} = $SIG{INT} = $SIG{TERM} = 'DEFAULT';
    $ENV{OMNILANE_JOB_TOKEN} = $JOB_TOKEN if $JOB_TOKEN ne '';
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
        reap_marked(1);
        exit $forwarded_exit;
    }

    my ($reaped, $status) = reap_nonblocking($pid);
    if ($reaped) {
        $worker_status = $status;
        last;
    }

    my $remaining = defined($deadline) ? $deadline - monotonic_now() : $POLL_SECONDS;
    if (defined($deadline) && $remaining <= 0) {
        terminate_group($pid, $pid, 0);
        reap_marked(1);
        exit 124;
    }
    sleep($remaining < $POLL_SECONDS ? $remaining : $POLL_SECONDS);
}

# A worker can exit while a child it spawned is still alive. The isolated
# process group belongs only to this job, so clean any such remainder before
# reporting completion.
terminate_group($pid, $pid, 1) if group_exists($pid);
# A normal finish may leave a deliberate background helper; count it, do not stop it.
reap_marked(0);

defined $worker_status or exit 125;
exit(($worker_status & 127) ? 128 + ($worker_status & 127) : ($worker_status >> 8));
