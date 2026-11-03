#!/bin/zsh
# Everything: the Swift tests, then what the web page has for syntax and tests. Touches no iPod and no network.
set -u
cd "${0:A:h}/.."
failed=0
print -r -- "== Swift"
swift test 2>&1 | tail -3 || failed=1
[[ ${pipestatus[1]} == 0 ]] || failed=1
print -r -- "== Web page"
web/dev/check.sh || failed=1
exit $failed
