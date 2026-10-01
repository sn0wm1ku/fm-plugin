#!/bin/sh
set -eu
fm available
answer=$(fm respond --model system --no-stream --greedy --text 'What is 2 plus 2? Reply with the digit only.')
test "$answer" = 4
echo 'PASS: native fm invocation returned the expected answer'
