#!/bin/zsh
# Everything the web page has for lint, typecheck and tests, one line per step. Touches no iPod and no network:
# the tests run on the fake (dev/fake_ipodctl.py). Tools/check.sh is the Swift side.
set -u
cd "${0:A:h}/.."
failed=0
step() { local name=$1; shift; if out=$("$@" 2>&1); then print -r -- "OK    $name"; else print -r -- "FAIL  $name"; print -r -- "$out" | tail -25; failed=1; fi; }
scratch=$(mktemp -d); trap 'rm -rf "$scratch"' EXIT
compile() { local python=$1; shift; PYTHONPYCACHEPREFIX=$scratch $python -W error -m py_compile "$@"; }
sources=(web.py enrich.py meta.py dev/serve.py dev/fake_ipodctl.py tests/harness.py tests/test_*.py)
step "python syntax ($(python3 --version))" compile python3 $sources
[[ -x /usr/bin/python3 ]] && step "python syntax (oldest supported: $(/usr/bin/python3 --version 2>&1))" compile /usr/bin/python3 $sources
script=$scratch/page.mjs
python3 -c "import re,sys; print(re.findall(r'<script>(.*?)</script>', open('index.html').read(), re.S)[-1])" > $script
step "page script syntax" node --check $script
step "page names are unique" python3 -c "
import collections, re
s = open('index.html').read()
names = re.findall(r'^(?:async )?function (\\w+)\\(|^(?:const|let) (\\w+)\\b', s, re.M)
twice = [n for n, c in collections.Counter(a or b for a, b in names).items() if c > 1]
assert not twice, twice"  # the later of two functions wins without a word, and syntax checks can't see it
step "css braces balance" python3 -c "
import re; s=re.sub(r'/\*.*?\*/','',open('style.css').read(),flags=re.S); d=0
for c in s:
    d+= (c=='{')-(c=='}')
    assert d>=0, 'a stray }'
assert d==0, 'an unclosed {'"
step "tests" env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t tests
exit $failed
