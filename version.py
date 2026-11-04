"""Which Nib this is. The one place the version lives.

It has to match the newest released section of ``CHANGELOG.md`` - a suite checks that - and the tag
a release is built from: ``release.yml`` refuses to build ``v1.2.0`` from a tree that says
``1.1.0`` here, because a release build that believes it is older than it is would offer itself
an update to itself forever (see ``updater.py``).
"""

__version__ = "1.1.0"
