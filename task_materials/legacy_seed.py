"""Write synthetic cache input in the unmodified Scrapy 2.13.3 format.

This is an old-format fixture writer, not a Vary cache implementation.
Layout is taken from the upstream FilesystemCacheStorage/DbmCacheStorage.
"""

import dbm.dumb
import gzip
import pickle
import time
from pathlib import Path

from w3lib.http import headers_dict_to_raw


def seed_legacy(storage_kind, root, spider_name, fingerprint, request, response,
                use_gzip=False):
    """storage_kind is 'filesystem' or 'dbm'; fingerprint is raw bytes.

    Call with a closed target store. Only write controlled, local fixture data.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    key = fingerprint.hex()
    now = time.time()
    if storage_kind == "dbm":
        data = {
            "status": response.status,
            "url": response.url,
            "headers": dict(response.headers),
            "body": response.body,
        }
        with dbm.dumb.open(str(root / f"{spider_name}.db"), "c") as database:
            database[f"{key}_data"] = pickle.dumps(data, protocol=4)
            database[f"{key}_time"] = str(now)
        return
    if storage_kind != "filesystem":
        raise ValueError(storage_kind)
    path = root / spider_name / key[:2] / key
    path.mkdir(parents=True, exist_ok=True)
    metadata = {
        "url": request.url,
        "method": request.method,
        "status": response.status,
        "response_url": response.url,
        "timestamp": now,
    }
    files = {
        "meta": repr(metadata).encode(),
        "pickled_meta": pickle.dumps(metadata, protocol=4),
        "response_headers": headers_dict_to_raw(response.headers),
        "response_body": response.body,
        "request_headers": headers_dict_to_raw(request.headers),
        "request_body": request.body,
    }
    opener = gzip.open if use_gzip else open
    for name, content in files.items():
        with opener(path / name, "wb") as stream:
            stream.write(content)
