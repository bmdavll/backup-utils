#!/usr/bin/env python3

import sys, os
from os import path

try:
    prev_file, prev_dir = None, None
    count = 0

    def flush():
        global prev_file, prev_dir, count

        if prev_file is not None:
            if count > 1:
                print(path.join(prev_dir, "*"), "[%d]" % count)
            else:
                print(prev_file)
            prev_file, prev_dir = None, None
            count = 0

    for line in sys.stdin:

        line = line.rstrip('\n')
        if not line:
            flush()
            print()
            continue

        line = line.rstrip(os.sep)
        dirname = path.dirname(line)

        if not line:
            flush()
            print(os.sep)

        elif not dirname:
            flush()
            print(line)

        elif dirname != prev_dir:
            flush()
            prev_file, prev_dir = line, dirname
            count += 1

        else:
            count += 1

    flush()

except KeyboardInterrupt:
    sys.exit(1)
