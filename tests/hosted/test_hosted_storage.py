"""The object store: content addressing, atomic writes, and loud refusals.

The local provider is exercised for real — a real directory, real bytes read back
off the disk. The S3 provider is exercised twice: its logic against a stub client,
and then end to end over real HTTP against a local S3-compatible server (``moto``,
run in-process). Neither is a bucket. There are no credentials for one on this
machine, so nothing here proves S3's own behaviour or a real bucket's permissions;
what the emulator tests do prove is our side — that the client our configuration
builds really signs requests, that objects land and read back over the wire, and
that the length and digest checks survive the round trip. The e2e run uses the real
local store; no S3 run has ever happened, and the README says so.
"""
from __future__ import annotations

import hashlib
import io
import os
import struct
import wave
from pathlib import Path

import pytest

from hosted.control_plane.config import Settings
from hosted.control_plane.storage import (
    LocalObjectStore,
    S3ObjectStore,
    StorageError,
    build_store,
    content_type_for_key,
    key_for,
)


def wav_bytes(seconds: float = 0.5, sample_rate: int = 24000) -> bytes:
    frames = int(round(seconds * sample_rate))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(struct.pack("<h", 900) * frames)
    return buffer.getvalue()


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


def test_a_stored_object_reads_back_as_the_bytes_that_were_written(store):
    data = wav_bytes()
    stored = store.put(data, "audio/wav")

    digest = hashlib.sha256(data).hexdigest()
    assert stored.key == f"{digest[:2]}/{digest[2:4]}/{digest}.wav"
    assert stored.sha256 == digest
    assert stored.size == len(data)
    assert stored.content_type == "audio/wav"
    assert store.get(stored.key) == data


def test_the_same_bytes_stored_twice_are_one_object(store, tmp_path):
    data = wav_bytes()
    first = store.put(data, "audio/wav")
    second = store.put(data, "audio/wav")

    assert first.key == second.key
    files = [
        path
        for path in (tmp_path / "objects").rglob("*")
        if path.is_file()
    ]
    assert len(files) == 1, f"expected one object on disk, found {files}"


def test_a_write_leaves_no_half_written_file_behind(store, tmp_path):
    store.put(wav_bytes(0.2), "audio/wav")

    leftovers = [
        path
        for path in (tmp_path / "objects").rglob("*.part")
    ]
    assert leftovers == []
    # The file is written with mode 0600 via mkstemp: customer audio is not for
    # every user on the box.
    for path in (tmp_path / "objects").rglob("*.wav"):
        assert os.stat(path).st_mode & 0o077 == 0


def test_an_object_that_does_not_hash_to_its_own_name_is_never_served(store):
    data = wav_bytes()
    stored = store.put(data, "audio/wav")
    path = store.path_for(stored.key)

    # Corrupt the file the way a bad disk would: same length, different bytes.
    corrupted = bytearray(path.read_bytes())
    corrupted[-1] ^= 0xFF
    path.write_bytes(bytes(corrupted))

    with pytest.raises(StorageError) as error:
        store.get(stored.key)
    assert "does not hash to its own name" in str(error.value)


def test_a_corrupt_object_is_repaired_by_storing_the_right_bytes_again(store):
    data = wav_bytes()
    stored = store.put(data, "audio/wav")
    path = store.path_for(stored.key)
    path.write_bytes(b"not the same bytes at all")

    again = store.put(data, "audio/wav")
    assert again.key == stored.key
    assert store.get(stored.key) == data


@pytest.mark.parametrize(
    "key",
    [
        "",
        "not a key",
        "../escape.wav",
        "/etc/passwd",
        "aa/bb/../../cc.wav",
        "aa/bb/" + "z" * 64 + ".wav",
        "aa/" + "a" * 64 + ".wav",  # missing the second level
        "aa/bb/" + "a" * 63 + ".wav",  # too short
        "aa/bb/" + "a" * 64 + ".exe",  # extension we never write
    ],
)
def test_a_key_that_is_not_a_content_address_is_refused(store, key):
    with pytest.raises(StorageError):
        store.get(key)
    assert store.exists(key) is False
    with pytest.raises(StorageError):
        store.path_for(key)


def test_an_empty_object_is_refused(store):
    with pytest.raises(StorageError):
        store.put(b"", "audio/wav")
    assert list((store.root).rglob("*.*")) == []


