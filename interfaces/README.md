# Interface baseline

`n2_chart.json` records the original proposal's data directions and the approved
network connection directions separately. `approved_flows` is the allowlist for
future account and firewall generation. A `null` connection endpoint or port is
`TBD(ICD)` and must not generate an allow rule.

`command_db/commands.json` and `tm_db/telemetry.json` intentionally contain no
operational entries. TC codes, parameters, TM packet identifiers, encoding, limits
and external frame layout are not yet confirmed. Their schemas define only a
versioned envelope. Before adding entries, the signed ICD and concrete item
schemas must be recorded and the corresponding ROS/MPS validators tested. No
service should treat `operational: false` as a usable database.

`schemas/manifest.schema.json` describes the stage-1 file exchange envelope.
`libgs` adds strict parsing, route/type allowlist checks and SHA-256 verification.
