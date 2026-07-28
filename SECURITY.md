# Security And Privacy

Photo Bracket Sorter runs locally. It does not send telemetry and does not require network access for normal operation.

Copy keeps original files untouched. Move deletes source files only after the destination copy is finalized and verified. When SHA-256 verification is enabled, source and destination hashes must match before deletion is attempted.

If a source cannot be deleted, the destination remains and the operation is reported as `DELETE_FAILED`. Review the destination before deleting anything manually.

MTP phone paths are rejected because they are not normal filesystem paths and cannot be safely verified like local files.
