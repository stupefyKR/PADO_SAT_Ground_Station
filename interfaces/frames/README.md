# TM/TC frame definitions

The external frame format, port, SCID, synchronization word and CRC are `TBD(ICD)`.
No production frame definition or default command is provided. Stage 3 must use the
simulator contract until the signed external ICD is available.

The isolated Stage 3 development contract is documented in
[simulator.md](simulator.md). Its magic, SCID, payload and CRC values are
synthetic test values and must not be used for an external ground link.