def test_a_storage_root_that_is_not_given_is_refused(tmp_path):
    with pytest.raises(StorageError):
        LocalObjectStore("")


def test_the_extension_follows_the_content_type_and_the_type_follows_back():
    assert key_for(b"x", "audio/wav").endswith(".wav")
    assert key_for(b"x", "audio/mpeg; charset=binary").endswith(".mp3")
    assert key_for(b"x", "application/json").endswith(".json")
    assert key_for(b"x", "application/x-nonsense").endswith(".bin")
    assert content_type_for_key("ab/cd/" + "a" * 64 + ".wav") == "audio/wav"
    assert content_type_for_key("ab/cd/" + "a" * 64 + ".mp3") == "audio/mpeg"


# ── the S3 backend, against a stub client ────────────────────────────────────


class StubS3:
    """An in-memory stand-in for boto3's client. Not S3, and named as such."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict] = []
        self.raise_on_head = False
        self.lying_length: int | None = None

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def head_object(self, **kwargs):
        if self.raise_on_head:
            raise RuntimeError("NoSuchKey")
        key = kwargs["Key"]
        if key not in self.objects:
            raise RuntimeError("NoSuchKey")
        length = (
            self.lying_length
            if self.lying_length is not None
            else len(self.objects[key])
        )
        return {"ContentLength": length}

    def get_object(self, **kwargs):
        body = self.objects[kwargs["Key"]]

        class Body:
            def read(self_inner):
                return body

        return {"Body": Body()}


def test_the_s3_backend_without_a_bucket_refuses_instead_of_using_local_disk():
    with pytest.raises(StorageError) as error:
        S3ObjectStore("")
    assert "QUALI_S3_BUCKET" in str(error.value)
    # The point of the refusal: a plane configured for S3 must not quietly write
    # customer audio to the local filesystem.
    assert "fall back" in str(error.value)


def test_build_store_refuses_a_backend_it_does_not_have(tmp_path):
    settings = Settings(database_url="sqlite:///:memory:", upstream_base_url="http://x",
                        storage_backend="gcs", storage_dir=str(tmp_path))
    with pytest.raises(StorageError) as error:
        build_store(settings)
    assert "not a backend" in str(error.value)


def test_build_store_refuses_s3_with_no_bucket_configured(tmp_path):
    settings = Settings(database_url="sqlite:///:memory:", upstream_base_url="http://x",
                        storage_backend="s3", storage_dir=str(tmp_path), s3_bucket="")
    with pytest.raises(StorageError):
        build_store(settings)


def test_the_s3_backend_prefixes_keys_and_verifies_length():
    client = StubS3()
    store = S3ObjectStore("bucket-name", prefix="qualia", client=client)
    data = wav_bytes()
    stored = store.put(data, "audio/wav")

    assert client.puts[0]["Bucket"] == "bucket-name"
    assert client.puts[0]["Key"] == f"qualia/{stored.key}"
    assert client.puts[0]["ContentType"] == "audio/wav"
    assert store.get(stored.key) == data
    assert store.exists(stored.key) is True
    assert store.exists("ab/cd/" + "b" * 64 + ".wav") is False


def test_the_s3_backend_refuses_an_upload_the_service_reports_back_wrong():
    client = StubS3()
    client.lying_length = 12
    store = S3ObjectStore("bucket-name", client=client)

    with pytest.raises(StorageError) as error:
        store.put(wav_bytes(), "audio/wav")
    assert "reports 12 bytes" in str(error.value)


def test_the_s3_backend_never_serves_an_object_that_does_not_match_its_key():
    client = StubS3()
    store = S3ObjectStore("bucket-name", client=client)
    stored = store.put(wav_bytes(), "audio/wav")
    client.objects[f"{stored.key}"] = b"different bytes, same name"

    with pytest.raises(StorageError):
        store.get(stored.key)


def test_the_s3_describe_never_reports_credentials():
    described = S3ObjectStore(
        "bucket", prefix="p", region="ap-south-1", addressing_style="path",
        client=StubS3(),
    ).describe()
    assert described["backend"] == "s3"
    assert described["bucket"] == "bucket"
    assert described["region"] == "ap-south-1"
    assert described["addressing_style"] == "path"
    assert described["endpoint_url_set"] is False
    assert not any("secret" in key or "key" in key for key in described)


def test_build_store_refuses_an_addressing_style_it_does_not_have(tmp_path):
    settings = Settings(
        database_url="sqlite:///:memory:", upstream_base_url="http://x",
        storage_backend="s3", s3_bucket="bucket", storage_dir=str(tmp_path),
        s3_addressing_style="sideways",
    )
    with pytest.raises(StorageError) as error:
        build_store(settings)
    assert "ADDRESSING_STYLE" in str(error.value)


# ── the S3 backend over real HTTP, against a local S3-compatible server ──────


@pytest.fixture(scope="module")
def s3_endpoint():
    """A real S3-compatible server on loopback (moto). Not a bucket, not S3."""
    server_module = pytest.importorskip(
        "moto.server", reason="moto is a test-only dependency and is not installed"
    )
    server = server_module.ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


@pytest.fixture
def s3_settings(s3_endpoint, tmp_path, monkeypatch):
    """Settings that point the real S3 backend at the local emulator."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import boto3
    from botocore.config import Config

    admin = boto3.client(
        "s3", region_name="us-east-1", endpoint_url=s3_endpoint,
        config=Config(s3={"addressing_style": "path"}),
    )
    admin.create_bucket(Bucket="qualiabits-test")
    return Settings(
        database_url="sqlite:///:memory:",
        upstream_base_url="http://engine",
        storage_backend="s3",
        storage_dir=str(tmp_path),
        s3_bucket="qualiabits-test",
        s3_region="us-east-1",
        s3_endpoint_url=s3_endpoint,
        s3_addressing_style="path",
    )


