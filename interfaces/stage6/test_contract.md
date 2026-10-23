# Stage 6 internal exchange contract

`interfaces/n2_chart.json` is the authority for data direction, connection
initiator, transport and allowed types. `gs_stage6_exchange` contains only
N05 and N08–N18. The Windows CTest compares every compiled route and type
against the chart; Linux CTest also sends one file pair over pinned-key SFTP
on each route. Unknown routes and external ICD paths have no entry.

The route wrapper checks manifest `from`, `to`, type, filename and SHA-256
through `libgs` before upload or after download. The caller supplies the
host, account, remote directory and file-size ceiling from deployment
configuration. The wrapper does not authenticate a caller's service identity
or choose the peer host by itself; separate VM accounts and host policy are
required before operational deployment.

N05 TLE syntax checking accepts exactly two 69-character NORAD lines with
matching 5-digit satellite IDs and modulo-10 checksums. It checks the
expected satellite ID. It does not establish Space-Track provenance,
orbital validity or a current epoch. State Vector contents remain opaque
until an external ICD is supplied.

The Archive catalog admits N09, N14, N15 and N16 only after route, size,
manifest and SHA-256 checks. N09/N14/N15 may be relabelled from Archive to
SDC on N17; N16 may be relabelled from Archive to MPS on N18. Forwarding
rechecks the source hash. The upstream manifest is preserved separately;
the downstream manifest has Archive as sender. The in-memory catalog and
synthetic file bodies are test data, not a durable retention or audit store.

FDS participates at the file boundary: it initiates N05 pull and is
sender/receiver on N08, N11, N13 and N15. A separate offline Java/Orekit
calculation core now produces preliminary Contact Schedule, Pass Information
with Doppler and Orbit Ephemeris bodies plus manifests. It is not yet called
by the N05/N11 exchange receiver or wired to N13/N15 SFTP senders. The
operational GNSS TM decoder, N08 antenna pointing body, source credentials
and mission acceptance rules need ICDs. These Stage 6 tests establish
transport and manifest routing only; a manifest type does not certify the
orbital or mission meaning of the bytes.
