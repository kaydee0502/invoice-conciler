#!/usr/bin/env bash
# Majority of A, B, C. No majority -> non-zero exit, run stops.
exec "$(dirname "$0")/_compare.sh" vote_report 1 spec_a spec_b spec_c