def test_the_s3_backend_stores_reads_and_verifies_over_real_http(s3_settings):
    store = build_store(s3_settings)
    data = wav_bytes(0.3)

    stored = store.put(data, "audio/wav")

    assert stored.key.endswith(".wav")
    assert store.exists(stored.key) is True
    # Read back through the real client: a GET, a signature, and the digest check.
    assert store.get(stored.key) == data
    # The same bytes again are the same object, so no second upload is needed.
    assert store.put(data, "audio/wav").key == stored.key
    described = store.describe()
    assert described["bucket"] == "qualiabits-test"
    assert described["addressing_style"] == "path"
    assert described["endpoint_url_set"] is True


def test_the_s3_prefix_is_part_of_the_stored_key(s3_settings):
    import boto3
    from botocore.config import Config
    from dataclasses import replace

    prefixed = replace(s3_settings, s3_prefix="tenant-a")
    store = build_store(prefixed)
    stored = store.put(wav_bytes(0.2), "audio/wav")

    admin = boto3.client(
        "s3", region_name="us-east-1", endpoint_url=s3_settings.s3_endpoint_url,
        config=Config(s3={"addressing_style": "path"}),
    )
    keys = [row["Key"] for row in admin.list_objects_v2(Bucket="qualiabits-test").get("Contents", [])]
    assert f"tenant-a/{stored.key}" in keys
    assert stored.key not in keys
    assert store.get(stored.key) == store.get(stored.key)


def test_an_object_that_changed_in_the_bucket_is_refused_not_served(s3_settings):
    import boto3
    from botocore.config import Config

    store = build_store(s3_settings)
    stored = store.put(wav_bytes(0.25), "audio/wav")
    admin = boto3.client(
        "s3", region_name="us-east-1", endpoint_url=s3_settings.s3_endpoint_url,
        config=Config(s3={"addressing_style": "path"}),
    )
    admin.put_object(Bucket="qualiabits-test", Key=stored.key, Body=b"something else entirely")

    with pytest.raises(StorageError) as error:
        store.get(stored.key)
    assert "does not hash to its own name" in str(error.value)


def test_a_bucket_that_does_not_exist_is_an_error_not_a_silent_local_write(s3_settings):
    from dataclasses import replace

    missing = replace(s3_settings, s3_bucket="no-such-bucket-here")
    store = build_store(missing)

    with pytest.raises(Exception) as error:  # noqa: B017 - any client error is the point
        store.put(wav_bytes(0.1), "audio/wav")
    assert not isinstance(error.value, StorageError) or "S3" in str(error.value)
    # And nothing landed on the local filesystem behind the operator's back.
    assert list(Path(s3_settings.storage_dir).rglob("*.wav")) == []