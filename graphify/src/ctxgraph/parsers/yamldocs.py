"""Load YAML documents for the parsers that read values rather than syntax.

Ansible and Docker Compose both keep their structure in the values a document
holds, so both load the file instead of querying its tree. The loader lives
here rather than in either of them, so neither has to import the other.
"""

from __future__ import annotations

from typing import Any

import yaml


class TolerantYamlLoader(yaml.SafeLoader):
    """SafeLoader that tolerates the custom tags a document may carry."""


# `!vault`, `!unsafe` and friends are not worth resolving, but they must not
# abort the load of the file that holds them.
TolerantYamlLoader.add_multi_constructor("", lambda loader, suffix, node: None)


def load_yaml_documents(content: str) -> list[Any] | None:
    """Return the documents of a YAML file, or None when it does not parse."""
    try:
        return list(yaml.load_all(content, Loader=TolerantYamlLoader))
    except (yaml.YAMLError, RecursionError):
        return None
