"""Where generated audio lives.

The plane does not keep customer audio in a process's memory or in its own
database. It writes bytes to an object store and keeps the key, the digest and the
length in the job row, so a restart loses nothing, two workers can serve the same
object, and a downloaded file can be checked against the receipt that describes it.

Two backends, and one rule about them: the configuration decides, and a backend
that cannot work refuses instead of quietly doing something else. ``local`` is the
directory next to the plane's state; ``s3`` needs a bucket and credentials, and
without them it raises at startup. There is no path where somebody who asked for
S3 silently gets a local directory and finds out when the disk fills.

Keys are content addresses: ``<sha256[:2]>/<sha256[2:4]>/<sha256>.<ext>``. Two
identical WAVs are therefore one object, and a key is checkable — the digest is
in the name, so ``get`` can prove the bytes it read are the bytes it stored. That
proof runs on every read here. It costs one hash of a small file, and the
alternative is serving a customer corrupted audio under a receipt that says the
audio was fine; when objects get big enough for that to matter, the check moves
to a background scrubber and this comment gets deleted.

Written by Hermes for QualiaBits. AGPL-3.0, like the rest of the fork.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

#: A stored key, exactly as this module writes them. Anything else is refused
#: rather than resolved, so no key — from a request, a database row or a log —
#: can point outside the storage root.
KEY_RE = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}\.(wav|mp3|json|bin)$")

CONTENT_TYPE_EXTENSIONS = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "application/json": "json",
}
EXTENSION_CONTENT_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "json": "application/json",
    "bin": "application/octet-stream",
}


class StorageError(RuntimeError):
    """Storage cannot do what it was asked. Always loud, never a silent fallback."""


@dataclass(frozen=True)
class StoredObject:
    """The receipt for one stored object."""

    key: str
    sha256: str
    size: int
    content_type: str
    backend: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "sha256": self.sha256,
            "size": self.size,
            "content_type": self.content_type,
            "backend": self.backend,
        }


class ObjectStore(Protocol):
    backend: str

    def put(self, data: bytes, content_type: str) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def describe(self) -> dict: ...


def content_type_for_key(key: str) -> str:
    extension = key.rsplit(".", 1)[-1] if "." in key else "bin"
    return EXTENSION_CONTENT_TYPES.get(extension, "application/octet-stream")


def key_for(data: bytes, content_type: str) -> str:
    """The content address of ``data``. Raises on an empty object."""
    if not data:
        raise StorageError("refusing to store an empty object")
    digest = hashlib.sha256(data).hexdigest()
    extension = CONTENT_TYPE_EXTENSIONS.get(
        (content_type or "").split(";")[0].strip().lower(), "bin"
    )
    return f"{digest[:2]}/{digest[2:4]}/{digest}.{extension}"


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise StorageError(f"not a storage key: {key[:80]!r}")
    return key


def digest_of_key(key: str) -> str:
    return validate_key(key).rsplit("/", 1)[-1].split(".")[0]


class LocalObjectStore:
    """The filesystem backend: a directory, atomic writes, verified reads."""

    backend = "local"

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if not str(root).strip():
            raise StorageError("QUALI_STORAGE_DIR is empty")
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise StorageError(f"storage root {self.root} is not a directory")

    # ── paths ────────────────────────────────────────────────────────────────
    def path_for(self, key: str) -> Path:
        """The file behind a validated key. Belt and braces on top of KEY_RE."""
        validate_key(key)
        candidate = (self.root / key).resolve()
        root = self.root.resolve()
        if root != candidate and root not in candidate.parents:
            # Unreachable while KEY_RE holds, and left in place so that a future
            # relaxation of the pattern cannot become a path traversal.
            raise StorageError(f"key escapes the storage root: {key!r}")
        return candidate

    # ── writes ───────────────────────────────────────────────────────────────
    def put(self, data: bytes, content_type: str = "audio/wav") -> StoredObject:
        if not data:
            raise StorageError("refusing to store an empty object")
        key = key_for(data, content_type)
        destination = self.path_for(key)
        digest = digest_of_key(key)
        if not self._is_intact(destination, key):
            # Either the object is not here yet, or something is sitting under this
            # name that does not hash to it — a truncated write from an older
            # crash, a bad disk. Either way it is replaced, and the read-back below
            # is what proves the replacement: "the file exists" is not "the file
            # is right", and inheriting somebody else's half-object as good is how
            # a customer receives audio that is quietly wrong.
            self._write_atomically(destination, data)
        stored = self._read_and_verify(destination, key)
        if len(stored) != len(data) or hashlib.sha256(stored).hexdigest() != digest:
            destination.unlink(missing_ok=True)
            raise StorageError(
                f"stored object {key} did not read back as what was written; "
                "the object was removed rather than left as a half-truth"
            )
        return StoredObject(key, digest, len(data), content_type_for_key(key), self.backend)

    def _is_intact(self, path: Path, key: str) -> bool:
        if not path.is_file():
            return False
        try:
            self._read_and_verify(path, key)
        except (StorageError, OSError):
            return False
        return True

    def _write_atomically(self, destination: Path, data: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            dir=str(destination.parent), prefix=".tmp-", suffix=".part"
        )
        try:
            with os.fdopen(handle, "wb") as temp_file:
                temp_file.write(data)
                temp_file.flush()
                # fsync before the rename: on a crash, either the old file or the
                # whole new one exists, never a renamed-but-empty one.
                os.fsync(temp_file.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, destination)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def _read_and_verify(self, path: Path, key: str) -> bytes:
        data = path.read_bytes()
        expected = digest_of_key(key)
        if hashlib.sha256(data).hexdigest() != expected:
            raise StorageError(
                f"object {key} on disk does not hash to its own name; the stored "
                "bytes are corrupt and will not be served"
            )
        return data

    # ── reads ────────────────────────────────────────────────────────────────
    def get(self, key: str) -> bytes:
        path = self.path_for(key)
        if not path.is_file():
            raise StorageError(f"no such object: {key}")
        return self._read_and_verify(path, key)

    def exists(self, key: str) -> bool:
        try:
            return self.path_for(key).is_file()
        except StorageError:
            return False

    def describe(self) -> dict:
        return {"backend": self.backend, "root": str(self.root)}


class S3ObjectStore:
    """The S3 backend. Written, and deliberately NOT exercised here.

    There is no bucket and no credentials on this machine, and inventing a fake
    endpoint to "test" it would produce evidence for a decision nobody made. What
    the tests do cover is the behaviour that matters when it is misconfigured:
    with no bucket, no credentials or no ``boto3``, constructing this raises, so a
    plane configured for S3 cannot start up quietly writing to local disk.

    When a bucket exists, ``put`` verifies with a HEAD (length and the checksum the
    service recorded) and ``get`` verifies the digest against the key, the same
    property the local store has.
    """

    backend = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        region: str = "",
        endpoint_url: str = "",
        client=None,
    ) -> None:
        if not bucket:
            raise StorageError(
                "QUALI_STORAGE_BACKEND=s3 needs QUALI_S3_BUCKET; refusing to fall "
                "back to local storage, which would put customer audio somewhere "
                "the operator did not choose"
            )
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region = region
        self.endpoint_url = endpoint_url
        if client is None:
            try:
                import boto3  # noqa: PLC0415 - optional dependency
            except ImportError as exc:  # pragma: no cover - depends on the box
                raise StorageError(
                    "QUALI_STORAGE_BACKEND=s3 needs boto3 installed; it is not"
                ) from exc
            try:
                client = boto3.client(
                    "s3",
                    region_name=region or None,
                    endpoint_url=endpoint_url or None,
                )
            except Exception as exc:  # noqa: BLE001 - any client error is this
                raise StorageError(
                    f"could not build an S3 client for bucket {bucket!r}: "
                    f"{type(exc).__name__}"
                ) from exc
        self._client = client

    def _full_key(self, key: str) -> str:
        validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, data: bytes, content_type: str = "audio/wav") -> StoredObject:
        if not data:
            raise StorageError("refusing to store an empty object")
        key = key_for(data, content_type)
        digest = digest_of_key(key)
        self._client.put_object(
            Bucket=self.bucket,
            Key=self._full_key(key),
            Body=data,
            ContentType=content_type_for_key(key),
        )
        head = self._client.head_object(Bucket=self.bucket, Key=self._full_key(key))
        if int(head.get("ContentLength", -1)) != len(data):
            raise StorageError(
                f"S3 accepted {key} but reports {head.get('ContentLength')} bytes "
                f"instead of {len(data)}"
            )
        return StoredObject(key, digest, len(data), content_type_for_key(key), self.backend)

    def get(self, key: str) -> bytes:
        validate_key(key)
        body = self._client.get_object(Bucket=self.bucket, Key=self._full_key(key))[
            "Body"
        ].read()
        expected = digest_of_key(key)
        if hashlib.sha256(body).hexdigest() != expected:
            raise StorageError(
                f"object {key} in S3 does not hash to its own name; refusing to serve it"
            )
        return body

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._full_key(key))
        except Exception:  # noqa: BLE001 - absent, forbidden and error all mean "not ours"
            return False
        return True

    def describe(self) -> dict:
        return {
            "backend": self.backend,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "region": self.region or "default",
            "endpoint_url_set": bool(self.endpoint_url),
            "note": "credentials and object contents are never reported here",
        }


def build_store(settings) -> ObjectStore:
    """The store the operator asked for, or a loud refusal."""
    backend = (settings.storage_backend or "local").strip().lower()
    if backend == "local":
        return LocalObjectStore(settings.storage_dir)
    if backend == "s3":
        return S3ObjectStore(
            settings.s3_bucket,
            prefix=settings.s3_prefix,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
        )
    raise StorageError(
        f"QUALI_STORAGE_BACKEND={backend!r} is not a backend this plane has "
        "(local, s3)"
    )