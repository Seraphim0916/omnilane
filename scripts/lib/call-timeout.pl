#!/usr/bin/env perl
use strict;
use warnings;
use File::Basename qw(dirname);
my $directory = dirname(__FILE__);
# Keep the public Perl entry point; the shared supervisor owns process identity,
# the monotonic deadline, and bounded cleanup. Never fall back to an unguarded call.
exec 'python3', "$directory/process_tree.py", 'call', @ARGV or do {
    print STDERR "omnilane: process supervision requires python3: $!\n";
    exit 125;
};
