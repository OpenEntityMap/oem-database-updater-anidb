from oem_framework.core.elapsed import Elapsed
from oem_updater.core.sources.base import Source
from oem_database_updater_anidb.parser import Parser

from xml.etree import ElementTree
import logging
import os
import sys

log = logging.getLogger(__name__)


class AniDB(Source):
    __key__ = 'anidb'
    __parameters__ = [
        {'name': 'source', 'kwargs': {'required': True}}
    ]

    def __init__(self, collection, **kwargs):
        super(AniDB, self).__init__(collection, **kwargs)

        self.seen = {}
        self.updated = {}

    def run(self):
        # Retrieve source path
        source_path = self.param('source')

        if not source_path:
            log.warn('Missing "source" parameter')
            return False

        # Ensure `source_path` exists
        if not os.path.exists(source_path):
            log.warn('Path %r doesn\'t exist', source_path)
            return False

        # Process items
        if not self.process(source_path):
            return False

        return True

    def process(self, source_path):
        items = ElementTree.iterparse(source_path)

        count_total = 0
        count_updated = 0

        for _, item in items:
            if item.tag != 'anime':
                continue

            sys.stdout.write('\r%s - (%05d/%05d)' % (self.collection, count_updated, count_total))
            sys.stdout.flush()

            count_total += 1

            # Process item
            success, updated = self.process_one(item)

            if not success:
                continue

            if updated:
                count_updated += 1

            # if count_updated > 100:
            #     break

        sys.stdout.write('\n')
        return True

    @Elapsed.track
    def process_one(self, node):
        # Parse item into the oem data-structure
        item = Parser.parse_item(self.collection, node)

        if not item:
            # Invalid item for collection
            return True, False

        # Update item (if not already stored)
        return self.update(self.collection.source, node, item)

    @Elapsed.track
    def update(self, service, node, item):
        # Determine item key
        if service == 'anidb':
            key = node.attrib.get('anidbid')
            hash_key = node.attrib.get('imdbid') or node.attrib.get('tvdbid')
        elif service == 'imdb':
            key = node.attrib.get('imdbid')
            hash_key = node.attrib.get('anidbid')
        elif service == 'tvdb':
            key = node.attrib.get('tvdbid')
            hash_key = node.attrib.get('anidbid')
        else:
            raise ValueError('Unknown service: %r' % service)

        # Update items
        updated = False

        for service_key in key.split(','):
            if service_key == 'unknown':
                continue

            # Update item identifiers
            item.identifiers[service] = service_key

            # Process item update
            i_success, i_updated = self.update_one(service, service_key, hash_key, item)

            if not i_success:
                return False, i_updated

            updated |= i_updated

        return True, updated

    @Elapsed.track
    def update_one(self, service, service_key, hash_key, item):
        # Construct hash of `item`
        hash = item.hash()

        # Check if item has already been seen
        if (service, service_key) in self.seen:
            # Add item to `previous` object (and convert to "multiple" structure if needed)
            previous = self.seen[(service, service_key)]

            if not previous.add(item, service):
                return False, False

            # Update `current` item
            current = previous
        else:
            # Update `current` item
            current = item

            # Update seen keys
            self.seen[(service, service_key)] = current

        # Try retrieve item metadata from collection
        metadata = self.collection.get(service_key)

        if metadata:
            # Ensure `item` doesn't match metadata (already up to date)
            if (service, service_key) in self.updated:
                log.debug('Updating item: %s/%s', service, service_key)
            elif metadata.hashes.get(hash_key) == hash:
                return True, False
            elif hash_key in metadata.hashes:
                log.debug('Updating item: %s/%s (%r != %r)', service, service_key, metadata.hashes[hash_key], hash)
        else:
            # Construct new index item
            metadata = self.collection.index.create(service_key)

            # Update index
            self.collection.set(service_key, metadata)

        # Mark item as updated
        self.updated[(service, service_key)] = True

        # Update item
        # log.debug('[%-5s] Updating item: %r (revision: %r)', service, service_key, metadata.revision)

        if not metadata.update(current, hash_key, hash):
            log.warn('[%-5s] Unable to update item: %r (revision: %r)', service, service_key, metadata.revision)

        return True, True