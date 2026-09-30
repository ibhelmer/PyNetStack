# Contributing

Use English identifiers, comments, docstrings, messages and documentation.
Keep the implementation inspectable: prefer small modules and explicit packet
formats over hidden operating-system networking behavior.

Run `python -m unittest discover -s tests -v` and both documented demo modes
before proposing changes. Add a regression test for a corrected protocol bug.
Update the wire-protocol document when changing frame fields or semantics.

Preserve applicable copyright and license notices, and retain SPDX identifiers.
Do not copy third-party source code without checking and retaining its license.
Describe which changes are simulated, hardware-tested or still experimental.

See LICENSE for the Apache License 2.0 terms and NOTICE for project attribution.
