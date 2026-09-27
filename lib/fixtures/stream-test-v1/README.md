# stream-test-v1 fixtures

These synthetic `specstride-test-v1` streams are hand-written for the seam tests
(`lib/test_stream_seam.py`). They model no real harness — the format and its
records exist only to exercise `lib/fixtures/stream_test_adapter.py`. `{EVIDENCE}`
is the evidence-path placeholder a test substitutes with the expected gate path.
None of the streams carry an init record.
