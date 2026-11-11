"""Small, deterministic query normalizer for technical and descriptive searches."""
import re

STOP = set('a an the how to with in on for of my is are problem problems issue issues'.split())
ALIASES = {'dependencies': 'dependency', 'packages': 'package', 'connections': 'connection',
           'connecting': 'connection', 'connect': 'connection', 'containers': 'container',
           'docker': 'container', 'postgres': 'postgresql', 'databases': 'database',
           'authentication': 'auth', 'authorization': 'auth', 'login': 'auth',
           'errors': 'error', 'types': 'type', 'queries': 'query'}


def terms(text):
    return {ALIASES.get(word, word) for word in re.findall(r'[a-z0-9_+#.-]+', text.casefold())
            if word not in STOP}


def matches(query, topic):
    required = terms(query)
    document = terms(' '.join([topic['name'], topic['description'], topic['category'], *topic['tags']]))
    return not required or required.issubset(document)
