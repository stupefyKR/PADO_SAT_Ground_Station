# Stage 3 TM simulator contract

This contract is for the local `--sim-only` path. It does not define the
KARI/Yonsei protocol or an operational TM parameter database. The real
external port, frame layout, SCID, synchronization and CRC remain `TBD(ICD)`.

`fake_kari` writes a stream of records to stdout. Each record starts with a
four-byte unsigned big-endian frame length and then the indicated bytes.
`dgs-gs` reads that stream from stdin. The ROS-initiated internal connection
uses the existing length-prefixed TLS 1.3 transport with mutual certificate
authentication and IP SAN validation. The TLS message body starts with `T`
and a validated simulator frame, or contains only `E` at clean end of input.
The IPs, port, certificate paths and local record paths are runtime arguments;
no external link configuration is supplied.

| Offset | Size | Synthetic field |
|---:|---:|---|
| 0 | 8 | ASCII `PSTSIM03` synchronization marker |
| 8 | 1 | Version `1` |
| 9 | 1 | Kind `1` (TM) |
| 10 | 2 | Big-endian SCID `0x7e57` |
| 12 | 2 | Big-endian payload length, 1–256 bytes |
| 14 | 4 | Big-endian sequence number |
| 18 | 1–256 | Opaque test payload; no TM parameter parsing |
| End | 4 | Big-endian CRC-32/IEEE of all preceding frame bytes |

The maximum frame is 278 bytes. DGS-GS rejects a malformed length, marker,
version, kind, SCID or CRC before forwarding. It appends rejected bytes to a
private regular quarantine file with a one-byte reason code and four-byte
big-endian length. ROS validates forwarded frames again and appends raw valid
frames to its private record file with tag `1` and the same length field.
Both services emit JSON audit events. The test fixture deliberately includes
two valid and five invalid records. Quarantine retention and operational TM
storage policy require a later deployment decision.

The temporary file format is a test artifact, not an archive or science data
format. Replacing this simulator requires an approved external ICD and an
operational telemetry database before real downlink processing can be claimed.
