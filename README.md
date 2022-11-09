# keke

This project is an extremely simple trace-event writer for Python.

You can read the traces in Perfetto or chrome's `about:tracing`.  This only
writes the consensus dialect that works in both, and is tiny enough to just
vendor on the off-chance that you want tracing in the future.

If your needs are more like a line profiler, you might want either pytracing
(slightly abandoned, the git version does work in py3) or viztracer (unsuitable
for vendoring in other projects due to size, but actively maintained).

I drew inspiration from both in writing this.

# Simple Example

```py
from __future__ import annotations  # for IO[str]

from typing import IO, Optional
import time

import click

@click.command()
@click.option("--trace", type=click.File(mode="w"), help="Trace output filename")
@click.option("--foo", help="This value gets logged")
def main(trace: Optional[IO[str]], foo: Optional[str]) -> None:
    with keke.TraceOutput(file=trace):
        with kev("main", __name__, foo=foo):
            sub()

def sub():
    with kev("sub1", __name__):
        time.sleep(1)
    with kev("sub2", __name__):
        time.sleep(2)
```
# Overhead

Very close to zero when not enabled.

The easiest way to not-enable is call `TraceOutput(file=None)` which will do nothing.

# Processes, or "how to get to distributed tracing"

This approach avoids all magic.

If you're calling another (trace-aware) program, then the simplest thing to do
is come up with a unique name and pass that to the child in argv, then attempt
to merge that yourself once it's done.

If you're doing something like fork/spawn to continue python work, then the
parent can control basic information (like the tmpdir to write to) and the child
can open a unique file with its pid.

If you're doing something more distributed, you might come up with a guid and
pass that to the child instead, for the child to tag it for later log uploading.

# What's with the name

I was trying to come up with a short, memorable name and some of the rendered
trace points were very pointy, which reminded me of the "bouba/kiki effect."
The name "kiki" was taken but "keke" was not.

# License

keke is copyright [Tim Hatch](https://timhatch.com/), and licensed under
the MIT license.  I am providing code in this repository to you under an open
source license.  This is my personal repository; the license you receive to
my code is from me and not from my employer. See the `LICENSE` file for details.
