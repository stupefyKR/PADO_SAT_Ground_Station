# PADO-SAT UHF TC/TM packet proposal (draft v0)

This directory is a **ground-owned application-packet proposal**, not the
flight software/AX100 ICD. `draft_v0.json` assigns candidate IDs and fixed
field layouts to 58 TC names and 16 TM families. Thirty-six TC and all 16 TM
families are representable by the draft codec; the remaining 22 TC entries
have `fields: null` and cannot be encoded. All entries remain non-operational.
The existing `interfaces/command_db/commands.json` and
`interfaces/tm_db/telemetry.json` remain empty and continue to block
operational MPS/ROS use. The existing test signer and simulator frame are
separate protocols. No new route to the antenna or DGS-GS TC gate is opened.

## Document evidence and what we chose

The source PDFs supplied by the user are on the local Desktop and are not
copied into this repository. Page references are PDF page numbers:

| Source | PDF pages | Evidence |
| --- | ---: | --- |
| PDR presentation, SHA-256 `061A47726389B5DD268F5027CFE61AE858588924B10FC17A4B2E918A2CE59AA1` | 65–67 | Immediate/scheduled command, 1 Hz HK polling, mode beacon fields, CSP node assignments |
| PDR presentation | 68–69, 73 | UHF TM/TC/Beacon, AX100 Mode 5 ASM+GOLAY/RS RF framing, FSW command function inventory |
| PDR annex, SHA-256 `D21ED1EE1AA66398C658A4B921214D33D2C4D327CDD6D320334D63CEF035BBED` | 27–30, 47 | CDHS/CS requirements, 1/60 Hz core and 1/600 Hz full HK storage, spacecraft command authentication, node HK field inventory |
| PDR proposal, SHA-256 `A6C44BA5AEC049AC26FDB8E7BD70ADC10453E4BB35A0BE50CC1CA6D1C8889EFF` | 181–184, 194–197 | CSP v1 addressing, proposed 200-byte UHF packet limit, mode beacon RF lengths and downlink context |

`source_inventory.json` transcribes named HK sources. The PDFs **do not**
provide flight command numeric IDs, exact argument encodings, per-sensor
calibration/limit values, OBC authentication scheme, AX100 decoded output
format, or a complete mode-beacon field map. Numeric IDs, binary encodings,
`solution_status=1`, units and CRC below are our proposal. They must be
reviewed with CS/CDHS and then revised with vendor/OBC ICDs. In particular,
the proposal's `<=200 B` CSP packet budget and presentation's `182–196 B`
beacon payload sizes need a joint budget review. `BEACON_CORE` is a compact
application TM proposal; it is **not** the final 182–196 B RF beacon.

S-band payload data reception, CREST science packet decoding, SIREN compressed
science packet decoding, image/model file transfer, and SRS-4 RF framing are
outside this implementation. UHF summary health and SIREN alert metadata are
in scope.

## Proposed application PDU

The application PDU is at most 200 bytes before any CSP/AX100 framing.
Multi-byte integers and IEEE-754 floats are big endian. Field order follows
the JSON `fields` object order. There is no variable-length or implicit
padding field. UTC values are Unix milliseconds. Packet CRC is IEEE CRC-32
over header plus payload; it detects corruption and **is not authentication**.

| Offset | Width | Field |
| ---: | ---: | --- |
| 0 | 2 | ASCII `PS` |
| 2 | 1 | Draft version `0` |
| 3 | 1 | Kind: `1` TC, `2` TM |
| 4 | 2 | Proposed command/TM ID |
| 6 | 2 | Sequence number; rollover policy requires CDHS review |
| 8 | 8 | TC issue time or TM sample time, Unix UTC ms |
| 16 | 2 | Payload length |
| 18 | 1 | Flags: bit 7 = **draft required**; bit 0 = scheduled TC |
| 19 | 1 | Reserved, zero |
| 20 | 0–176 | Payload |
| end−4 | 4 | CRC-32 |

Every TC payload begins with `execute_at_utc_ms` (u64): zero for immediate,
later than issue time for scheduled. Command fields follow. The decoder
rejects non-draft packets, unsupported IDs, reserved flags, wrong lengths,
bad CRC, non-finite floats, and noncanonical booleans. `fields: null` entries
are intentionally unencodable until their argument format and safety rules
are defined. `risk` labels are ground planning hints, not authorization.

The draft does **not** implement spacecraft authentication or authorization.
Before any flight use, CS/CDHS must define the authenticated data range,
key/algorithm and key rotation, replay window and sequence reset rules,
accepted time skew, schedule cancellation/expiry, command ACK codes, and
operator approval policy. These requirements apply even if a test codec
round-trip succeeds.

## Data use

`tools/pado_packet_codec.py` provides `encode_tc`, `encode_tm`, and `decode`
for offline contract testing. Its `gnss_to_fds_properties` converts a decoded
draft `GNSS_PVT` record to the existing `fds.gnss-state.v1` N11 body after
fix/age/orbit checks. It does not ingest vendor OEM719A logs or publish the
N11 manifest. The source documents name ECEF position/velocity; a
quaternion belongs to the separate ADCS attitude packet and does not encode
position. The current adapter expects whole-second UTC because the FDS input
contract does. The `receiver_age_ms <= 1000` and `solution_status=1` rule is
provisional until the OEM719A-to-OBC mapping is verified.

The catalog contains subset health packets sized for UHF and an inventory of
additional vendor fields. `FULL_HK_CHUNK` divides an opaque stored-HK record
into 160-byte pieces, each carried by a 200-byte PDU; a 2,034-byte record uses
13 chunks. The header carries record ID, index/count, total length and
whole-record CRC-32. `split_full_hk` and `join_full_hk` check completeness,
identity, padding and integrity. The contents of the full record and its
request protocol still need the vendor/CDHS ICD before ROS can interpret it.
Exact scaling, physical limits, sensor calibration, and mode beacon content
are CDR/bench-test work. Do not infer these from the Python example bytes.

## Next review with CS/CDHS

1. Confirm actual AX100/CSP byte boundary, 200-byte limit, CSP ports, and
   where the CRC-32 and RF FEC are added or stripped.
2. Approve/reassign TC and TM IDs, field widths, mode values, device/channel
   numbering, fixed-point scaling, timestamp epoch and coordinate frame.
3. Supply OBC command authentication and ACK ICD plus the exact 58-command
   argument/precondition table; enable only reviewed commands.
4. Supply vendor HK/ADCS/GNSS extraction and limit/calibration tables, the
   mode beacon map, and full-HK fragmentation contract.
5. Validate golden vectors and end-to-end ROS↔DGS-GS↔FlatSat traces before
   moving entries into the operational databases.
