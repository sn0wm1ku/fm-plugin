#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
fm available
answer=$(fm respond --model system --no-stream --greedy --text 'What is 2 plus 2? Reply with the digit only.')
test "$answer" = 4
echo 'PASS: native fm invocation returned the expected answer'
"${PYTHON:-python3}" "$root/tests/test_sdk.py" --live
