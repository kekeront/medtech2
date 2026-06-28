"""Parser accuracy evaluation harness.

Pipeline:  reset test DB -> ingest a file through the real parser -> diff the
parser's rows against a Claude-validated golden reference -> score accuracy and
classify mismatches -> report -> reset.

Runs against a *separate* `medarchive_test` database so the working `medarchive`
DB is never touched.
"""
