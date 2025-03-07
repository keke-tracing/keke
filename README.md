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

The simplest thing, if you want to avoid all magic, is set an environment
variable in the child process asking it nicely to store its trace in a known
place, then merge all those files at the end.

If you control the initializer (say, with a multiprocessing.Pool or
concurrent.futures.ProcessPool you are in charge of), then you can also start a
thread and send events over a pipe.  See `test_we_get_events_from_child` for
some ideas, but this does require a background thread in the parent (and
probably gets unwieldy with children-of-children).

# What's with the name

I was trying to come up with a short, memorable name and some of the rendered
trace points were very pointy, which reminded me of the "bouba/kiki effect."
The name "kiki" was taken but "keke" was not.

# Version Compat

Usage of this library should work back to 3.7, but development (and mypy
compatibility) only on 3.10-3.12.  Linting requires 3.12 for full fidelity.

# Versioning

This library follows [meanver](https://meanver.org/) which basically means
[semver](https://semver.org/) along with a promise to rename when the major
version changes.

# License

keke is copyright [Tim Hatch](https://timhatch.com/), and licensed under
the MIT license.  See the `LICENSE` file for details.
