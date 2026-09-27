#!/usr/bin/env bash
# A vs B. Records spec_agreed; disagreement routes to the tie-break.
exec "$(dirname "$0")/_compare.sh" compare_report 0 spec_a spec_b
